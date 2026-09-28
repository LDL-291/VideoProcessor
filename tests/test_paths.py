from pathlib import Path

from video_batch.core import paths


def test_default_temp_root_is_on_d_drive():
    assert str(paths.DEFAULT_TEMP_ROOT).upper().startswith("D:")


def test_get_temp_root_honors_env_override(tmp_path, monkeypatch):
    override = tmp_path / "custom_temp"
    monkeypatch.setenv(paths.TEMP_ROOT_ENV_VAR, str(override))

    result = paths.get_temp_root()

    assert result == override
    assert override.is_dir()


def test_get_temp_root_creates_default_when_unset(monkeypatch):
    monkeypatch.delenv(paths.TEMP_ROOT_ENV_VAR, raising=False)

    result = paths.get_temp_root()

    assert result == paths.DEFAULT_TEMP_ROOT
    assert Path(result).is_dir()
