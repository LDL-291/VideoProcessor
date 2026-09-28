"""Runs ffmpeg and parses its `-progress pipe:1` output.

Progress parsing (`parse_progress_stream`) is pure and unit-tested separately
from the subprocess plumbing. `start_encode` returns the Popen handle
immediately (non-blocking) so the caller can hold onto it for cancellation
(write "q" to stdin, then terminate the process tree) while a separate
thread drains progress via `stream_progress`.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def parse_progress_stream(lines: Iterable[str], duration_s: float) -> Iterator[float]:
    """Yield fractional progress (0.0-1.0) as ffmpeg -progress key=value lines arrive.

    Issue 11: `out_time_ms` is actually microseconds despite its name, so it is
    ignored in favor of `out_time_us`.
    """
    if duration_s <= 0:
        return

    for line in lines:
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key != "out_time_us":
            continue
        try:
            out_time_us = int(value)
        except ValueError:
            continue
        fraction = (out_time_us / 1_000_000) / duration_s
        yield max(0.0, min(1.0, fraction))


def build_ffmpeg_command(args: list[str], ffmpeg_path: str = "ffmpeg") -> list[str]:
    # -y only ever applies to the .tmp output path the planner targets, never
    # to a final destination gated by the user's overwrite policy.
    return [ffmpeg_path, "-y", "-progress", "pipe:1", "-nostats", "-hide_banner", *args]


def start_encode(args: list[str], ffmpeg_path: str = "ffmpeg") -> subprocess.Popen:
    """Start ffmpeg and return its Popen handle immediately, without waiting.

    The caller can drain progress with `stream_progress(process, duration_s)`
    on a worker thread while keeping this handle on another thread to cancel:
    write "q" to `process.stdin`, then terminate the process tree if it does
    not exit.
    """
    command = build_ffmpeg_command(args, ffmpeg_path)
    kwargs = {}
    if hasattr(subprocess, "STARTUPINFO"):
        kwargs["creationflags"] = CREATE_NO_WINDOW

    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        **kwargs,
    )


def stream_progress(process: subprocess.Popen, duration_s: float) -> Iterator[float]:
    """Drain `process.stdout` and yield progress fractions until ffmpeg exits."""
    if process.stdout is None:
        return
    yield from parse_progress_stream(process.stdout, duration_s)


def finalize_output(tmp_path: Path, final_path: Path) -> None:
    """Atomically promote a successful encode's temp file to its final name."""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_path, final_path)


def discard_temp_output(tmp_path: Path) -> None:
    """Delete a temp file left behind by a cancelled or failed encode."""
    Path(tmp_path).unlink(missing_ok=True)
