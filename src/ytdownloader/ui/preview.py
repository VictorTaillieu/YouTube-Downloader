"""Preview of the analysed video: thumbnail, title, channel and duration."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..ytdlp import VideoInfo, format_eta

_THUMBNAIL_SIZE = QSize(192, 108)
_THUMBNAIL_TIMEOUT_MS = 10_000
_MAX_THUMBNAIL_BYTES = 5_000_000


class VideoPreview(QFrame):
    def __init__(self) -> None:
        super().__init__(frameShape=QFrame.Shape.StyledPanel, visible=False)
        self._network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None

        self.thumbnail = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setFixedSize(_THUMBNAIL_SIZE)
        # Titles come from the web: never let QLabel interpret them as rich text.
        self.title = QLabel(wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.details = QLabel(wordWrap=True, textFormat=Qt.TextFormat.PlainText)

        text = QVBoxLayout()
        text.addWidget(self.title)
        text.addWidget(self.details)
        text.addStretch(1)
        layout = QHBoxLayout(self)
        layout.addWidget(self.thumbnail)
        layout.addLayout(text, 1)

    def show_info(self, info: VideoInfo) -> None:
        self._abort()
        self.thumbnail.clear()
        self.title.setText(info.title)
        parts = [info.uploader] if info.uploader else []
        if info.duration is not None:
            parts.append(format_eta(info.duration))
        self.details.setText(" · ".join(parts))
        self.setVisible(True)
        if info.thumbnail:
            request = QNetworkRequest(QUrl(info.thumbnail))
            request.setTransferTimeout(_THUMBNAIL_TIMEOUT_MS)
            self._reply = self._network.get(request)
            self._reply.finished.connect(partial(self._on_thumbnail, self._reply))

    def reset(self) -> None:
        self._abort()
        self.thumbnail.clear()
        self.setVisible(False)

    def _abort(self) -> None:
        reply, self._reply = self._reply, None
        if reply:
            reply.abort()

    def _on_thumbnail(self, reply: QNetworkReply) -> None:
        reply.deleteLater()
        if reply is not self._reply:
            return
        self._reply = None
        if reply.error() != QNetworkReply.NetworkError.NoError:
            return  # the preview is still useful without its image
        data = reply.readAll()
        pixmap = QPixmap()
        if data.size() > _MAX_THUMBNAIL_BYTES or not pixmap.loadFromData(data):
            return
        ratio = self.devicePixelRatioF()
        scaled = pixmap.scaled(
            _THUMBNAIL_SIZE * ratio,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(ratio)
        self.thumbnail.setPixmap(scaled)
