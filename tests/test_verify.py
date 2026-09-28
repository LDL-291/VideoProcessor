import csv
from pathlib import Path

import pytest

from video_batch.core.probe import MediaInfo
from video_batch.core.verify import (
    QualityScore,
    StructuralResult,
    VerificationResult,
    append_report_row,
    check_output_size,
    check_structural,
    compute_sample_windows,
    decide_retry,
    parse_filters_list,
    parse_ssim_stderr,
    parse_vmaf_log,
    write_report_header,
)


def make_info(**overrides) -> MediaInfo:
    defaults = dict(
        path=Path("clip.mp4"),
        duration_s=60.0,
        video_codec="h264",
        profile="High",
        pix_fmt="yuv420p",
        width=1920,
        height=1080,
        fps=30.0,
        nb_frames=1800,
        color_space="bt709",
        color_primaries="bt709",
        color_transfer="bt709",
        color_range="tv",
        is_vfr=False,
        audio_codecs=["aac"],
        subtitle_count=0,
        container="mp4",
    )
    defaults.update(overrides)
    return MediaInfo(**defaults)


# --------------------------------------------------------------------------
# Structural checks
# --------------------------------------------------------------------------

def test_identical_media_passes_structural_check():
    source = make_info()
    output = make_info()

    result = check_structural(source, output)

    assert result.passed is True
    assert result.issues == []


def test_duration_drift_within_tolerance_passes():
    source = make_info(duration_s=60.0)
    output = make_info(duration_s=60.05)

    result = check_structural(source, output)

    assert result.passed is True


def test_duration_drift_beyond_tolerance_fails():
    source = make_info(duration_s=60.0)
    output = make_info(duration_s=60.5)

    result = check_structural(source, output)

    assert result.passed is False
    assert any("Duration changed" in i for i in result.issues)


def test_resolution_change_fails():
    source = make_info(width=1920, height=1080)
    output = make_info(width=1280, height=720)

    result = check_structural(source, output)

    assert result.passed is False
    assert any("Resolution changed" in i for i in result.issues)


def test_frame_count_within_tolerance_passes():
    source = make_info(nb_frames=1800)
    output = make_info(nb_frames=1801)

    result = check_structural(source, output)

    assert result.passed is True


def test_frame_count_beyond_tolerance_fails():
    source = make_info(nb_frames=1800)
    output = make_info(nb_frames=1750)

    result = check_structural(source, output)

    assert result.passed is False
    assert any("Frame count changed" in i for i in result.issues)


def test_frame_count_falls_back_to_fps_times_duration_when_missing():
    source = make_info(nb_frames=None, fps=30.0, duration_s=60.0)
    output = make_info(nb_frames=None, fps=30.0, duration_s=60.0)

    result = check_structural(source, output)

    assert result.passed is True


def test_missing_video_stream_in_output_fails():
    source = make_info(video_codec="h264")
    output = make_info(video_codec=None)

    result = check_structural(source, output)

    assert result.passed is False
    assert any("Video stream is missing" in i for i in result.issues)


def test_dropped_audio_track_fails():
    source = make_info(audio_codecs=["aac", "aac"])
    output = make_info(audio_codecs=["aac"])

    result = check_structural(source, output)

    assert result.passed is False
    assert any("Audio stream count changed" in i for i in result.issues)


def test_dropped_subtitle_track_fails():
    source = make_info(subtitle_count=2)
    output = make_info(subtitle_count=0)

    result = check_structural(source, output)

    assert result.passed is False
    assert any("Subtitle stream count changed" in i for i in result.issues)


# --------------------------------------------------------------------------
# Sample window selection
# --------------------------------------------------------------------------

def test_short_clip_uses_whole_file():
    assert compute_sample_windows(30.0) is None
    assert compute_sample_windows(60.0) is None


def test_long_clip_uses_five_samples():
    windows = compute_sample_windows(600.0)

    assert windows is not None
    assert len(windows) == 5
    assert all(length == 5.0 for _, length in windows)


def test_long_clip_samples_are_evenly_spaced_and_in_range():
    windows = compute_sample_windows(600.0)

    starts = [start for start, _ in windows]
    assert starts[0] == 0.0
    assert starts == sorted(starts)
    for start, length in windows:
        assert start + length <= 600.0 + 1e-6


def test_sample_windows_never_negative_for_clip_just_over_threshold():
    windows = compute_sample_windows(61.0)

    assert windows is not None
    for start, _ in windows:
        assert start >= 0.0


# --------------------------------------------------------------------------
# Score parsing
# --------------------------------------------------------------------------

def test_parse_vmaf_log_extracts_pooled_mean():
    data = {"pooled_metrics": {"vmaf": {"mean": 96.234, "min": 90.0}}}

    score = parse_vmaf_log(data)

    assert score.vmaf == pytest.approx(96.234)


def test_parse_vmaf_log_missing_pooled_metrics():
    score = parse_vmaf_log({})

    assert score.vmaf is None


def test_parse_ssim_stderr_extracts_all_value():
    text = "[Parsed_ssim_2 @ 0x1234] SSIM Y:0.995 U:0.998 V:0.997 All:0.996123 (24.1)"

    score = parse_ssim_stderr(text)

    assert score.ssim == pytest.approx(0.996123)


def test_parse_ssim_stderr_no_match_returns_empty_score():
    score = parse_ssim_stderr("no ssim info here")

    assert score.ssim is None


def test_parse_filters_list_detects_libvmaf():
    text = (
        " ... aconvert          A->A       Convert the input audio to sample_fmt...\n"
        " ... libvmaf           VV->V      Calculate the VMAF between two video streams.\n"
        " T.. scale             V->V       Scale the input video size and/or convert the pixel format.\n"
    )

    filters = parse_filters_list(text)

    assert "libvmaf" in filters
    assert "scale" in filters


def test_parse_filters_list_absent_when_not_present():
    text = " T.. scale             V->V       Scale the input video size.\n"

    filters = parse_filters_list(text)

    assert "libvmaf" not in filters


def test_vmaf_sample_runs_ffmpeg_with_cwd_and_bare_log_filename(monkeypatch, tmp_path: Path):
    """Regression test: an absolute Windows path (e.g. C:/Users/.../vmaf.json) embedded
    directly in the libvmaf filter option would have its drive-letter colon misparsed
    by ffmpeg's filtergraph parser. _run_vmaf_sample must instead run with cwd set to
    the log's directory and reference only the bare filename in the filter string."""
    from video_batch.core import verify as verify_module

    captured = {}

    def fake_run(args, capture_output, check, cwd=None, **kwargs):
        captured["args"] = args
        captured["cwd"] = cwd
        filter_arg = args[args.index("-filter_complex") + 1]
        log_value = filter_arg.split("log_path=")[1]
        # The log_path filter option value must contain no ':' (which ffmpeg's
        # filtergraph parser would treat as an option separator).
        assert ":" not in log_value
        (Path(cwd) / log_value).write_text(
            '{"pooled_metrics": {"vmaf": {"mean": 97.5}}}', encoding="utf-8"
        )

    monkeypatch.setattr(verify_module.subprocess, "run", fake_run)

    score = verify_module._run_vmaf_sample(
        tmp_path / "source.mov", tmp_path / "output.mp4", None, "ffmpeg"
    )

    assert score.vmaf == pytest.approx(97.5)
    assert captured["cwd"] is not None


# --------------------------------------------------------------------------
# Threshold / pass-fail
# --------------------------------------------------------------------------

def test_quality_score_passes_at_vmaf_threshold():
    assert QualityScore(vmaf=95.0).passed is True
    assert QualityScore(vmaf=94.99).passed is False


def test_quality_score_passes_at_ssim_threshold_when_no_vmaf():
    assert QualityScore(ssim=0.98).passed is True
    assert QualityScore(ssim=0.9799).passed is False


def test_quality_score_with_no_data_fails():
    assert QualityScore().passed is False


def test_verification_result_requires_both_structural_and_quality():
    passing_quality = QualityScore(vmaf=99.0)
    failing_structural = StructuralResult(passed=False, issues=["x"])

    result = VerificationResult(structural=failing_structural, quality=passing_quality)

    assert result.passed is False


# --------------------------------------------------------------------------
# Retry decision
# --------------------------------------------------------------------------

def test_no_retry_when_verification_passed():
    result = VerificationResult(
        structural=StructuralResult(passed=True), quality=QualityScore(vmaf=99.0)
    )

    assert decide_retry(result, attempt=0, current_crf=17) is None


def test_retry_lowers_crf_by_two_on_first_failure():
    result = VerificationResult(
        structural=StructuralResult(passed=True), quality=QualityScore(vmaf=80.0)
    )

    assert decide_retry(result, attempt=0, current_crf=17) == 15


def test_no_retry_after_max_attempts():
    result = VerificationResult(
        structural=StructuralResult(passed=True), quality=QualityScore(vmaf=80.0)
    )

    assert decide_retry(result, attempt=1, current_crf=17) is None


def test_retry_crf_never_goes_below_zero():
    result = VerificationResult(
        structural=StructuralResult(passed=True), quality=QualityScore(vmaf=80.0)
    )

    assert decide_retry(result, attempt=0, current_crf=1) == 0


def test_no_retry_when_crf_unknown_eg_gpu_preset():
    result = VerificationResult(
        structural=StructuralResult(passed=True), quality=QualityScore(vmaf=80.0)
    )

    assert decide_retry(result, attempt=0, current_crf=None) is None


# --------------------------------------------------------------------------
# CSV report
# --------------------------------------------------------------------------

def test_write_report_header_and_append_row(tmp_path: Path):
    csv_path = tmp_path / "report.csv"
    write_report_header(csv_path)

    append_report_row(
        csv_path,
        source=Path("in.mov"),
        output=Path("out.mp4"),
        status="Done",
        quality=QualityScore(vmaf=96.5, ssim=0.991),
        attempts=1,
        issues=[],
    )

    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["source"] == "in.mov"
    assert rows[0]["status"] == "Done"
    assert rows[0]["vmaf"] == "96.50"
    assert rows[0]["ssim"] == "0.9910"


# --------------------------------------------------------------------------
# Output size warning
# --------------------------------------------------------------------------

def test_no_size_warning_when_output_smaller():
    assert check_output_size(1000, 800) is None


def test_no_size_warning_when_output_equal():
    assert check_output_size(1000, 1000) is None


def test_size_warning_when_output_larger():
    warning = check_output_size(1000, 1500)

    assert warning is not None
    assert "1,500" in warning
    assert "1,000" in warning


def test_no_size_warning_when_source_size_unknown():
    assert check_output_size(0, 1500) is None


def test_append_row_handles_missing_quality_and_output(tmp_path: Path):
    csv_path = tmp_path / "report.csv"
    write_report_header(csv_path)

    append_report_row(
        csv_path,
        source=Path("in.mov"),
        output=None,
        status="Quality check failed",
        quality=None,
        attempts=2,
        issues=["Duration changed: 60.0s -> 61.0s"],
    )

    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows[0]["output"] == ""
    assert rows[0]["vmaf"] == ""
    assert rows[0]["issues"].startswith("Duration changed")
