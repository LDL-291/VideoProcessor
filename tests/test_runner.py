import threading
from pathlib import Path

import pytest

from video_batch.core import planner, verify
from video_batch.core.job_queue import Job, JobState
from video_batch.core.probe import MediaInfo
from video_batch.core.runner import RunnerConfig, run_job


def make_info(**overrides) -> MediaInfo:
    defaults = dict(
        path=Path("clip.mov"),
        duration_s=10.0,
        video_codec="prores",
        profile=None,
        pix_fmt="yuv422p10le",
        width=1920,
        height=1080,
        fps=30.0,
        nb_frames=300,
        color_space="bt709",
        color_primaries="bt709",
        color_transfer="bt709",
        color_range="tv",
        is_vfr=False,
        audio_codecs=["pcm_s16le"],
        subtitle_count=0,
        container="mov",
    )
    defaults.update(overrides)
    return MediaInfo(**defaults)


class FakeProcess:
    def __init__(self, returncode=0, progress_lines=None):
        self.returncode = returncode
        self.stdout = progress_lines or []
        self.stdin = _FakeStdin()
        self._polled_done = False

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        pass


class _FakeStdin:
    def write(self, _):
        pass

    def flush(self):
        pass


def make_job(tmp_path: Path) -> tuple[Job, RunnerConfig]:
    source = tmp_path / "in" / "clip.mov"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x")
    out_dir = tmp_path / "out"

    job = Job(id=str(source), source_path=source)
    config = RunnerConfig(output_dir=out_dir, verify_quality=True)
    return job, config


def common_kwargs():
    return dict(
        reserved_paths=set(),
        reserved_paths_lock=threading.Lock(),
        cancel_event=threading.Event(),
    )


def test_successful_encode_and_verify_marks_done(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    source_info = make_info(path=job.source_path)
    output_info = make_info(path=job.source_path, video_codec="h264")

    finalize_calls = []
    monkeypatch.setattr("video_batch.core.runner.encoder.start_encode", lambda args, ffmpeg_path: FakeProcess())
    monkeypatch.setattr(
        "video_batch.core.runner.encoder.finalize_output",
        lambda tmp, final: finalize_calls.append((tmp, final)),
    )
    monkeypatch.setattr(
        "video_batch.core.runner.verify.run_quality_check",
        lambda *a, **k: verify.QualityScore(vmaf=99.0),
    )

    probes = iter([source_info, output_info])
    run_job(job, config, on_progress=lambda f: None, on_log=lambda m: None,
            probe_fn=lambda path, ffprobe_path: next(probes), **common_kwargs())

    assert job.state == JobState.DONE
    assert job.quality.vmaf == 99.0
    assert len(finalize_calls) == 1


def test_stream_copy_skips_quality_check(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    source_info = make_info(path=job.source_path, video_codec="h264", container="mp4")
    output_info = make_info(path=job.source_path, video_codec="h264", container="mp4")

    monkeypatch.setattr("video_batch.core.runner.encoder.start_encode", lambda args, ffmpeg_path: FakeProcess())
    finalize_calls = []
    monkeypatch.setattr(
        "video_batch.core.runner.encoder.finalize_output",
        lambda tmp, final: finalize_calls.append((tmp, final)),
    )
    quality_check_calls = []
    monkeypatch.setattr(
        "video_batch.core.runner.verify.run_quality_check",
        lambda *a, **k: quality_check_calls.append(1) or verify.QualityScore(vmaf=0.0),
    )

    probes = iter([source_info, output_info])
    run_job(job, config, probe_fn=lambda path, ffprobe_path: next(probes), **common_kwargs())

    assert job.state == JobState.DONE
    assert quality_check_calls == []
    assert len(finalize_calls) == 1


def test_structural_failure_marks_failed(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    source_info = make_info(path=job.source_path, width=1920)
    output_info = make_info(path=job.source_path, video_codec="h264", width=1280)

    monkeypatch.setattr("video_batch.core.runner.encoder.start_encode", lambda args, ffmpeg_path: FakeProcess())
    monkeypatch.setattr("video_batch.core.runner.encoder.discard_temp_output", lambda p: None)

    probes = iter([source_info, output_info])
    run_job(job, config, probe_fn=lambda path, ffprobe_path: next(probes), **common_kwargs())

    assert job.state == JobState.FAILED
    assert "Structural verification failed." == job.error


def test_quality_failure_retries_then_succeeds(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    source_info = make_info(path=job.source_path)
    output_info = make_info(path=job.source_path, video_codec="h264")

    monkeypatch.setattr("video_batch.core.runner.encoder.start_encode", lambda args, ffmpeg_path: FakeProcess())
    monkeypatch.setattr("video_batch.core.runner.encoder.discard_temp_output", lambda p: None)
    monkeypatch.setattr("video_batch.core.runner.encoder.finalize_output", lambda tmp, final: None)

    quality_results = iter([verify.QualityScore(vmaf=80.0), verify.QualityScore(vmaf=99.0)])
    monkeypatch.setattr(
        "video_batch.core.runner.verify.run_quality_check",
        lambda *a, **k: next(quality_results),
    )

    probes = iter([source_info, output_info, output_info])
    run_job(job, config, probe_fn=lambda path, ffprobe_path: next(probes), **common_kwargs())

    assert job.state == JobState.DONE
    assert job.attempts == 2


def test_quality_failure_after_retry_marks_quality_check_failed(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    source_info = make_info(path=job.source_path)
    output_info = make_info(path=job.source_path, video_codec="h264")

    monkeypatch.setattr("video_batch.core.runner.encoder.start_encode", lambda args, ffmpeg_path: FakeProcess())
    monkeypatch.setattr("video_batch.core.runner.encoder.discard_temp_output", lambda p: None)
    monkeypatch.setattr(
        "video_batch.core.runner.verify.run_quality_check",
        lambda *a, **k: verify.QualityScore(vmaf=50.0),
    )

    probes = iter([source_info, output_info, output_info])
    run_job(job, config, probe_fn=lambda path, ffprobe_path: next(probes), **common_kwargs())

    assert job.state == JobState.QUALITY_CHECK_FAILED
    assert job.attempts == 2


def test_ffmpeg_nonzero_exit_marks_failed(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    source_info = make_info(path=job.source_path)

    monkeypatch.setattr(
        "video_batch.core.runner.encoder.start_encode",
        lambda args, ffmpeg_path: FakeProcess(returncode=1),
    )

    run_job(job, config, probe_fn=lambda path, ffprobe_path: source_info, **common_kwargs())

    assert job.state == JobState.FAILED
    assert "ffmpeg exited with code 1" in job.error


def test_cancel_during_encode_marks_cancelled(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    source_info = make_info(path=job.source_path, duration_s=10.0)

    process = FakeProcess(progress_lines=["out_time_us=1000000", "out_time_us=2000000"])
    monkeypatch.setattr("video_batch.core.runner.encoder.start_encode", lambda args, ffmpeg_path: process)
    discarded = []
    monkeypatch.setattr("video_batch.core.runner.encoder.discard_temp_output", lambda p: discarded.append(p))

    # Cancellation is requested mid-encode, after the first progress tick has
    # already been drained (not before the job even starts).
    cancel_event = threading.Event()

    def on_progress(fraction):
        cancel_event.set()

    run_job(
        job, config, probe_fn=lambda path, ffprobe_path: source_info,
        reserved_paths=set(), reserved_paths_lock=threading.Lock(), cancel_event=cancel_event,
        on_progress=on_progress,
    )

    assert job.state == JobState.CANCELLED
    assert len(discarded) == 1


def test_skip_action_marks_skipped(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    config.overwrite_policy = planner.OverwritePolicy.SKIP
    source_info = make_info(path=job.source_path, video_codec="h264", container="mp4")

    # Pre-create the final output so the planner's SKIP check triggers.
    final_path = planner.build_plan(source_info, config.preset, config.output_dir).output_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"already there")

    run_job(job, config, probe_fn=lambda path, ffprobe_path: source_info, **common_kwargs())

    assert job.state == JobState.SKIPPED


def test_finalize_warns_when_output_larger_than_source(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    config.verify_quality = False
    source_info = make_info(path=job.source_path)
    output_info = make_info(path=job.source_path, video_codec="h264")

    monkeypatch.setattr("video_batch.core.runner.encoder.start_encode", lambda args, ffmpeg_path: FakeProcess())

    probes = iter([source_info, output_info])
    plan_holder = {}

    def probe_fn(path, ffprobe_path):
        return next(probes)

    # Build the plan the same way run_job will, so we know the tmp path to
    # create real bytes at (finalize needs real files to stat()).
    import video_batch.core.planner as planner_module

    original_build_plan = planner_module.build_plan

    def spying_build_plan(*args, **kwargs):
        plan = original_build_plan(*args, **kwargs)
        plan_holder["plan"] = plan
        if plan.tmp_output_path is not None:
            plan.tmp_output_path.parent.mkdir(parents=True, exist_ok=True)
            plan.tmp_output_path.write_bytes(b"0" * 2000)
        return plan

    monkeypatch.setattr("video_batch.core.runner.planner.build_plan", spying_build_plan)
    job.source_path.write_bytes(b"0" * 500)

    run_job(job, config, probe_fn=probe_fn, **common_kwargs())

    assert job.state == JobState.DONE
    assert any("larger than the source" in w for w in job.warnings)


def test_crf_override_is_used_for_the_initial_encode(tmp_path, monkeypatch):
    job, config = make_job(tmp_path)
    config.crf_override = 22
    source_info = make_info(path=job.source_path)
    output_info = make_info(path=job.source_path, video_codec="h264")

    captured_args = {}

    def fake_start_encode(args, ffmpeg_path):
        captured_args["args"] = args
        return FakeProcess()

    monkeypatch.setattr("video_batch.core.runner.encoder.start_encode", fake_start_encode)
    monkeypatch.setattr("video_batch.core.runner.encoder.finalize_output", lambda tmp, final: None)
    monkeypatch.setattr(
        "video_batch.core.runner.verify.run_quality_check",
        lambda *a, **k: verify.QualityScore(vmaf=99.0),
    )

    probes = iter([source_info, output_info])
    run_job(job, config, probe_fn=lambda path, ffprobe_path: next(probes), **common_kwargs())

    assert job.state == JobState.DONE
    idx = captured_args["args"].index("-crf")
    assert captured_args["args"][idx + 1] == "22"


def test_refuse_action_marks_failed(tmp_path, monkeypatch):
    source = tmp_path / "clip.mov"
    source.write_bytes(b"x")
    job = Job(id=str(source), source_path=source)
    config = RunnerConfig(output_dir=tmp_path)  # same dir as source, no suffix -> refuse
    source_info = make_info(path=source)

    run_job(job, config, probe_fn=lambda path, ffprobe_path: source_info, **common_kwargs())

    assert job.state == JobState.FAILED
    assert job.error
