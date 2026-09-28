"""Settings dialog: preset (with tradeoff text), CRF override, audio mode,
overwrite policy, parallel jobs, verify-quality toggle, output suffix.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from video_batch.core.planner import PRESET_CONFIGS, PRESET_DESCRIPTIONS, OverwritePolicy, Preset
from video_batch.settings import MAX_CRF, MAX_PARALLEL_JOBS, MIN_CRF, MIN_PARALLEL_JOBS, AppSettings

AUDIO_MODE_LABELS = {
    "auto": "Auto (copy when compatible, else AAC 256k)",
    "copy": "Always copy",
    "reencode": "Always re-encode to AAC 256k",
}

OVERWRITE_POLICY_LABELS = {
    OverwritePolicy.SKIP: "Skip existing files",
    OverwritePolicy.RENAME: "Rename to avoid collisions",
    OverwritePolicy.OVERWRITE: "Overwrite existing files",
}


class SettingsPanel(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._build_ui()
        self.set_settings(settings)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        # Qt's QVariant storage unwraps str-subclass enums (Preset/OverwritePolicy
        # are `class X(str, Enum)`) back down to plain str, so userData is
        # stored as the plain .value and converted back to the enum on read.
        self.preset_combo = QComboBox()
        for preset in Preset:
            self.preset_combo.addItem(preset.value, preset.value)
        self.preset_combo.currentIndexChanged.connect(self._update_preset_description)
        form.addRow("Preset", self.preset_combo)

        self.preset_description = QLabel("")
        self.preset_description.setWordWrap(True)
        form.addRow("", self.preset_description)

        self.crf_override_check = QCheckBox("Override CRF (advanced)")
        self.crf_override_check.toggled.connect(self._on_crf_override_toggled)
        form.addRow(self.crf_override_check)

        self.crf_override_spin = QSpinBox()
        self.crf_override_spin.setRange(MIN_CRF, MAX_CRF)
        self.crf_override_spin.setEnabled(False)
        form.addRow("CRF value", self.crf_override_spin)

        self.audio_mode_combo = QComboBox()
        for mode, label in AUDIO_MODE_LABELS.items():
            self.audio_mode_combo.addItem(label, mode)
        form.addRow("Audio", self.audio_mode_combo)

        self.overwrite_policy_combo = QComboBox()
        for policy, label in OVERWRITE_POLICY_LABELS.items():
            self.overwrite_policy_combo.addItem(label, policy.value)
        form.addRow("If output exists", self.overwrite_policy_combo)

        self.parallel_jobs_spin = QSpinBox()
        self.parallel_jobs_spin.setRange(MIN_PARALLEL_JOBS, MAX_PARALLEL_JOBS)
        form.addRow("Parallel jobs", self.parallel_jobs_spin)

        self.verify_quality_check = QCheckBox("Verify output quality (VMAF/SSIM) after encoding")
        form.addRow(self.verify_quality_check)

        self.suffix_edit = QLineEdit()
        self.suffix_edit.setPlaceholderText("e.g. _h264 (required if output folder == source folder)")
        form.addRow("Output filename suffix", self.suffix_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_crf_override_toggled(self, checked: bool) -> None:
        self.crf_override_spin.setEnabled(checked)

    def _update_preset_description(self) -> None:
        preset = Preset(self.preset_combo.currentData())
        self.preset_description.setText(PRESET_DESCRIPTIONS.get(preset, ""))

        # Keep the CRF spin box tracking the selected preset's own CRF while
        # the user hasn't turned on an explicit override yet, so checking
        # "Override CRF" starts from that preset's value instead of an
        # unrelated hardcoded number.
        preset_crf = PRESET_CONFIGS[preset].crf
        if not self.crf_override_check.isChecked() and preset_crf is not None:
            self.crf_override_spin.setValue(preset_crf)

    def set_settings(self, settings: AppSettings) -> None:
        preset_index = self.preset_combo.findData(settings.preset)
        if preset_index >= 0:
            self.preset_combo.setCurrentIndex(preset_index)

        self.crf_override_check.setChecked(settings.crf_override is not None)
        if settings.crf_override is not None:
            self.crf_override_spin.setValue(settings.crf_override)
        self._update_preset_description()

        audio_index = self.audio_mode_combo.findData(settings.audio_mode)
        if audio_index >= 0:
            self.audio_mode_combo.setCurrentIndex(audio_index)

        policy_index = self.overwrite_policy_combo.findData(settings.overwrite_policy)
        if policy_index >= 0:
            self.overwrite_policy_combo.setCurrentIndex(policy_index)

        self.parallel_jobs_spin.setValue(settings.parallel_jobs)
        self.verify_quality_check.setChecked(settings.verify_quality)
        self.suffix_edit.setText(settings.suffix)

    def get_settings(self) -> AppSettings:
        crf_override = self.crf_override_spin.value() if self.crf_override_check.isChecked() else None

        return AppSettings(
            preset=self.preset_combo.currentData(),
            crf_override=crf_override,
            audio_mode=self.audio_mode_combo.currentData(),
            overwrite_policy=self.overwrite_policy_combo.currentData(),
            parallel_jobs=self.parallel_jobs_spin.value(),
            verify_quality=self.verify_quality_check.isChecked(),
            suffix=self.suffix_edit.text(),
        )
