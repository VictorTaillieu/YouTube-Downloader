from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QSettings, Qt, QTimer
from PySide6.QtGui import QGuiApplication

from ytdownloader import tools
from ytdownloader.tools import Tool
from ytdownloader.ui.main_window import MainWindow
from ytdownloader.ui.options_panel import pick_height
from ytdownloader.ytdlp import YOUTUBE_BLOCKED_MESSAGE, Format, SubtitleChoice, VideoInfo


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


def test_subtitle_selection_builds_options(make_window):
    window = make_window(all_tools())
    analysed(window)
    panel = window.options_panel.subtitles
    assert not panel.toggle.isEnabled()
    panel.set_choices(
        [SubtitleChoice("en", "English", False), SubtitleChoice("fr", "French", True)]
    )
    assert panel.toggle.text() == "Sous-titres : aucun"
    assert panel.options().languages == []
    panel.language_list.item(0).setCheckState(Qt.CheckState.Checked)
    assert panel.options().languages == ["en"] and not panel.options().include_auto
    panel.language_list.item(1).setCheckState(Qt.CheckState.Checked)
    assert panel.options().include_auto
    assert panel.toggle.text() == "Sous-titres : en, fr"
    panel.filter_edit.setText("fren")
    assert panel.language_list.item(0).isHidden()
    assert panel.options().languages == ["en", "fr"]  # a filtered-out choice stays selected


def test_translations_are_hidden_until_requested_or_searched(make_window):
    window = make_window(all_tools())
    panel = window.options_panel.subtitles
    panel.set_choices(
        [
            SubtitleChoice("en-orig", "English (Original)", True),
            SubtitleChoice("fr", "French", True, translated=True),
        ]
    )
    french = panel.language_list.item(1)
    assert panel.translations_check.text().endswith("(1)")
    assert french.isHidden()
    panel.translations_check.setChecked(True)
    assert not french.isHidden()
    panel.translations_check.setChecked(False)
    assert french.isHidden()
    panel.filter_edit.setText("french")
    assert not french.isHidden()
    french.setCheckState(Qt.CheckState.Checked)
    panel.filter_edit.clear()
    assert not french.isHidden()  # selected: stays visible
    assert panel.options().languages == ["fr"]


def test_blocked_youtube_shows_clear_message_and_keeps_details_in_log(make_window):
    window = make_window(all_tools())
    window.url_edit.setText("https://youtu.be/abc")
    window._analyze_timer.stop()
    job = object()
    window._info_job = job
    raw = "[youtube] abc: Sign in to confirm you're not a bot."
    window._on_info_failed(job, raw)
    assert window.stage_label.text() == f"Échec de l'analyse : {YOUTUBE_BLOCKED_MESSAGE}"
    assert raw in window.log_view.toPlainText()


def test_new_video_clears_subtitle_selection(make_window):
    window = make_window(all_tools())
    panel = window.options_panel.subtitles
    panel.set_choices([SubtitleChoice("en", "English", False)])
    panel.language_list.item(0).setCheckState(Qt.CheckState.Checked)
    analysed(window)
    assert panel.options().languages == []


def test_pick_height():
    assert pick_height([1080, 720, 480], 720) == 720
    assert pick_height([1080, 480], 720) == 480
    assert pick_height([1080], 720) == 1080
    assert pick_height([1080], 0) is None
