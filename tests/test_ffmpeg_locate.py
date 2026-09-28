from pathlib import Path

from video_batch.core.ffmpeg_locate import default_search_dirs, find_executable

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_prefers_path_when_found():
    result = find_executable("ffmpeg", search_dirs=[], which_fn=lambda name: "C:/PATH/ffmpeg.exe")

    assert result == "C:/PATH/ffmpeg.exe"


def test_falls_back_to_search_dir_when_not_on_path(tmp_path: Path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "ffmpeg.exe").write_bytes(b"x")

    result = find_executable(
        "ffmpeg", search_dirs=[app_dir], which_fn=lambda name: None, is_windows=True
    )

    assert result == str(app_dir / "ffmpeg.exe")


def test_checks_search_dirs_in_order(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "ffmpeg.exe").write_bytes(b"x")  # only present in the second dir

    result = find_executable(
        "ffmpeg", search_dirs=[first, second], which_fn=lambda name: None, is_windows=True
    )

    assert result == str(second / "ffmpeg.exe")


def test_appends_exe_suffix_on_windows(tmp_path: Path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "ffmpeg.exe").write_bytes(b"x")

    result = find_executable(
        "ffmpeg", search_dirs=[app_dir], which_fn=lambda name: None, is_windows=True
    )

    assert result.endswith("ffmpeg.exe")


def test_does_not_append_exe_suffix_off_windows(tmp_path: Path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "ffmpeg").write_bytes(b"x")

    result = find_executable(
        "ffmpeg", search_dirs=[app_dir], which_fn=lambda name: None, is_windows=False
    )

    assert result == str(app_dir / "ffmpeg")


def test_falls_back_to_bare_name_when_nowhere_found(tmp_path: Path):
    result = find_executable(
        "ffmpeg", search_dirs=[tmp_path / "nonexistent"], which_fn=lambda name: None, is_windows=True
    )

    assert result == "ffmpeg"


# --------------------------------------------------------------------------
# default_search_dirs(): where "next to the app" actually resolves to.
# --------------------------------------------------------------------------

def test_dev_mode_search_dir_is_the_real_project_root(monkeypatch):
    monkeypatch.delattr("sys.frozen", raising=False)

    dirs = default_search_dirs()

    assert dirs == [PROJECT_ROOT / "ffmpeg"]
    # Sanity-check this is genuinely the project root, not some other ancestor.
    assert (PROJECT_ROOT / "video_batch.spec").is_file()
    assert (PROJECT_ROOT / "requirements.txt").is_file()


def test_frozen_mode_search_dirs_are_next_to_the_exe(monkeypatch, tmp_path: Path):
    fake_exe = tmp_path / "BatchH264Encoder.exe"
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(fake_exe))

    dirs = default_search_dirs()

    assert dirs == [tmp_path, tmp_path / "ffmpeg"]
