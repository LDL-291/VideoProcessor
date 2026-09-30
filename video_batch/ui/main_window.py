"""Main window: add files/folders, pick an output dir, queue table,
settings/presets, start and cancel.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_batch.core.ffmpeg_locate import resolve_ffmpeg_path, resolve_ffprobe_path
from video_batch.core.job_queue import Job, JobQueue, JobState, TERMINAL_STATES
from video_batch.core.runner import RunnerConfig, run_job
from video_batch.core.scanner import scan_paths
from video_batch.settings import AppSettings, load_settings, save_settings
from video_batch.ui.settings_panel import SettingsPanel

COLUMNS = ["Name", "Source Codec", "Resolution", "Status", "Progress", "Quality", "Warnings"]
REFRESH_INTERVAL_MS = 300


class WorkerSignals(QObject):
    log = Signal(str)
    job_finished = Signal(str)


class JobRunnable(QRunnable):
    def __init__(
        self,
        job: Job,
        config: RunnerConfig,
        reserved_paths: set,
        reserved_paths_lock: threading.Lock,
        cancel_event: threading.Event,
        active_processes: dict,
        signals: WorkerSignals,
    ) -> None:
        super().__init__()
        self.job = job
        self.config = config
        self.reserved_paths = reserved_paths
        self.reserved_paths_lock = reserved_paths_lock
        self.cancel_event = cancel_event
        self.active_processes = active_processes
        self.signals = signals

    def run(self) -> None:
        try:
            run_job(
                self.job,
                self.config,
                reserved_paths=self.reserved_paths,
                reserved_paths_lock=self.reserved_paths_lock,
                cancel_event=self.cancel_event,
                on_log=self.signals.log.emit,
                active_processes=self.active_processes,
            )
        finally:
            self.signals.job_finished.emit(self.job.id)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Batch H.264 Encoder")
        self.resize(1000, 640)

        self.job_queue = JobQueue()
        self.settings: AppSettings = load_settings()
        self.output_dir: Path | None = (
            Path(self.settings.output_dir) if self.settings.output_dir else None
        )
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(self.settings.parallel_jobs)
        self.reserved_paths: set = set()
        self.reserved_paths_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.active_processes: dict = {}
        self.signals = WorkerSignals()
        self.signals.log.connect(self._append_log)
        self.signals.job_finished.connect(self._on_job_finished)
        self._running_count = 0
        self._row_by_job_id: dict[str, int] = {}
        self.source_root: Path | None = None

        # Resolved once at startup (PATH first, then next to the packaged
        # .exe) rather than per job, per VideoPreProcessing.md section 8.
        self.ffmpeg_path = resolve_ffmpeg_path()
        self.ffprobe_path = resolve_ffprobe_path()

        self._build_ui()
        self.setAcceptDrops(True)
        if self.ffmpeg_path == "ffmpeg":
            # resolve_ffmpeg_path() only returns the bare name when it found
            # ffmpeg neither on PATH nor next to the app.
            self._append_log(
                "Warning: ffmpeg was not found on PATH or next to this app. "
                "Encoding will fail until ffmpeg.exe/ffprobe.exe are available."
            )

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(REFRESH_INTERVAL_MS)
        self.refresh_timer.timeout.connect(self._refresh_table)
        self.refresh_timer.start()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        top_bar = QHBoxLayout()
        add_files_btn = QPushButton("Add Files")
        add_files_btn.clicked.connect(self._add_files)
        add_folder_btn = QPushButton("Add Folder")
        add_folder_btn.clicked.connect(self._add_folder)
        self.recursive_check = QCheckBox("Recursive")
        self.recursive_check.setChecked(True)
        self.mirror_check = QCheckBox("Mirror folder structure")
        self.mirror_check.setEnabled(False)
        self.output_dir_label = QLabel(self._output_dir_label_text())
        output_dir_btn = QPushButton("Choose Output Folder")
        output_dir_btn.clicked.connect(self._choose_output_dir)
        self.open_output_btn = QPushButton("Open Output Folder")
        self.open_output_btn.clicked.connect(self._open_output_folder)
        self.open_output_btn.setEnabled(self.output_dir is not None)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self._open_settings)
        top_bar.addWidget(add_files_btn)
        top_bar.addWidget(add_folder_btn)
        top_bar.addWidget(self.recursive_check)
        top_bar.addWidget(self.mirror_check)
        top_bar.addWidget(settings_btn)
        top_bar.addStretch()
        top_bar.addWidget(self.output_dir_label)
        top_bar.addWidget(output_dir_btn)
        top_bar.addWidget(self.open_output_btn)
        layout.addLayout(top_bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)

        self.log_panel = QPlainTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumBlockCount(2000)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.log_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, stretch=1)

        bottom_bar = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setEnabled(False)
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        bottom_bar.addWidget(self.start_btn)
        bottom_bar.addWidget(self.cancel_btn)
        bottom_bar.addWidget(self.overall_progress, stretch=1)
        layout.addLayout(bottom_bar)

    # --------------------------------------------------------------- Adding

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add video files")
        self._add_paths([Path(f) for f in files])

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add folder")
        if folder:
            self._add_paths([Path(folder)])

    def _add_paths(self, paths: list[Path]) -> None:
        if not paths:
            return

        # Remember the most recently added folder as the root for mirroring
        # the input folder structure into the output (section 6 of the plan).
        for path in paths:
            if path.is_dir():
                self.source_root = path
        self.mirror_check.setEnabled(self.source_root is not None)
        if self.source_root is None:
            self.mirror_check.setChecked(False)

        recursive = self.recursive_check.isChecked()
        for video_path in scan_paths(paths, recursive=recursive):
            job = self.job_queue.add(video_path)
            if self._row_for(job.id) is None:
                self._add_row(job)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self._add_paths(paths)
        event.acceptProposedAction()

    def _choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if folder:
            self.output_dir = Path(folder)
            self.settings.output_dir = folder
            save_settings(self.settings)
            self.output_dir_label.setText(self._output_dir_label_text())
            self.open_output_btn.setEnabled(True)

    def _open_output_folder(self) -> None:
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def _output_dir_label_text(self) -> str:
        return f"Output folder: {self.output_dir}" if self.output_dir else "Output folder: (not set)"

    def _open_settings(self) -> None:
        dialog = SettingsPanel(self.settings, self)
        if dialog.exec() == SettingsPanel.Accepted:
            new_settings = dialog.get_settings()
            new_settings.output_dir = self.settings.output_dir  # panel doesn't manage this field
            self.settings = new_settings
            self.thread_pool.setMaxThreadCount(self.settings.parallel_jobs)
            save_settings(self.settings)

    # ----------------------------------------------------------------- Table

    def _add_row(self, job: Job) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(job.source_path.name))
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.setItem(row, 2, QTableWidgetItem(""))
        self.table.setItem(row, 3, QTableWidgetItem(job.state.value))
        self.table.setItem(row, 4, QTableWidgetItem("0%"))
        self.table.setItem(row, 5, QTableWidgetItem(""))
        self.table.setItem(row, 6, QTableWidgetItem(""))
        self.table.item(row, 0).setData(Qt.UserRole, job.id)
        self._row_by_job_id[job.id] = row

    def _row_for(self, job_id: str) -> int | None:
        return self._row_by_job_id.get(job_id)

    def _refresh_table(self) -> None:
        jobs = self.job_queue.jobs()
        for job in jobs:
            row = self._row_for(job.id)
            if row is None:
                continue
            self.table.item(row, 1).setText(job.source_codec)
            self.table.item(row, 2).setText(job.resolution)
            self.table.item(row, 3).setText(job.state.value)
            self.table.item(row, 4).setText(f"{job.progress * 100:.0f}%")
            quality_text = ""
            if job.quality is not None:
                if job.quality.vmaf is not None:
                    quality_text = f"VMAF {job.quality.vmaf:.1f}"
                elif job.quality.ssim is not None:
                    quality_text = f"SSIM {job.quality.ssim:.3f}"
            self.table.item(row, 5).setText(quality_text)
            issues = list(job.warnings)
            if job.error:
                issues.append(job.error)
            self.table.item(row, 6).setText("; ".join(issues))

        if jobs:
            overall = sum(1.0 if j.state in TERMINAL_STATES else j.progress for j in jobs) / len(jobs)
            self.overall_progress.setValue(int(overall * 100))

        if self.job_queue.is_finished() and jobs:
            self.start_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)

    def _append_log(self, message: str) -> None:
        self.log_panel.appendPlainText(message)

    # ------------------------------------------------------------- Start/Cancel

    def _start(self) -> None:
        if self.output_dir is None:
            QMessageBox.warning(self, "No output folder", "Choose an output folder first.")
            return

        pending = self.job_queue.pending_jobs()
        if not pending:
            return

        self.cancel_event.clear()
        config = RunnerConfig(
            output_dir=self.output_dir,
            preset=self.settings.preset_enum(),
            source_root=self.source_root if self.mirror_check.isChecked() else None,
            overwrite_policy=self.settings.overwrite_policy_enum(),
            audio_mode=self.settings.audio_mode,
            verify_quality=self.settings.verify_quality,
            crf_override=self.settings.crf_override,
            suffix=self.settings.suffix,
            crop=self.settings.crop_spec(),
            ffmpeg_path=self.ffmpeg_path,
            ffprobe_path=self.ffprobe_path,
        )

        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._running_count = len(pending)

        for job in pending:
            runnable = JobRunnable(
                job, config, self.reserved_paths, self.reserved_paths_lock,
                self.cancel_event, self.active_processes, self.signals,
            )
            self.thread_pool.start(runnable)

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_btn.setEnabled(False)
        self._append_log("Cancel requested.")

    def _on_job_finished(self, job_id: str) -> None:
        self._running_count = max(0, self._running_count - 1)
        if self._running_count == 0:
            self.start_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.cancel_event.set()
        self.thread_pool.waitForDone(5000)
        super().closeEvent(event)
