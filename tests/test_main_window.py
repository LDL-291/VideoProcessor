import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication

from video_batch.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeDropEvent:
    def __init__(self, mime: QMimeData):
        self._mime = mime
        self.accepted = False

    def mimeData(self) -> QMimeData:
        return self._mime

    def acceptProposedAction(self) -> None:
        self.accepted = True


def make_window(qapp, monkeypatch, config_dir: Path) -> MainWindow:
    monkeypatch.setenv("VIDEO_BATCH_CONFIG_DIR", str(config_dir))
    return MainWindow()


def make_video_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "videos"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "a.mp4").write_bytes(b"x")
    (sub / "b.mp4").write_bytes(b"x")
    (root / "notes.txt").write_bytes(b"x")
    return root, sub


def test_drop_adds_files_recursively_by_default(qapp, monkeypatch, tmp_path):
    window = make_window(qapp, monkeypatch, tmp_path / "cfg")
    root, _sub = make_video_tree(tmp_path)

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(root))])
    event = FakeDropEvent(mime)

    window.dropEvent(event)

    assert window.table.rowCount() == 2  # a.mp4 and sub/b.mp4, not notes.txt
    assert event.accepted is True
    window.close()


def test_drop_respects_recursive_checkbox_off(qapp, monkeypatch, tmp_path):
    window = make_window(qapp, monkeypatch, tmp_path / "cfg")
    root, _sub = make_video_tree(tmp_path)
    window.recursive_check.setChecked(False)

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(root))])
    window.dropEvent(FakeDropEvent(mime))

    assert window.table.rowCount() == 1  # only a.mp4, not sub/b.mp4
    window.close()


def test_dropping_a_folder_sets_source_root_and_enables_mirror(qapp, monkeypatch, tmp_path):
    window = make_window(qapp, monkeypatch, tmp_path / "cfg")
    root, _sub = make_video_tree(tmp_path)

    assert window.mirror_check.isEnabled() is False

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(root))])
    window.dropEvent(FakeDropEvent(mime))

    assert window.source_root == root
    assert window.mirror_check.isEnabled() is True
    window.close()


def test_dropping_only_files_does_not_enable_mirror(qapp, monkeypatch, tmp_path):
    window = make_window(qapp, monkeypatch, tmp_path / "cfg")
    root, _sub = make_video_tree(tmp_path)

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(root / "a.mp4"))])
    window.dropEvent(FakeDropEvent(mime))

    assert window.source_root is None
    assert window.mirror_check.isEnabled() is False
    assert window.mirror_check.isChecked() is False
    window.close()


def test_drop_ignores_non_url_drags(qapp, monkeypatch, tmp_path):
    window = make_window(qapp, monkeypatch, tmp_path / "cfg")

    mime = QMimeData()
    mime.setText("not a file")
    window.dropEvent(FakeDropEvent(mime))

    assert window.table.rowCount() == 0
    window.close()


def test_add_paths_deduplicates_across_calls(qapp, monkeypatch, tmp_path):
    window = make_window(qapp, monkeypatch, tmp_path / "cfg")
    root, _sub = make_video_tree(tmp_path)

    window._add_paths([root])
    window._add_paths([root])

    assert window.table.rowCount() == 2
    window.close()


def test_open_output_folder_button_state(qapp, monkeypatch, tmp_path):
    window = make_window(qapp, monkeypatch, tmp_path / "cfg")

    assert window.open_output_btn.isEnabled() is False

    window.output_dir = tmp_path / "out"
    window.open_output_btn.setEnabled(True)

    assert window.open_output_btn.isEnabled() is True
    window.close()
