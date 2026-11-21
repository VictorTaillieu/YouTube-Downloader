from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtGui import QGuiApplication

from ytdownloader import tools
from ytdownloader.tools import Tool
from ytdownloader.ui.main_window import MainWindow
from ytdownloader.ui.options_panel import pick_height
from ytdownloader.ytdlp import Format, VideoInfo


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


def video_info():
    return VideoInfo(
        id="abc",
        title="<b>Clip</b>",
        uploader="Chaîne",
        duration=75,
        thumbnail=None,
        webpage_url=None,
        formats=[
            Format("137", "mp4", 1080, 30, "avc1.640028", "none", 100_000_000),
            Format("136", "mp4", 720, 30, "avc1.4d401f", "none", 50_000_000),
            Format("140", "m4a", None, None, "none", "mp4a.40.2", 5_000_000),
        ],
        subtitles={},
        auto_subtitles={},
    )


def analysed(window):
    window.url_edit.setText("https://youtu.be/abc")
    window._analyze_timer.stop()
    job = object()
    window._info_job = job
    window._on_info(job, video_info())


def test_download_needs_a_successful_analysis(make_window):
    window = make_window(all_tools())
    assert not window.tools_banner.isVisibleTo(window)
    window.url_edit.setText("https://youtu.be/abc")
    window._analyze_timer.stop()
    assert window.analyze_button.isEnabled()
    assert not window.download_button.isEnabled()
    analysed(window)
    assert window.download_button.isEnabled()
    assert not window.analyze_button.isEnabled()


def test_analysis_fills_preview_and_options(make_window):
    window = make_window(all_tools())
    analysed(window)
    assert window.preview.isVisibleTo(window)
    assert window.preview.title.text() == "<b>Clip</b>"
    assert window.preview.details.text() == "Chaîne · 1:15"
    combo = window.options_panel.height_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["Meilleure", "1080p", "720p"]
    assert window.options_panel.size_label.text() == "Taille estimée : ≈ 105,0 Mo"
    combo.setCurrentIndex(2)
    window._refresh_size()
    assert window.options_panel.size_label.text() == "Taille estimée : ≈ 55,0 Mo"


def test_changing_url_invalidates_analysis(make_window):
    window = make_window(all_tools())
    analysed(window)
    window.url_edit.setText("https://youtu.be/other")
    window._analyze_timer.stop()
    assert not window.preview.isVisibleTo(window)
    assert not window.options_panel.isVisibleTo(window)
    assert not window.download_button.isEnabled()


def test_late_analysis_result_is_ignored(make_window):
    window = make_window(all_tools())
    window.url_edit.setText("https://youtu.be/abc")
    window._analyze_timer.stop()
    window._on_info(object(), video_info())
    assert window._info is None


def test_missing_tool_shows_banner_and_blocks_download(make_window):
    window = make_window({**all_tools(), tools.FFMPEG: None})
    assert window.tools_banner.isVisibleTo(window)
    assert "ffmpeg" in window.tools_label.text()
    analysed(window)
    assert not window.download_button.isEnabled()


def test_clipboard_url_is_pasted_only_if_youtube_and_only_once(make_window):
    QGuiApplication.clipboard().setText("https://example.com/video")
    window = make_window(all_tools())
    assert window.url_edit.text() == ""
    url = "https://www.youtube.com/watch?v=abc"
    QGuiApplication.clipboard().setText(url)
    window._paste_clipboard_url()
    window._analyze_timer.stop()
    assert window.url_edit.text() == url
    window.url_edit.clear()
    window._paste_clipboard_url()
    assert window.url_edit.text() == ""


def test_height_preference_survives_a_lower_video(make_window, tmp_path):
    window = make_window(all_tools())
    analysed(window)
    panel = window.options_panel
    panel.height_combo.setCurrentIndex(1)
    panel.height_combo.activated.emit(1)
    panel.set_heights([480, 360])
    assert panel.height_combo.currentData() == 480
    assert panel._settings.value("max_height", 0, int) == 1080


def test_pick_height():
    assert pick_height([1080, 720, 480], 720) == 720
    assert pick_height([1080, 480], 720) == 480
    assert pick_height([1080], 720) == 1080
    assert pick_height([1080], 0) is None
