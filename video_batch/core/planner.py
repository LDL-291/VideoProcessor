"""MediaInfo + preset -> ffmpeg args + warnings. Pure logic, no I/O.

This module is the "brain" described in VideoPreProcessing.md section 1: it decides
*what* ffmpeg should do and *why*, without ever touching the filesystem or a
subprocess itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from video_batch.core.probe import MediaInfo

MP4_COMPATIBLE_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac"}


class Preset(str, Enum):
    VISUALLY_LOSSLESS = "visually_lossless"
    ARCHIVAL = "archival"
    LOSSLESS = "lossless"
    BALANCED = "balanced"
    WEB_COMPATIBLE = "web_compatible"
    FAST_GPU = "fast_gpu"


class OverwritePolicy(str, Enum):
    SKIP = "skip"
    RENAME = "rename"
    OVERWRITE = "overwrite"


class Action(str, Enum):
    COPY = "copy"          # stream copy or byte copy, no re-encode
    ENCODE = "encode"
    SKIP = "skip"           # e.g. output would collide and policy is SKIP
    REFUSE = "refuse"        # unsafe request (e.g. output dir == source dir)


@dataclass(frozen=True)
class PresetConfig:
    crf: int | None
    x264_preset: str | None
    force_pix_fmt: str | None = None
    force_profile: str | None = None
    use_gpu: bool = False
    gpu_cq: int | None = None
    faststart: bool = False


PRESET_CONFIGS: dict[Preset, PresetConfig] = {
    Preset.VISUALLY_LOSSLESS: PresetConfig(crf=17, x264_preset="slow"),
    Preset.ARCHIVAL: PresetConfig(crf=14, x264_preset="slower"),
    Preset.LOSSLESS: PresetConfig(crf=0, x264_preset="slow"),
    Preset.BALANCED: PresetConfig(crf=20, x264_preset="medium"),
    Preset.WEB_COMPATIBLE: PresetConfig(
        crf=18, x264_preset="slow", force_pix_fmt="yuv420p",
        force_profile="main", faststart=True,
    ),
    Preset.FAST_GPU: PresetConfig(
        crf=None, x264_preset=None, use_gpu=True, gpu_cq=19,
    ),
}

# Plain-language quality tradeoff shown next to each preset in the UI
# (VideoPreProcessing.md section 2: "Show the quality tradeoff ... next to
# the preset").
PRESET_DESCRIPTIONS: dict[Preset, str] = {
    Preset.VISUALLY_LOSSLESS: "No visible quality loss for general use. Larger files, slower encode.",
    Preset.ARCHIVAL: "Highest quality for masters you'll re-edit later. Very large files, slowest encode.",
    Preset.LOSSLESS: "True lossless (CRF 0). Huge files — for intermediates only, not delivery.",
    Preset.BALANCED: "Smaller files, still very good quality. A reasonable everyday default.",
    Preset.WEB_COMPATIBLE: "Maximum player compatibility (8-bit, main profile). Use only when a player requires it.",
    Preset.FAST_GPU: "Fastest (GPU/NVENC). Lower quality per bit than the CPU presets above.",
}


class CropMode(str, Enum):
    NONE = "none"
    MARGINS = "margins"    # trim a fixed number of pixels off each edge
    ASPECT = "aspect"      # largest centered region with a given aspect ratio


@dataclass(frozen=True)
class CropSpec:
    mode: CropMode = CropMode.NONE
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0
    aspect_w: int = 9
    aspect_h: int = 16

    @property
    def active(self) -> bool:
        if self.mode == CropMode.MARGINS:
            return any((self.left, self.top, self.right, self.bottom))
        return self.mode == CropMode.ASPECT


# (width, height, x, y) in source pixels
CropRect = tuple[int, int, int, int]


def compute_crop(info: MediaInfo, spec: CropSpec) -> CropRect | None:
    """Resolve `spec` against one source's dimensions.

    Returns None when no crop applies. Sizes and offsets are rounded to even
    numbers so yuv420 chroma stays aligned. Raises ValueError when the
    request can't be satisfied for this source (e.g. margins exceed its size).
    """
    if not spec.active:
        return None
    if not info.width or not info.height:
        raise ValueError("Source dimensions are unknown; can't crop.")
    src_w, src_h = info.width, info.height

    if spec.mode == CropMode.MARGINS:
        if min(spec.left, spec.top, spec.right, spec.bottom) < 0:
            raise ValueError("Crop margins can't be negative.")
        x = spec.left - spec.left % 2
        y = spec.top - spec.top % 2
        w = src_w - x - spec.right
        h = src_h - y - spec.bottom
        w -= w % 2
        h -= h % 2
    else:
        if spec.aspect_w <= 0 or spec.aspect_h <= 0:
            raise ValueError("Crop aspect ratio must be positive.")
        if src_w * spec.aspect_h > src_h * spec.aspect_w:
            h = src_h
            w = h * spec.aspect_w // spec.aspect_h
        else:
            w = src_w
            h = w * spec.aspect_h // spec.aspect_w
        w -= w % 2
        h -= h % 2
        x = (src_w - w) // 2
        y = (src_h - h) // 2
        x -= x % 2
        y -= y % 2

    if w < 2 or h < 2 or x + w > src_w or y + h > src_h:
        raise ValueError(f"Crop doesn't fit a {src_w}x{src_h} source.")
    if (w, h, x, y) == (src_w, src_h, 0, 0):
        return None
    return w, h, x, y


def crop_filter(rect: CropRect) -> str:
    w, h, x, y = rect
    return f"crop={w}:{h}:{x}:{y}"


@dataclass
class Plan:
    action: Action
    input_path: Path
    output_path: Path | None
    tmp_output_path: Path | None = None
    args: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    crf: int | None = None
    crop_rect: CropRect | None = None


def temp_output_path(final_path: Path) -> Path:
    """The path ffmpeg actually writes to; renamed to `final_path` on success."""
    return final_path.with_name(f"{final_path.stem}.tmp{final_path.suffix}")


def _resolve_output_path(
    info: MediaInfo,
    output_dir: Path,
    source_root: Path | None,
    suffix: str,
    overwrite_policy: OverwritePolicy,
    reserved_paths: set[Path],
) -> Path:
    if source_root is not None:
        try:
            rel = info.path.resolve().relative_to(source_root.resolve())
        except ValueError:
            rel = Path(info.path.name)
    else:
        rel = Path(info.path.name)

    stem = rel.stem + suffix
    candidate = output_dir / rel.parent / f"{stem}.mp4"

    def _collides(p: Path) -> bool:
        return p in reserved_paths or p.exists()

    # Renaming to avoid a collision is driven by the overwrite policy for
    # pre-existing files, but a path already claimed by another plan in this
    # same batch (`reserved_paths`) must always be avoided, regardless of
    # policy, so two sources never race to write the same output.
    if overwrite_policy == OverwritePolicy.RENAME or candidate in reserved_paths:
        n = 1
        while _collides(candidate):
            candidate = output_dir / rel.parent / f"{stem} ({n}).mp4"
            n += 1

    return candidate


def build_plan(
    info: MediaInfo,
    preset: Preset,
    output_dir: Path,
    *,
    source_root: Path | None = None,
    suffix: str = "",
    overwrite_policy: OverwritePolicy = OverwritePolicy.SKIP,
    audio_mode: str = "auto",
    allow_hdr_tonemap: bool = False,
    force_reencode_h264: bool = False,
    reserved_paths: set[Path] | None = None,
    crf_override: int | None = None,
    crop: CropSpec | None = None,
) -> Plan:
    """Build a Plan for one source file.

    `reserved_paths` should be the same set object passed across every
    build_plan call for one batch run; it is mutated to claim each output
    path so two sources in the same batch never get planned to the same
    final path (see _resolve_output_path).

    `crf_override` replaces the preset's CRF (used by the verification
    pipeline's auto-retry at a lower CRF); it has no effect on the GPU preset.

    `crop` forces a re-encode (a stream copy can't crop); the resolved
    rectangle is recorded on the Plan so verification can account for it.
    """
    warnings: list[str] = []
    config = PRESET_CONFIGS[preset]
    if crf_override is not None and not config.use_gpu:
        config = replace(config, crf=crf_override)
    if reserved_paths is None:
        reserved_paths = set()

    # Issue 14: never write into the source file's directory unless a suffix
    # is set, to avoid destroying originals.
    if not suffix and output_dir.resolve() == info.path.parent.resolve():
        return Plan(
            action=Action.REFUSE,
            input_path=info.path,
            output_path=None,
            warnings=["Output directory equals source directory and no suffix is set."],
        )

    try:
        crop_rect = compute_crop(info, crop) if crop is not None else None
    except ValueError as exc:
        return Plan(
            action=Action.REFUSE, input_path=info.path, output_path=None,
            warnings=[str(exc)],
        )

    output_path = _resolve_output_path(
        info, output_dir, source_root, suffix, overwrite_policy, reserved_paths
    )

    if overwrite_policy == OverwritePolicy.SKIP and output_path.exists():
        return Plan(action=Action.SKIP, input_path=info.path, output_path=output_path)

    reserved_paths.add(output_path)
    tmp_path = temp_output_path(output_path)

    # Section 3: already H.264 in an MP4 container -> offer stream copy.
    already_h264_mp4 = (
        info.video_codec == "h264"
        and "mp4" in (info.container or "").lower()
    )
    if already_h264_mp4 and not force_reencode_h264 and crop_rect is None:
        return Plan(
            action=Action.COPY,
            input_path=info.path,
            output_path=output_path,
            tmp_output_path=tmp_path,
            args=_build_copy_args(info, tmp_path),
        )
    if already_h264_mp4 and (force_reencode_h264 or crop_rect is not None):
        warnings.append("Re-encoding an already-H.264 source causes generation loss.")

    # Issue 3: HDR sources need explicit tonemap opt-in; skip by default.
    if info.is_hdr and not allow_hdr_tonemap:
        warnings.append(
            "Source is HDR (wide-gamut/PQ or HLG). Encoding will preserve HDR "
            "metadata as-is; no tonemap was requested."
        )

    args = _build_encode_args(info, config, tmp_path, audio_mode, warnings, crop_rect)

    return Plan(
        action=Action.ENCODE,
        input_path=info.path,
        output_path=output_path,
        tmp_output_path=tmp_path,
        args=args,
        warnings=warnings,
        crf=config.crf if not config.use_gpu else None,
        crop_rect=crop_rect,
    )


def _build_copy_args(info: MediaInfo, tmp_output_path: Path) -> list[str]:
    return [
        "-i", str(info.path),
        "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
        "-map_metadata", "0", "-map_chapters", "0",
        "-c", "copy",
        "-f", "mp4",
        str(tmp_output_path),
    ]


def _build_encode_args(
    info: MediaInfo,
    config: PresetConfig,
    tmp_output_path: Path,
    audio_mode: str,
    warnings: list[str],
    crop_rect: CropRect | None = None,
) -> list[str]:
    args: list[str] = ["-i", str(info.path)]

    # Issue 6: map all streams explicitly instead of relying on defaults.
    args += ["-map", "0:v:0", "-map", "0:a?", "-map", "0:s?"]
    args += ["-map_metadata", "0", "-map_chapters", "0"]

    # Issue 8: variable frame rate -> keep original timestamps.
    if info.is_vfr:
        args += ["-fps_mode", "passthrough"]

    if config.use_gpu:
        args += ["-c:v", "h264_nvenc", "-cq", str(config.gpu_cq), "-preset", "p7", "-tune", "hq"]
    else:
        args += ["-c:v", "libx264", "-crf", str(config.crf), "-preset", config.x264_preset]

    # Issue 7: don't force a profile unless the preset explicitly wants
    # maximum compatibility.
    if config.force_profile:
        args += ["-profile:v", config.force_profile]

    # Issue 2: keep the source pixel format unless the preset forces one.
    pix_fmt = config.force_pix_fmt or info.pix_fmt
    if config.force_pix_fmt and info.pix_fmt and config.force_pix_fmt != info.pix_fmt:
        warnings.append(
            f"Pixel format forced from {info.pix_fmt} to {config.force_pix_fmt} "
            "by preset; bit depth or chroma may be reduced."
        )
    if pix_fmt:
        args += ["-pix_fmt", pix_fmt]

    if crop_rect is not None:
        # compute_crop() always yields even dimensions, so no odd-size pad.
        args += ["-vf", crop_filter(crop_rect)]
        warnings.append(
            f"Cropped {info.width}x{info.height} -> {crop_rect[0]}x{crop_rect[1]}."
        )
    # Issue 9: odd dimensions crash libx264 with yuv420p; pad by 1px.
    elif info.is_odd_dimensions:
        args += ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        warnings.append(
            f"Source has odd dimensions ({info.width}x{info.height}); padded to even."
        )

    # Issue 4: carry color tags through so playback doesn't shift.
    for flag, value in (
        ("-colorspace", info.color_space),
        ("-color_primaries", info.color_primaries),
        ("-color_trc", info.color_transfer),
        ("-color_range", info.color_range),
    ):
        if value:
            args += [flag, value]

    # Issue 5: copy audio when MP4-compatible, else re-encode to AAC.
    args += _build_audio_args(info, audio_mode)

    # Subtitles: use mov_text for MP4 compatibility when subtitles exist.
    if info.subtitle_count:
        args += ["-c:s", "mov_text"]

    args += ["-f", "mp4", str(tmp_output_path)]
    return args


def _build_audio_args(info: MediaInfo, audio_mode: str) -> list[str]:
    if audio_mode == "reencode":
        return ["-c:a", "aac", "-b:a", "256k"]
    if audio_mode == "copy":
        return ["-c:a", "copy"]

    # auto: copy when every audio stream is MP4-compatible, else re-encode.
    if info.audio_codecs and all(c in MP4_COMPATIBLE_AUDIO for c in info.audio_codecs):
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "256k"]
