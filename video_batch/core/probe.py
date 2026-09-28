"""ffprobe wrapper: runs ffprobe and parses its JSON into a MediaInfo dataclass.

Parsing is separated from the subprocess call (`parse_ffprobe_json`) so it can be
unit-tested against fixture JSON without invoking ffprobe.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

HDR_TRANSFER_CHARACTERISTICS = {"smpte2084", "arib-std-b67"}
HDR_COLOR_PRIMARIES = {"bt2020"}


@dataclass
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str


@dataclass
class MediaInfo:
    path: Path
    duration_s: float
    video_codec: str | None
    profile: str | None
    pix_fmt: str | None
    width: int | None
    height: int | None
    fps: float | None
    nb_frames: int | None
    color_space: str | None
    color_primaries: str | None
    color_transfer: str | None
    color_range: str | None
    is_vfr: bool
    audio_codecs: list[str] = field(default_factory=list)
    subtitle_count: int = 0
    container: str = ""
    streams: list[StreamInfo] = field(default_factory=list)

    @property
    def is_hdr(self) -> bool:
        return (
            (self.color_transfer or "").lower() in HDR_TRANSFER_CHARACTERISTICS
            or (self.color_primaries or "").lower() in HDR_COLOR_PRIMARIES
        )

    @property
    def is_odd_dimensions(self) -> bool:
        if self.width is None or self.height is None:
            return False
        return bool(self.width % 2) or bool(self.height % 2)


def _parse_fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            return None
        return num_f / den_f if den_f else None
    try:
        return float(rate)
    except ValueError:
        return None


def parse_ffprobe_json(data: dict, path: Path) -> MediaInfo:
    fmt = data.get("format", {})
    streams_raw = data.get("streams", [])

    video_stream = next((s for s in streams_raw if s.get("codec_type") == "video"), None)
    audio_streams = [s for s in streams_raw if s.get("codec_type") == "audio"]
    subtitle_streams = [s for s in streams_raw if s.get("codec_type") == "subtitle"]

    avg_fps = _parse_fps(video_stream.get("avg_frame_rate")) if video_stream else None
    r_fps = _parse_fps(video_stream.get("r_frame_rate")) if video_stream else None
    # VFR heuristic: average and constant-assumed frame rates disagree noticeably.
    is_vfr = bool(
        avg_fps and r_fps and abs(avg_fps - r_fps) > 0.01 * max(avg_fps, r_fps)
    )

    streams = [
        StreamInfo(
            index=s.get("index", i),
            codec_type=s.get("codec_type", ""),
            codec_name=s.get("codec_name", ""),
        )
        for i, s in enumerate(streams_raw)
    ]

    try:
        duration_s = float(fmt.get("duration", 0.0))
    except (TypeError, ValueError):
        duration_s = 0.0

    nb_frames = None
    if video_stream is not None:
        try:
            nb_frames = int(video_stream.get("nb_frames"))
        except (TypeError, ValueError):
            nb_frames = None

    return MediaInfo(
        path=path,
        duration_s=duration_s,
        video_codec=video_stream.get("codec_name") if video_stream else None,
        profile=video_stream.get("profile") if video_stream else None,
        pix_fmt=video_stream.get("pix_fmt") if video_stream else None,
        width=video_stream.get("width") if video_stream else None,
        height=video_stream.get("height") if video_stream else None,
        fps=avg_fps,
        nb_frames=nb_frames,
        color_space=video_stream.get("color_space") if video_stream else None,
        color_primaries=video_stream.get("color_primaries") if video_stream else None,
        color_transfer=video_stream.get("color_transfer") if video_stream else None,
        color_range=video_stream.get("color_range") if video_stream else None,
        is_vfr=is_vfr,
        audio_codecs=[s.get("codec_name", "") for s in audio_streams],
        subtitle_count=len(subtitle_streams),
        container=fmt.get("format_name", ""),
        streams=streams,
    )


def probe_file(path: Path, ffprobe_path: str = "ffprobe") -> MediaInfo:
    """Run ffprobe on `path` and return its parsed MediaInfo. Raises on failure."""
    args = [
        ffprobe_path,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    kwargs = {}
    if hasattr(subprocess, "STARTUPINFO"):
        kwargs["creationflags"] = CREATE_NO_WINDOW

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        **kwargs,
    )
    data = json.loads(result.stdout)
    return parse_ffprobe_json(data, path)
