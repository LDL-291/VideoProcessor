"""Expand a list of input paths (files and/or folders) into a flat file list."""
from __future__ import annotations

from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm",
    ".wmv", ".flv", ".mts", ".m2ts", ".ts", ".3gp",
}


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def scan_paths(paths: list[Path], recursive: bool = True) -> list[Path]:
    """Expand files and folders into a sorted, de-duplicated list of video files."""
    found: set[Path] = set()

    for raw in paths:
        path = Path(raw)
        if path.is_file():
            if is_video_file(path):
                found.add(path.resolve())
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            for entry in iterator:
                if is_video_file(entry):
                    found.add(entry.resolve())

    return sorted(found)
