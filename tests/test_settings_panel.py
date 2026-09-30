import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from video_batch.core.planner import PRESET_CONFIGS, OverwritePolicy, Preset
from video_batch.settings import AppSettings
from video_batch.ui.settings_panel import SettingsPanel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_set_then_get_settings_roundtrips(qapp):
    original = AppSettings(
        preset=Preset.ARCHIVAL.value,
        crf_override=12,
        audio_mode="copy",
        overwrite_policy=OverwritePolicy.RENAME.value,
        parallel_jobs=3,
        verify_quality=False,
        suffix="_h264",
    )
    panel = SettingsPanel(original)

    result = panel.get_settings()

    assert result.preset == original.preset
    assert result.crf_override == original.crf_override
    assert result.audio_mode == original.audio_mode
    assert result.overwrite_policy == original.overwrite_policy
    assert result.parallel_jobs == original.parallel_jobs
    assert result.verify_quality == original.verify_quality
    assert result.suffix == original.suffix


def test_default_settings_roundtrip(qapp):
    panel = SettingsPanel(AppSettings())

    result = panel.get_settings()

    assert result == AppSettings(output_dir=None)


def test_preset_description_updates_with_selection(qapp):
    panel = SettingsPanel(AppSettings())

    idx = panel.preset_combo.findData(Preset.WEB_COMPATIBLE.value)
    panel.preset_combo.setCurrentIndex(idx)

    assert "compatib" in panel.preset_description.text().lower()


def test_crf_override_disabled_by_default(qapp):
    panel = SettingsPanel(AppSettings())

    assert panel.crf_override_check.isChecked() is False
    assert panel.crf_override_spin.isEnabled() is False
    assert panel.get_settings().crf_override is None


def test_enabling_crf_override_uses_current_preset_crf_as_seed(qapp):
    panel = SettingsPanel(AppSettings(preset=Preset.ARCHIVAL.value))

    assert panel.crf_override_spin.value() == PRESET_CONFIGS[Preset.ARCHIVAL].crf

    panel.crf_override_check.setChecked(True)

    assert panel.get_settings().crf_override == PRESET_CONFIGS[Preset.ARCHIVAL].crf


def test_explicit_crf_override_is_preserved_on_load(qapp):
    panel = SettingsPanel(AppSettings(preset=Preset.ARCHIVAL.value, crf_override=5))

    assert panel.crf_override_spin.value() == 5
    assert panel.get_settings().crf_override == 5


def test_crop_settings_roundtrip_and_enable_state(qapp):
    panel = SettingsPanel(AppSettings())
    assert not panel.crop_aspect_combo.isEnabled()
    assert not panel.crop_margin_spins["left"].isEnabled()

    original = AppSettings(crop_mode="margins", crop_left=12, crop_bottom=40)
    panel.set_settings(original)
    assert panel.crop_margin_spins["left"].isEnabled()
    assert panel.get_settings().crop_left == 12 and panel.get_settings().crop_bottom == 40

    custom = AppSettings(crop_mode="aspect", crop_aspect_w=3, crop_aspect_h=4)
    panel.set_settings(custom)
    assert panel.crop_aspect_w_spin.isEnabled()
    result = panel.get_settings()
    assert (result.crop_mode, result.crop_aspect_w, result.crop_aspect_h) == ("aspect", 3, 4)

    panel.set_settings(AppSettings(crop_mode="aspect", crop_aspect_w=9, crop_aspect_h=16))
    assert not panel.crop_aspect_w_spin.isEnabled()
