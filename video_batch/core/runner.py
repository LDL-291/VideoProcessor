"""Drives a single Job through the state machine, doing the actual I/O:
probe -> plan -> encode -> verify -> (retry once on quality failure) -> finalize.

Every I/O dependency is passed in as a callable with a real default, so tests
can substitute fakes without touching ffmpeg/ffprobe.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from video_batch.core import encoder, planner, verify
from video_batch.core.job_queue import Job, JobState
from video_batch.core.probe import MediaInfo, probe_file

CANCEL_POLL_INTERVAL_S = 0.2


@dataclass
class RunnerConfig:
    output_dir: object
    preset: planner.Preset = planner.Preset.VISUALLY_LOSSLESS
    source_root: object | None = None
    suffix: str = ""
    overwrite_policy: planner.OverwritePolicy = planner.OverwritePolicy.SKIP
    audio_mode: str = "auto"
    verify_quality: bool = True
    crf_override: int | None = None
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"


def run_job(
    job: Job,
    config: RunnerConfig,
    *,
    reserved_paths: set,
    reserved_paths_lock: threading.Lock,
    cancel_event: threading.Event,
    on_progress: Callable[[float], None] = lambda f: None,
    on_log: Callable[[str], None] = lambda msg: None,
    active_processes: dict | None = None,
    probe_fn: Callable[..., MediaInfo] = probe_file,
) -> None:
    """Run `job` to completion, mutating its state/progress/output/quality/warnings."""
    if active_processes is None:
        active_processes = {}

    try:
        job.set_state(JobState.PROBING)
        source_info = probe_fn(job.source_path, config.ffprobe_path)
        job.source_codec = source_info.video_codec or ""
        if source_info.width and source_info.height:
            job.resolution = f"{source_info.width}x{source_info.height}"

        with reserved_paths_lock:
            plan = planner.build_plan(
                source_info,
                config.preset,
                config.output_dir,
                source_root=config.source_root,
                suffix=config.suffix,
                overwrite_policy=config.overwrite_policy,
                audio_mode=config.audio_mode,
                reserved_paths=reserved_paths,
                crf_override=config.crf_override,
            )

        job.warnings = list(plan.warnings)

        if plan.action == planner.Action.SKIP:
            job.output_path = plan.output_path
            job.set_state(JobState.SKIPPED)
            return
        if plan.action == planner.Action.REFUSE:
            job.error = "; ".join(plan.warnings) or "Refused: unsafe output path."
            job.set_state(JobState.FAILED)
            return

        if cancel_event.is_set():
            job.set_state(JobState.CANCELLED)
            return

        _encode_verify_loop(
            job, plan, source_info, config, cancel_event, on_progress, on_log,
            active_processes, probe_fn,
        )

    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not swallowed
        job.error = str(exc)
        if job.state not in (JobState.FAILED, JobState.CANCELLED):
            try:
                job.set_state(JobState.FAILED)
            except ValueError:
                pass


def _encode_verify_loop(
    job: Job,
    plan: planner.Plan,
    source_info: MediaInfo,
    config: RunnerConfig,
    cancel_event: threading.Event,
    on_progress: Callable[[float], None],
    on_log: Callable[[str], None],
    active_processes: dict,
    probe_fn: Callable[..., MediaInfo],
) -> None:
    attempt = 0
    current_plan = plan

    while True:
        job.attempts = attempt + 1
        job.set_state(JobState.ENCODING)

        if plan.action == planner.Action.COPY:
            on_log(f"Stream-copying {job.source_path.name}")
        else:
            on_log(f"Encoding {job.source_path.name} (attempt {attempt + 1}, CRF {current_plan.crf})")

        ok = _run_encode(job, current_plan, source_info.duration_s, config, cancel_event, on_progress, active_processes)
        if not ok:
            return  # state already set to CANCELLED or FAILED

        job.set_state(JobState.VERIFYING)
        output_info = probe_fn(current_plan.tmp_output_path, config.ffprobe_path)
        structural = verify.check_structural(source_info, output_info)

        if not structural.passed:
            job.warnings += structural.issues
            job.error = "Structural verification failed."
            job.set_state(JobState.FAILED)
            return

        # A stream copy is byte-for-byte lossless by definition (section 3 of
        # the plan doc), so there's nothing to score, and a COPY plan has no
        # CRF to retry at if a score ever came back spuriously low.
        if not config.verify_quality or current_plan.action == planner.Action.COPY:
            _finalize(job, current_plan)
            job.set_state(JobState.DONE)
            return

        quality = verify.run_quality_check(
            source_info.path, current_plan.tmp_output_path, source_info.duration_s,
            config.ffmpeg_path,
        )
        job.quality = quality

        result = verify.VerificationResult(structural=structural, quality=quality)
        if result.passed:
            _finalize(job, current_plan)
            job.set_state(JobState.DONE)
            return

        retry_crf = verify.decide_retry(result, attempt, current_plan.crf)
        if retry_crf is None:
            on_log(f"Quality check failed for {job.source_path.name} (VMAF={quality.vmaf})")
            job.set_state(JobState.QUALITY_CHECK_FAILED)
            return

        on_log(f"Quality check failed; retrying at CRF {retry_crf}")
        encoder.discard_temp_output(current_plan.tmp_output_path)
        attempt += 1
        # Re-derived deterministically from the same inputs (info, preset,
        # output_dir, suffix), so this lands on the same output/tmp paths as
        # the first attempt without needing reserved_paths again.
        current_plan = planner.build_plan(
            source_info, config.preset, config.output_dir,
            source_root=config.source_root, suffix=config.suffix,
            overwrite_policy=config.overwrite_policy, audio_mode=config.audio_mode,
            crf_override=retry_crf,
        )


def _run_encode(
    job: Job,
    plan: planner.Plan,
    duration_s: float,
    config: RunnerConfig,
    cancel_event: threading.Event,
    on_progress: Callable[[float], None],
    active_processes: dict,
) -> bool:
    process = encoder.start_encode(plan.args, config.ffmpeg_path)
    active_processes[job.id] = process
    try:
        for fraction in encoder.stream_progress(process, duration_s):
            job.progress = fraction
            on_progress(fraction)
            if cancel_event.is_set():
                _cancel_process(process)
                break

        returncode = process.wait()
    finally:
        active_processes.pop(job.id, None)

    if cancel_event.is_set():
        encoder.discard_temp_output(plan.tmp_output_path)
        job.set_state(JobState.CANCELLED)
        return False

    if returncode != 0:
        job.error = f"ffmpeg exited with code {returncode}."
        job.set_state(JobState.FAILED)
        return False

    return True


def _cancel_process(process) -> None:
    try:
        if process.stdin:
            process.stdin.write("q")
            process.stdin.flush()
    except (OSError, ValueError):
        pass

    deadline = time.monotonic() + 5.0
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(CANCEL_POLL_INTERVAL_S)
    if process.poll() is None:
        process.terminate()


def _finalize(job: Job, plan: planner.Plan) -> None:
    try:
        source_size = plan.input_path.stat().st_size
        output_size = plan.tmp_output_path.stat().st_size
        size_warning = verify.check_output_size(source_size, output_size)
        if size_warning:
            job.warnings.append(size_warning)
    except OSError:
        pass  # best-effort warning; never block finalizing on it

    encoder.finalize_output(plan.tmp_output_path, plan.output_path)
    job.output_path = plan.output_path
