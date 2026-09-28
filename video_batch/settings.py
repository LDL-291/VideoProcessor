"""Persisted app config (JSON), per VideoPreProcessing.md section 6/9.

Serialization (settings_to_dict/settings_from_dict) is pure and unit-tested
without touching disk; load_settings/save_settings do the actual file I/O.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

from video_batch.core.paths import get_config_dir
from video_batch.core.planner import OverwritePolicy, Preset

SETTINGS_FILENAME = "settings.json"

MIN_CRF, MAX_CRF = 0, 51
MIN_PARALLEL_JOBS, MAX_PARALLEL_JOBS = 1, 8
VALID_AUDIO_MODES = {"auto", "copy", "reencode"}


@dataclass
class AppSettings:
    preset: str = Preset.VISUALLY_LOSSLESS.value
    crf_override: int | None = None
    audio_mode: str = "auto"
    overwrite_policy: str = OverwritePolicy.SKIP.value
    parallel_jobs: int = 1
    verify_quality: bool = True
    suffix: str = ""
    output_dir: str | None = None

    def preset_enum(self) -> Preset:
        return Preset(self.preset)

    def overwrite_policy_enum(self) -> OverwritePolicy:
        return OverwritePolicy(self.overwrite_policy)


def settings_to_dict(settings: AppSettings) -> dict:
    return dataclasses.asdict(settings)


def settings_from_dict(data: dict) -> AppSettings:
    """Build AppSettings from a possibly stale/malformed dict.

    Every field falls back to its default individually rather than the whole
    load failing, so a settings.json from an older version (or one a user
    hand-edited badly) degrades gracefully instead of resetting everything.
    """
    if not isinstance(data, dict):
        return AppSettings()

    defaults = AppSettings()
    kwargs: dict = {}

    preset = data.get("preset")
    kwargs["preset"] = preset if _is_valid_preset(preset) else defaults.preset

    overwrite_policy = data.get("overwrite_policy")
    kwargs["overwrite_policy"] = (
        overwrite_policy if _is_valid_overwrite_policy(overwrite_policy) else defaults.overwrite_policy
    )

    audio_mode = data.get("audio_mode")
    kwargs["audio_mode"] = audio_mode if audio_mode in VALID_AUDIO_MODES else defaults.audio_mode

    crf_override = data.get("crf_override")
    kwargs["crf_override"] = crf_override if _is_valid_crf(crf_override) else None

    parallel_jobs = data.get("parallel_jobs")
    kwargs["parallel_jobs"] = (
        parallel_jobs if _is_valid_parallel_jobs(parallel_jobs) else defaults.parallel_jobs
    )

    verify_quality = data.get("verify_quality")
    kwargs["verify_quality"] = verify_quality if isinstance(verify_quality, bool) else defaults.verify_quality

    suffix = data.get("suffix")
    kwargs["suffix"] = suffix if isinstance(suffix, str) else defaults.suffix

    output_dir = data.get("output_dir")
    kwargs["output_dir"] = output_dir if isinstance(output_dir, str) else defaults.output_dir

    return AppSettings(**kwargs)


def _is_valid_preset(value: object) -> bool:
    return isinstance(value, str) and value in {p.value for p in Preset}


def _is_valid_overwrite_policy(value: object) -> bool:
    return isinstance(value, str) and value in {p.value for p in OverwritePolicy}


def _is_valid_crf(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and MIN_CRF <= value <= MAX_CRF


def _is_valid_parallel_jobs(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and MIN_PARALLEL_JOBS <= value <= MAX_PARALLEL_JOBS
    )


def get_settings_path() -> Path:
    return get_config_dir() / SETTINGS_FILENAME


def load_settings(path: Path | None = None) -> AppSettings:
    path = path or get_settings_path()
    if not path.exists():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    return settings_from_dict(data)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    path = path or get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings_to_dict(settings), indent=2), encoding="utf-8")
