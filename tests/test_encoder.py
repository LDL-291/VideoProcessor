from pathlib import Path

from video_batch.core.encoder import (
    build_ffmpeg_command,
    discard_temp_output,
    finalize_output,
    parse_progress_stream,
    stream_progress,
)


def test_progress_uses_out_time_us_not_out_time_ms():
    lines = [
        "frame=100",
        "out_time_ms=999999999",  # if this were used, progress would be wrong
        "out_time_us=30000000",
        "progress=continue",
    ]

    fractions = list(parse_progress_stream(lines, duration_s=60.0))

    assert fractions == [0.5]


def test_progress_clamped_to_one():
    lines = ["out_time_us=120000000"]

    fractions = list(parse_progress_stream(lines, duration_s=60.0))

    assert fractions == [1.0]


def test_zero_duration_yields_nothing():
    lines = ["out_time_us=1000000"]

    fractions = list(parse_progress_stream(lines, duration_s=0.0))

    assert fractions == []


def test_build_ffmpeg_command_has_required_flags():
    command = build_ffmpeg_command(["-i", "in.mov", "out.mp4"])

    assert command[0] == "ffmpeg"
    assert "-progress" in command
    assert "pipe:1" in command
    assert "-nostats" in command
    assert "-hide_banner" in command


class _FakeProcess:
    def __init__(self, stdout):
        self.stdout = stdout


def test_stream_progress_reads_from_process_stdout():
    process = _FakeProcess(stdout=["out_time_us=15000000"])

    fractions = list(stream_progress(process, duration_s=60.0))

    assert fractions == [0.25]


def test_stream_progress_handles_missing_stdout():
    process = _FakeProcess(stdout=None)

    fractions = list(stream_progress(process, duration_s=60.0))

    assert fractions == []


def test_finalize_output_promotes_tmp_to_final(tmp_path: Path):
    tmp_file = tmp_path / "clip.tmp.mp4"
    tmp_file.write_bytes(b"encoded")
    final_file = tmp_path / "clip.mp4"

    finalize_output(tmp_file, final_file)

    assert not tmp_file.exists()
    assert final_file.read_bytes() == b"encoded"


def test_discard_temp_output_removes_file(tmp_path: Path):
    tmp_file = tmp_path / "clip.tmp.mp4"
    tmp_file.write_bytes(b"partial")

    discard_temp_output(tmp_file)

    assert not tmp_file.exists()


def test_discard_temp_output_is_safe_when_missing(tmp_path: Path):
    discard_temp_output(tmp_path / "missing.tmp.mp4")
