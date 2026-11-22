"""Download options: video quality and container, audio format, or transcript."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..ytdlp import AUDIO_FORMATS, VIDEO_CONTAINERS, DownloadOptions, Mode
from .subtitles_panel import SubtitlesPanel

_AUDIO_LABELS = {
    "best": "Original (sans conversion)",
    "mp3": "MP3",
    "m4a": "M4A (AAC)",
    "opus": "Opus",
    "flac": "FLAC",
}


def pick_height(heights: list[int], preferred: int) -> int | None:
    """Height to preselect: the best one not above the preference (0 = best available)."""
    if not preferred or not heights:
        return None
    return next((h for h in heights if h <= preferred), heights[-1])


class OptionsPanel(QWidget):
    changed = Signal()

    def __init__(self, settings: QSettings) -> None:
        super().__init__(visible=False)
        self._settings = settings
        self._preferred_height = settings.value("max_height", 0, int)

        self.video_radio = QRadioButton("Vidéo")
        self.audio_radio = QRadioButton("Audio seul")
        self.transcript_radio = QRadioButton("Transcription")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.video_radio)
        self._mode_group.addButton(self.audio_radio)
        self._mode_group.addButton(self.transcript_radio)
        modes = QHBoxLayout()
        modes.addWidget(self.video_radio)
        modes.addWidget(self.audio_radio)
        modes.addWidget(self.transcript_radio)
        modes.addStretch(1)

        self.height_combo = QComboBox()
        self.container_combo = QComboBox()
        for container in VIDEO_CONTAINERS:
            self.container_combo.addItem(container.upper(), container)
        video_page = QWidget()
        video_form = QFormLayout(video_page)
        video_form.setContentsMargins(0, 0, 0, 0)
        video_form.addRow("Résolution", self.height_combo)
        video_form.addRow("Conteneur", self.container_combo)

        self.audio_format_combo = QComboBox()
        for audio_format in AUDIO_FORMATS:
            self.audio_format_combo.addItem(_AUDIO_LABELS[audio_format], audio_format)
        audio_page = QWidget()
        audio_form = QFormLayout(audio_page)
        audio_form.setContentsMargins(0, 0, 0, 0)
        audio_form.addRow("Format", self.audio_format_combo)

        self.stack = QStackedWidget()
        self.stack.addWidget(video_page)
        self.stack.addWidget(audio_page)
        self.subtitles = SubtitlesPanel(settings)
        self.size_label = QLabel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(modes)
        layout.addWidget(self.stack)
        layout.addWidget(self.subtitles)
        layout.addWidget(self.size_label)

        self._load()
        self._mode_group.buttonToggled.connect(self._on_mode)
        self.subtitles.changed.connect(self.changed)
        self.height_combo.activated.connect(self._on_height_chosen)
        self.container_combo.currentIndexChanged.connect(self._on_choice)
        self.audio_format_combo.currentIndexChanged.connect(self._on_choice)

    @property
    def mode(self) -> Mode:
        if self.transcript_radio.isChecked():
            return "transcript"
        return "audio" if self.audio_radio.isChecked() else "video"

    def _load(self) -> None:
        saved = self._settings.value("mode", "video", str)
        radios = {"audio": self.audio_radio, "transcript": self.transcript_radio}
        radios.get(saved, self.video_radio).setChecked(True)
        self._apply_mode()
        self._select(self.container_combo, self._settings.value("container", "mp4", str))
        self._select(self.audio_format_combo, self._settings.value("audio_format", "mp3", str))

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _save(self) -> None:
        self._settings.setValue("mode", self.mode)
        self._settings.setValue("container", self.container_combo.currentData())
        self._settings.setValue("audio_format", self.audio_format_combo.currentData())

    def _apply_mode(self) -> None:
        mode = self.mode
        self.stack.setCurrentIndex(int(mode == "audio"))
        self.stack.setVisible(mode != "transcript")
        self.size_label.setVisible(mode != "transcript")
        self.subtitles.set_mode(mode)

    def _on_mode(self, _button: QAbstractButton, checked: bool) -> None:
        if checked:  # the group also reports the button being unchecked
            self._apply_mode()
            self._on_choice()

    def _on_height_chosen(self) -> None:
        # Saved only on a user choice: a 480p video must not lower the preference.
        self._preferred_height = self.height_combo.currentData() or 0
        self._settings.setValue("max_height", self._preferred_height)
        self.changed.emit()

    def _on_choice(self) -> None:
        self._save()
        self.changed.emit()

    def set_heights(self, heights: list[int]) -> None:
        self.height_combo.clear()
        self.height_combo.addItem("Meilleure", 0)
        for height in heights:
            self.height_combo.addItem(f"{height}p", height)
        selected = pick_height(heights, self._preferred_height)
        self.height_combo.setCurrentIndex(self.height_combo.findData(selected or 0))

    def build_options(self, url: str, output_dir: Path) -> DownloadOptions:
        return DownloadOptions(
            url=url,
            output_dir=output_dir,
            mode=self.mode,
            max_height=self.height_combo.currentData() or None,
            container=self.container_combo.currentData(),
            audio_format=self.audio_format_combo.currentData(),
            subtitles=self.subtitles.options(),
        )
