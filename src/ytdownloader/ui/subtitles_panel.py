"""Subtitle selection: checkable languages, separate files or embedded in the video."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..ytdlp import SubtitleChoice, SubtitleOptions

_EMBED_KEY = "subtitles_embed"
_LIST_HEIGHT = 130
_ROLE = Qt.ItemDataRole.UserRole


class SubtitlesPanel(QWidget):
    def __init__(self, settings: QSettings) -> None:
        super().__init__()
        self._settings = settings

        self.toggle = QToolButton(
            checkable=True,
            arrowType=Qt.ArrowType.RightArrow,
            toolButtonStyle=Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
            autoRaise=True,
        )
        self.filter_edit = QLineEdit(
            placeholderText="Filtrer les langues…", clearButtonEnabled=True
        )
        self.language_list = QListWidget()
        self.language_list.setFixedHeight(_LIST_HEIGHT)
        self.translations_check = QCheckBox()

        self.separate_radio = QRadioButton("Fichiers .srt séparés")
        self.embed_radio = QRadioButton("Intégrés à la vidéo")
        self._output_group = QButtonGroup(self)
        self._output_group.addButton(self.separate_radio)
        self._output_group.addButton(self.embed_radio)
        embed = settings.value(_EMBED_KEY, False, bool)
        self.embed_radio.setChecked(embed)
        self.separate_radio.setChecked(not embed)
        self.output_row = QWidget()
        output = QHBoxLayout(self.output_row)
        output.setContentsMargins(0, 0, 0, 0)
        output.addWidget(self.separate_radio)
        output.addWidget(self.embed_radio)
        output.addStretch(1)

        self.body = QWidget(visible=False)
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.filter_edit)
        body.addWidget(self.language_list)
        body.addWidget(self.translations_check)
        body.addWidget(self.output_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.body)

        self.toggle.toggled.connect(self._on_toggled)
        self.filter_edit.textChanged.connect(self._refresh_visibility)
        self.translations_check.toggled.connect(self._refresh_visibility)
        self.language_list.itemChanged.connect(self._refresh_summary)
        self.embed_radio.toggled.connect(self._on_embed)
        self.set_choices([])

    def _items(self) -> list[QListWidgetItem]:
        return [self.language_list.item(i) for i in range(self.language_list.count())]

    def _selected(self) -> list[SubtitleChoice]:
        return [
            item.data(_ROLE) for item in self._items() if item.checkState() == Qt.CheckState.Checked
        ]

    def set_choices(self, choices: list[SubtitleChoice]) -> None:
        """Show the languages of a new video; nothing is selected by default."""
        self.language_list.blockSignals(True)
        self.language_list.clear()
        for choice in choices:
            suffix = " · auto" if choice.auto else ""
            item = QListWidgetItem(f"{choice.name} ({choice.code}){suffix}")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(_ROLE, choice)
            self.language_list.addItem(item)
        self.language_list.blockSignals(False)
        translations = sum(choice.translated for choice in choices)
        self.translations_check.setText(f"Afficher les traductions automatiques ({translations})")
        self.translations_check.setVisible(translations > 0)
        self.translations_check.setChecked(False)
        self.filter_edit.clear()
        self.toggle.setChecked(False)
        self.toggle.setEnabled(bool(choices))
        self._refresh_visibility()
        self._refresh_summary()

    def set_audio_mode(self, audio: bool) -> None:
        # Audio files never embed subtitles: they are always written as .srt.
        self.output_row.setVisible(not audio)

    def options(self) -> SubtitleOptions:
        selected = self._selected()
        return SubtitleOptions(
            languages=[choice.code for choice in selected],
            include_auto=any(choice.auto for choice in selected),
            embed=self.embed_radio.isChecked(),
        )

    def _on_toggled(self, shown: bool) -> None:
        self.body.setVisible(shown)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)

    def _on_embed(self, embed: bool) -> None:
        self._settings.setValue(_EMBED_KEY, embed)

    def _refresh_visibility(self) -> None:
        # A search covers every language; a selected translation never disappears from the list.
        needle = self.filter_edit.text().strip().casefold()
        show_translations = self.translations_check.isChecked()
        for item in self._items():
            if needle:
                visible = needle in item.text().casefold()
            else:
                visible = (
                    show_translations
                    or not item.data(_ROLE).translated
                    or item.checkState() == Qt.CheckState.Checked
                )
            item.setHidden(not visible)

    def _refresh_summary(self) -> None:
        if not self.language_list.count():
            self.toggle.setText("Sous-titres : aucun disponible")
            return
        codes = [choice.code for choice in self._selected()]
        self.toggle.setText(f"Sous-titres : {', '.join(codes) if codes else 'aucun'}")
