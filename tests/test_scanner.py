from pathlib import Path

from video_batch.core.scanner import scan_paths


def test_scan_single_file(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")

    result = scan_paths([video])

    assert result == [video.resolve()]


def test_scan_ignores_non_video_files(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hi")
    video = tmp_path / "clip.mkv"
    video.write_bytes(b"data")

    result = scan_paths([tmp_path])

    assert result == [video.resolve()]


def test_scan_recursive_vs_non_recursive(tmp_path: Path):
    nested_dir = tmp_path / "sub"
    nested_dir.mkdir()
    top = tmp_path / "top.mp4"
    top.write_bytes(b"data")
    nested = nested_dir / "nested.mp4"
    nested.write_bytes(b"data")

    recursive_result = scan_paths([tmp_path], recursive=True)
    non_recursive_result = scan_paths([tmp_path], recursive=False)

    assert recursive_result == sorted([top.resolve(), nested.resolve()])
    assert non_recursive_result == [top.resolve()]


def test_scan_deduplicates(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")

    result = scan_paths([video, tmp_path])

    assert result == [video.resolve()]
