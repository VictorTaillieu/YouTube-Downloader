from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtGui import QGuiApplication

from ytdownloader import tools
from ytdownloader.tools import Tool
from ytdownloader.ui.main_window import MainWindow


def wait_for(condition, timeout_ms=5000):
    loop = QEventLoop()
    poll = QTimer(interval=10)
    poll.timeout.connect(lambda: condition() and loop.quit())
    poll.start()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    assert condition()


@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch):
    def factory(found):
        monkeypatch.setattr(tools, "find_all", lambda: found)
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("output_dir", str(tmp_path))
        window = MainWindow(settings)
        wait_for(lambda: window._task is None)
        return window

    QGuiApplication.clipboard().clear()
    return factory


def all_tools():
    return {name: Tool(name, Path(f"/bin/{name}"), "1", True) for name in tools.TOOL_NAMES}


def test_download_needs_url_and_yt_dlp(make_window):
    window = make_window(all_tools())
    assert not window.tools_banner.isVisibleTo(window)
    assert not window.download_button.isEnabled()
    window.url_edit.setText("https://youtu.be/abc")
    assert window.download_button.isEnabled()


def test_missing_tool_shows_banner_and_blocks_download(make_window):
    window = make_window({**all_tools(), tools.YT_DLP: None})
    assert window.tools_banner.isVisibleTo(window)
    assert "yt-dlp" in window.tools_label.text()
    window.url_edit.setText("https://youtu.be/abc")
    assert not window.download_button.isEnabled()


def test_clipboard_url_is_pasted_only_if_youtube(make_window):
    QGuiApplication.clipboard().setText("https://example.com/video")
    window = make_window(all_tools())
    assert window.url_edit.text() == ""
    QGuiApplication.clipboard().setText("https://www.youtube.com/watch?v=abc")
    window._paste_clipboard_url()
    assert window.url_edit.text() == "https://www.youtube.com/watch?v=abc"
