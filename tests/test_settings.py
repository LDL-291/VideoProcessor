from pathlib import Path

from video_batch.core.planner import OverwritePolicy, Preset
from video_batch.settings import (
    AppSettings,
    load_settings,
    save_settings,
    settings_from_dict,
    settings_to_dict,
)


def test_default_settings():
    settings = AppSettings()

    assert settings.preset_enum() == Preset.VISUALLY_LOSSLESS
    assert settings.overwrite_policy_enum() == OverwritePolicy.SKIP
    assert settings.verify_quality is True
    assert settings.parallel_jobs == 1


def test_roundtrip_dict():
    settings = AppSettings(preset=Preset.ARCHIVAL.value, crf_override=12, parallel_jobs=3)

    data = settings_to_dict(settings)
    restored = settings_from_dict(data)

    assert restored == settings


def test_from_dict_falls_back_on_invalid_preset():
    restored = settings_from_dict({"preset": "not_a_real_preset"})

    assert restored.preset == AppSettings().preset


def test_from_dict_falls_back_on_invalid_overwrite_policy():
    restored = settings_from_dict({"overwrite_policy": "delete_everything"})

    assert restored.overwrite_policy == AppSettings().overwrite_policy


def test_from_dict_falls_back_on_invalid_audio_mode():
    restored = settings_from_dict({"audio_mode": "yolo"})

    assert restored.audio_mode == "auto"


def test_from_dict_falls_back_on_out_of_range_crf():
    restored = settings_from_dict({"crf_override": 999})

    assert restored.crf_override is None


def test_from_dict_accepts_valid_crf():
    restored = settings_from_dict({"crf_override": 20})

    assert restored.crf_override == 20


def test_from_dict_rejects_bool_as_crf():
    # bool is a subclass of int in Python; must not silently coerce True/False.
    restored = settings_from_dict({"crf_override": True})

    assert restored.crf_override is None


def test_from_dict_falls_back_on_out_of_range_parallel_jobs():
    restored = settings_from_dict({"parallel_jobs": 0})

    assert restored.parallel_jobs == 1

    restored2 = settings_from_dict({"parallel_jobs": 99})

    assert restored2.parallel_jobs == 1


def test_from_dict_handles_completely_malformed_input():
    restored = settings_from_dict("not a dict")

    assert restored == AppSettings()


def test_from_dict_ignores_unknown_keys():
    restored = settings_from_dict({"preset": Preset.BALANCED.value, "made_up_field": 123})

    assert restored.preset == Preset.BALANCED.value


def test_load_settings_returns_defaults_when_file_missing(tmp_path: Path):
    settings = load_settings(tmp_path / "does_not_exist.json")

    assert settings == AppSettings()


def test_load_settings_returns_defaults_on_corrupt_json(tmp_path: Path):
    bad_file = tmp_path / "settings.json"
    bad_file.write_text("{not valid json", encoding="utf-8")

    settings = load_settings(bad_file)

    assert settings == AppSettings()


def test_save_then_load_roundtrips(tmp_path: Path):
    path = tmp_path / "nested" / "settings.json"
    original = AppSettings(preset=Preset.WEB_COMPATIBLE.value, parallel_jobs=2, verify_quality=False)

    save_settings(original, path)
    loaded = load_settings(path)

    assert loaded == original
