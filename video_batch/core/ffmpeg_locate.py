"""Where to find ffmpeg.exe / ffprobe.exe.

Per VideoPreProcessing.md section 8: locate on PATH first, then next to the
app (the PyInstaller-packaged .exe ships its own ffmpeg build alongside it).
The search itself is a pure function of its inputs so it's unit-testable
without touching the real filesystem or PATH.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

WINDOWS_EXE_SUFFIX = ".exe"


def find_executable(
    name: str,
    search_dirs: list[Path],
    which_fn=shutil.which,
    is_windows: bool = sys.platform.startswith("win"),
) -> str:
    """Resolve `name` (e.g. "ffmpeg") to a runnable path.

    1. PATH, via `which_fn` (shutil.which by default).
    2. Each directory in `search_dirs`, in order (e.g. next to the packaged .exe).
    3. Falls back to the bare `name`, letting the OS/subprocess report "not found"
       with its own clear error rather than this function guessing wrong.
    """
    found = which_fn(name)
    if found:
        return found

    exe_name = f"{name}{WINDOWS_EXE_SUFFIX}" if is_windows and not name.endswith(WINDOWS_EXE_SUFFIX) else name
    for directory in search_dirs:
        candidate = Path(directory) / exe_name
        if candidate.is_file():
            return str(candidate)

    return name


def default_search_dirs() -> list[Path]:
    """Directories to check next to the app, in priority order.

    When PyInstaller-frozen, `sys.executable` is the packaged .exe itself, so
    its directory (and an `ffmpeg/` subfolder in it, where the build script
    places the bundled binaries) is checked. In a normal dev checkout, an
    `ffmpeg/` folder at the project root serves the same purpose for local
    testing without needing ffmpeg on PATH.
    """
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).parent
        return [app_dir, app_dir / "ffmpeg"]

    project_root = Path(__file__).resolve().parents[2]
    return [project_root / "ffmpeg"]


def resolve_ffmpeg_path() -> str:
    return find_executable("ffmpeg", default_search_dirs())


def resolve_ffprobe_path() -> str:
    return find_executable("ffprobe", default_search_dirs())
