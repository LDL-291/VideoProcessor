"""Verification pipeline: structural checks + VMAF/SSIM quality scoring.

Per VideoPreProcessing.md section 4, this runs on the .tmp output before it is
renamed to its final name. Pure decision logic (structural comparison, sample
window selection, score parsing, threshold/retry decisions) is separated from
the ffmpeg/ffprobe subprocess calls so it can be unit-tested without media
files.
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from video_batch.core.paths import get_temp_root
from video_batch.core.probe import MediaInfo

CREATE_NO_WINDOW = 0x08000000

DURATION_TOLERANCE_S = 0.1
FRAME_COUNT_TOLERANCE = 1

VMAF_THRESHOLD = 95.0
SSIM_THRESHOLD = 0.98

# Sampling: whole file for clips at or under this length, otherwise N evenly
# spaced samples of SAMPLE_LEN_S seconds each.
SHORT_CLIP_THRESHOLD_S = 60.0
SAMPLE_COUNT = 5
SAMPLE_LEN_S = 5.0

MAX_AUTO_RETRIES = 1
RETRY_CRF_STEP = 2


@dataclass
class StructuralResult:
    passed: bool
    issues: list[str] = field(default_factory=list)


@dataclass
class QualityScore:
    vmaf: float | None = None
    ssim: float | None = None

    @property
    def passed(self) -> bool:
        if self.vmaf is not None:
            return self.vmaf >= VMAF_THRESHOLD
        if self.ssim is not None:
            return self.ssim >= SSIM_THRESHOLD
        return False


@dataclass
class VerificationResult:
    structural: StructuralResult
    quality: QualityScore | None

    @property
    def passed(self) -> bool:
        return self.structural.passed and self.quality is not None and self.quality.passed


# --------------------------------------------------------------------------
# Structural checks (pure)
# --------------------------------------------------------------------------

def check_structural(source: MediaInfo, output: MediaInfo) -> StructuralResult:
    issues: list[str] = []

    if abs(source.duration_s - output.duration_s) > DURATION_TOLERANCE_S:
        issues.append(
            f"Duration changed: {source.duration_s:.3f}s -> {output.duration_s:.3f}s "
            f"(tolerance {DURATION_TOLERANCE_S}s)."
        )

    if (source.width, source.height) != (output.width, output.height):
        issues.append(
            f"Resolution changed: {source.width}x{source.height} -> "
            f"{output.width}x{output.height}."
        )

    source_frames = _expected_frame_count(source)
    output_frames = _expected_frame_count(output)
    if source_frames is not None and output_frames is not None:
        if abs(source_frames - output_frames) > FRAME_COUNT_TOLERANCE:
            issues.append(
                f"Frame count changed: {source_frames} -> {output_frames} "
                f"(tolerance {FRAME_COUNT_TOLERANCE})."
            )

    if source.video_codec and not output.video_codec:
        issues.append("Video stream is missing from the output.")

    if len(source.audio_codecs) != len(output.audio_codecs):
        issues.append(
            f"Audio stream count changed: {len(source.audio_codecs)} -> "
            f"{len(output.audio_codecs)}."
        )

    if source.subtitle_count != output.subtitle_count:
        issues.append(
            f"Subtitle stream count changed: {source.subtitle_count} -> "
            f"{output.subtitle_count}."
        )

    return StructuralResult(passed=not issues, issues=issues)


def _expected_frame_count(info: MediaInfo) -> int | None:
    if info.nb_frames is not None:
        return info.nb_frames
    if info.fps and info.duration_s:
        return round(info.fps * info.duration_s)
    return None


def check_output_size(source_size_bytes: int, output_size_bytes: int) -> str | None:
    """Preset table rule: warn (don't block) when the output ends up bigger.

    Highly compressed sources can grow when re-encoded at a high-quality CRF.
    """
    if source_size_bytes > 0 and output_size_bytes > source_size_bytes:
        return (
            f"Output is larger than the source ({output_size_bytes:,} > "
            f"{source_size_bytes:,} bytes); consider keeping the original."
        )
    return None


# --------------------------------------------------------------------------
# Sample window selection (pure)
# --------------------------------------------------------------------------

def compute_sample_windows(duration_s: float) -> list[tuple[float, float]] | None:
    """Return [(start, length), ...] sample windows, or None for the whole file."""
    if duration_s <= SHORT_CLIP_THRESHOLD_S:
        return None

    usable = max(duration_s - SAMPLE_LEN_S, 0.0)
    if SAMPLE_COUNT == 1:
        starts = [usable / 2]
    else:
        step = usable / (SAMPLE_COUNT - 1)
        starts = [min(i * step, usable) for i in range(SAMPLE_COUNT)]

    return [(round(start, 3), SAMPLE_LEN_S) for start in starts]


# --------------------------------------------------------------------------
# Quality score parsing (pure)
# --------------------------------------------------------------------------

def parse_vmaf_log(data: dict) -> QualityScore:
    pooled = data.get("pooled_metrics", {})
    vmaf = pooled.get("vmaf", {}).get("mean")
    return QualityScore(vmaf=float(vmaf) if vmaf is not None else None)


_SSIM_ALL_RE = re.compile(r"All:([\d.]+)")


def parse_ssim_stderr(text: str) -> QualityScore:
    """Parse ffmpeg's `-filter:v ssim` stderr summary line, e.g. '... All:0.987654'."""
    matches = _SSIM_ALL_RE.findall(text)
    if not matches:
        return QualityScore()
    return QualityScore(ssim=float(matches[-1]))


def parse_filters_list(text: str) -> set[str]:
    """Parse `ffmpeg -filters` output into a set of filter names."""
    names = set()
    for line in text.splitlines():
        match = re.match(r"\s*[A-Z.]{3}\s+(\w+)\s", line)
        if match:
            names.add(match.group(1))
    return names


# --------------------------------------------------------------------------
# Retry decision (pure)
# --------------------------------------------------------------------------

def decide_retry(result: VerificationResult, attempt: int, current_crf: int | None) -> int | None:
    """Return the CRF to retry at, or None if no retry should happen.

    Issue 13 / section 4.4: on failure, re-encode once with CRF lowered by 2.
    """
    if result.passed:
        return None
    if attempt >= MAX_AUTO_RETRIES:
        return None
    if current_crf is None:
        return None
    return max(0, current_crf - RETRY_CRF_STEP)


# --------------------------------------------------------------------------
# I/O: running ffmpeg/ffprobe for the quality score, and CSV reporting
# --------------------------------------------------------------------------

def _subprocess_kwargs() -> dict:
    kwargs: dict = {}
    if hasattr(subprocess, "STARTUPINFO"):
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return kwargs


def has_libvmaf(ffmpeg_path: str = "ffmpeg") -> bool:
    result = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-filters"],
        capture_output=True, text=True, encoding="utf-8",
        **_subprocess_kwargs(),
    )
    return "libvmaf" in parse_filters_list(result.stdout)


def _trim_args(window: tuple[float, float] | None) -> list[str]:
    if window is None:
        return []
    start, length = window
    return ["-ss", str(start), "-t", str(length)]


def run_quality_check(
    source_path: Path,
    output_path: Path,
    duration_s: float,
    ffmpeg_path: str = "ffmpeg",
    use_vmaf: bool | None = None,
) -> QualityScore:
    """Score `output_path` against `source_path`, sampling per compute_sample_windows."""
    if use_vmaf is None:
        use_vmaf = has_libvmaf(ffmpeg_path)

    windows = compute_sample_windows(duration_s)
    samples = windows or [None]

    scores: list[QualityScore] = []
    for window in samples:
        if use_vmaf:
            scores.append(_run_vmaf_sample(source_path, output_path, window, ffmpeg_path))
        else:
            scores.append(_run_ssim_sample(source_path, output_path, window, ffmpeg_path))

    return _average_scores(scores)


def _average_scores(scores: list[QualityScore]) -> QualityScore:
    vmaf_values = [s.vmaf for s in scores if s.vmaf is not None]
    ssim_values = [s.ssim for s in scores if s.ssim is not None]
    return QualityScore(
        vmaf=sum(vmaf_values) / len(vmaf_values) if vmaf_values else None,
        ssim=sum(ssim_values) / len(ssim_values) if ssim_values else None,
    )


def _run_vmaf_sample(
    source_path: Path, output_path: Path, window: tuple[float, float] | None, ffmpeg_path: str
) -> QualityScore:
    with tempfile.TemporaryDirectory(dir=get_temp_root()) as tmp_dir:
        log_name = "vmaf.json"
        # The libvmaf log_path is a filter *option value*, where ffmpeg's
        # filtergraph parser splits on unescaped ':'. An absolute Windows
        # temp path (e.g. "C:/Users/.../vmaf.json") would have its
        # drive-letter colon misparsed as an option separator, so the ffmpeg
        # process is run with cwd=tmp_dir and only the bare filename is
        # passed into the filter string.
        args = [
            ffmpeg_path, "-hide_banner", "-nostats",
            *_trim_args(window), "-i", str(Path(output_path).resolve()),
            *_trim_args(window), "-i", str(Path(source_path).resolve()),
            "-filter_complex",
            f"[0:v][1:v]libvmaf=log_fmt=json:log_path={log_name}",
            "-f", "null", "-",
        ]
        subprocess.run(args, capture_output=True, check=True, cwd=tmp_dir, **_subprocess_kwargs())
        data = json.loads((Path(tmp_dir) / log_name).read_text(encoding="utf-8"))
        return parse_vmaf_log(data)


def _run_ssim_sample(
    source_path: Path, output_path: Path, window: tuple[float, float] | None, ffmpeg_path: str
) -> QualityScore:
    args = [
        ffmpeg_path, "-hide_banner", "-nostats",
        *_trim_args(window), "-i", str(output_path),
        *_trim_args(window), "-i", str(source_path),
        "-filter_complex", "[0:v][1:v]ssim",
        "-f", "null", "-",
    ]
    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", check=True, **_subprocess_kwargs()
    )
    return parse_ssim_stderr(result.stderr)


def run_verification(
    source: MediaInfo,
    output: MediaInfo,
    ffmpeg_path: str = "ffmpeg",
    use_vmaf: bool | None = None,
) -> VerificationResult:
    structural = check_structural(source, output)
    quality = run_quality_check(source.path, output.path, source.duration_s, ffmpeg_path, use_vmaf)
    return VerificationResult(structural=structural, quality=quality)


# --------------------------------------------------------------------------
# CSV report
# --------------------------------------------------------------------------

REPORT_FIELDNAMES = [
    "source", "output", "status", "vmaf", "ssim", "attempts", "issues",
]


def write_report_header(csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=REPORT_FIELDNAMES).writeheader()


def append_report_row(
    csv_path: Path,
    source: Path,
    output: Path | None,
    status: str,
    quality: QualityScore | None,
    attempts: int,
    issues: list[str],
) -> None:
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDNAMES)
        writer.writerow({
            "source": str(source),
            "output": str(output) if output else "",
            "status": status,
            "vmaf": f"{quality.vmaf:.2f}" if quality and quality.vmaf is not None else "",
            "ssim": f"{quality.ssim:.4f}" if quality and quality.ssim is not None else "",
            "attempts": attempts,
            "issues": "; ".join(issues),
        })
