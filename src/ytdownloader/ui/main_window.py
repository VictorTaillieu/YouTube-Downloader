"""Main window: URL, output folder, progress, cancel, tool management and log."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import tools
from ..jobs import DownloadJob, InfoJob, Reporter, ToolTask
from ..tools import Tool
from ..ytdlp import (
    Progress,
    ToolPaths,
    VideoInfo,
    build_download_args,
    build_info_args,
    estimate_size,
    format_progress,
    format_size,
    friendly_error,
    is_youtube_url,
    subtitle_choices,
)
from .options_panel import OptionsPanel
from .preview import VideoPreview

_OUTPUT_DIR_KEY = "output_dir"
_MAX_LOG_LINES = 2000
_ANALYZE_DELAY_MS = 400


def default_output_dir() -> str:
    location = QStandardPaths.StandardLocation.DownloadLocation
    return QStandardPaths.writableLocation(location) or str(Path.home())


def _install_tools(names: list[str], report: Reporter) -> dict[str, Tool | None]:
    for name in names:

        def on_progress(done: int, total: int | None, tool: str = name) -> None:
            if total:
                report(
                    f"Installation de {tool}… {format_size(done)} / {format_size(total)}",
                    done / total,
                )
            else:
                report(f"Installation de {tool}… {format_size(done)}", None)

        report(f"Installation de {name}…", None)
        tools.install(name, on_progress)
    return tools.find_all()


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self._settings = settings or QSettings()
        self._tools: dict[str, Tool | None] = {}
        self._job: DownloadJob | None = None
        self._task: ToolTask | None = None
        self._last_dir: Path | None = None
        self._info: VideoInfo | None = None
        self._info_job: InfoJob | None = None
        self._pasted_url = ""
        self._task_ok = False
        self._analyze_timer = QTimer(self, singleShot=True, interval=_ANALYZE_DELAY_MS)

        self.setWindowTitle("YouTube Downloader")
        self.resize(720, 560)
        self._build_ui()
        self._connect()
        self._update_controls()

        self._run_task(lambda _report: tools.find_all(), "Recherche des outils…")
        self._paste_clipboard_url()

    # ------------------------------------------------------------------ construction

    def _build_ui(self) -> None:
        self.tools_banner = QFrame(frameShape=QFrame.Shape.StyledPanel, visible=False)
        self.tools_label = QLabel(wordWrap=True)
        self.install_button = QPushButton("Installer")
        banner = QHBoxLayout(self.tools_banner)
        banner.addWidget(self.tools_label, 1)
        banner.addWidget(self.install_button)

        self.url_edit = QLineEdit(placeholderText="https://www.youtube.com/watch?v=…")
        self.analyze_button = QPushButton("Analyser")
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL"))
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(self.analyze_button)

        self.preview = VideoPreview()
        self.options_panel = OptionsPanel(self._settings)

        saved = self._settings.value(_OUTPUT_DIR_KEY, "", str)
        self.dir_edit = QLineEdit(saved or default_output_dir(), readOnly=True)
        self.browse_button = QPushButton("Parcourir…")
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Dossier"))
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(self.browse_button)

        # Error messages can quote web content: keep them as plain text.
        self.stage_label = QLabel("Prêt.", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        self.progress_bar = QProgressBar(minimum=0, maximum=1000, textVisible=False)
        self.status_label = QLabel()

        self.download_button = QPushButton("Télécharger", default=True)
        self.cancel_button = QPushButton("Annuler")
        self.open_button = QPushButton("Ouvrir le dossier")
        self.update_button = QPushButton("Mettre à jour yt-dlp")
        actions = QHBoxLayout()
        actions.addWidget(self.download_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.open_button)
        actions.addStretch(1)
        actions.addWidget(self.update_button)

        self.log_toggle = QToolButton(
            text="Journal",
            checkable=True,
            arrowType=Qt.ArrowType.RightArrow,
            toolButtonStyle=Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
            autoRaise=True,
        )
        self.log_view = QPlainTextEdit(readOnly=True, visible=False)
        self.log_view.setMaximumBlockCount(_MAX_LOG_LINES)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.tools_banner)
        layout.addLayout(url_row)
        layout.addLayout(dir_row)
        layout.addWidget(self.preview)
        layout.addWidget(self.options_panel)
        layout.addSpacing(8)
        layout.addWidget(self.stage_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addLayout(actions)
        layout.addWidget(self.log_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.log_view, 1)
        layout.addStretch(0)
        self.setCentralWidget(root)

    def _connect(self) -> None:
        self.url_edit.textChanged.connect(self._on_url_changed)
        self.url_edit.returnPressed.connect(self._on_return)
        self.analyze_button.clicked.connect(self.analyze)
        self._analyze_timer.timeout.connect(self.analyze)
        self.options_panel.changed.connect(self._refresh_size)
        self.download_button.clicked.connect(self.start_download)
        self.browse_button.clicked.connect(self._browse)
        self.cancel_button.clicked.connect(self.cancel_download)
        self.open_button.clicked.connect(self._open_folder)
        self.update_button.clicked.connect(self._update_yt_dlp)
        self.install_button.clicked.connect(self._install_missing)
        self.log_toggle.toggled.connect(self._toggle_log)
        app = QGuiApplication.instance()
        app.applicationStateChanged.connect(self._on_app_state)

    # ------------------------------------------------------------------ state

    @property
    def _busy(self) -> bool:
        return self._job is not None or self._task is not None or self._info_job is not None

    @property
    def _can_analyze(self) -> bool:
        return (
            not self._busy
            and self._info is None
            and self._tools.get(tools.YT_DLP) is not None
            and bool(self.url_edit.text().strip())
        )

    def _update_controls(self) -> None:
        has_tools = all(self._tools.get(name) is not None for name in (tools.YT_DLP, tools.FFMPEG))
        self.analyze_button.setEnabled(self._can_analyze)
        self.download_button.setEnabled(not self._busy and has_tools and self._info is not None)
        self.options_panel.setEnabled(self._job is None)
        self.cancel_button.setEnabled(self._job is not None)
        self.url_edit.setEnabled(self._job is None)
        self.browse_button.setEnabled(self._job is None)
        self.update_button.setEnabled(not self._busy)
        self.install_button.setEnabled(not self._busy)
        self.open_button.setEnabled(self._last_dir is not None)

    def _set_bar(self, fraction: float | None) -> None:
        if fraction is None:
            self.progress_bar.setRange(0, 0)  # indeterminate
        else:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(round(fraction * 1000))

    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _toggle_log(self, shown: bool) -> None:
        self.log_view.setVisible(shown)
        arrow = Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow
        self.log_toggle.setArrowType(arrow)

    def _refresh_tools_banner(self) -> None:
        missing = [name for name in tools.TOOL_NAMES if self._tools.get(name) is None]
        self.tools_banner.setVisible(bool(missing))
        if missing:
            lines = [f"<b>{name}</b> manquant. {tools.missing_tool_hint(name)}" for name in missing]
            self.tools_label.setText("<br>".join(lines))

    # ------------------------------------------------------------------ clipboard & folder

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self._paste_clipboard_url()

    def _paste_clipboard_url(self) -> None:
        if self._job is not None or self.url_edit.text().strip():
            return
        text = QGuiApplication.clipboard().text().strip()
        # Do not bring back a URL that was already pasted once (e.g. after its download).
        if is_youtube_url(text) and text != self._pasted_url:
            self._pasted_url = text
            self.url_edit.setText(text)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Dossier de sortie", self.dir_edit.text())
        if chosen:
            self.dir_edit.setText(chosen)
            self._settings.setValue(_OUTPUT_DIR_KEY, chosen)

    def _open_folder(self) -> None:
        if self._last_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_dir)))

    # ------------------------------------------------------------------ analysis

    def _tool_paths(self) -> ToolPaths:
        ffmpeg, deno = self._tools.get(tools.FFMPEG), self._tools.get(tools.DENO)
        return ToolPaths(ffmpeg=ffmpeg.path if ffmpeg else None, deno=deno.path if deno else None)

    def _on_url_changed(self) -> None:
        self._analyze_timer.stop()
        self._reset_analysis()
        self._update_controls()
        if is_youtube_url(self.url_edit.text()):
            self._analyze_timer.start()

    def _reset_analysis(self) -> None:
        job, self._info_job = self._info_job, None
        if job:
            job.cancel()  # its late signals are ignored by _on_info / _on_info_failed
            self.stage_label.setText("Prêt.")
            self._set_bar(0.0)
        self._info = None
        self.preview.reset()
        self.options_panel.setVisible(False)

    def _on_return(self) -> None:
        if self._info is not None:
            self.start_download()
        else:
            self.analyze()

    def analyze(self) -> None:
        self._analyze_timer.stop()
        if not self._can_analyze:
            return
        url = self.url_edit.text().strip()
        ytdlp = self._tools[tools.YT_DLP]
        job = InfoJob(str(ytdlp.path), build_info_args(url, self._tool_paths()), self)
        job.succeeded.connect(partial(self._on_info, job))
        job.failed.connect(partial(self._on_info_failed, job))
        self._info_job = job
        self.stage_label.setText("Analyse de la vidéo…")
        self.status_label.clear()
        self._set_bar(None)
        self._update_controls()
        job.start()

    def _on_info(self, job: InfoJob, info: VideoInfo) -> None:
        if job is not self._info_job:
            return
        if not info.formats:
            self._on_info_failed(job, "aucun format trouvé (les playlists ne sont pas gérées).")
            return
        self._info_job = None
        self._info = info
        self.preview.show_info(info)
        self.options_panel.set_heights(info.heights)
        self.options_panel.subtitles.set_choices(subtitle_choices(info))
        self.options_panel.setVisible(True)
        self._refresh_size()
        self.stage_label.setText("Prêt.")
        self._set_bar(0.0)
        self._update_controls()

    def _on_info_failed(self, job: InfoJob, message: str) -> None:
        if job is not self._info_job:
            return
        self._info_job = None
        self.stage_label.setText(f"Échec de l'analyse : {friendly_error(message)}")
        self._append_log(f"Échec de l'analyse : {message}")
        self._set_bar(0.0)
        self._update_controls()

    def _refresh_size(self) -> None:
        if self._info is None:
            return
        opts = self.options_panel.build_options("", Path(self.dir_edit.text()))
        size = estimate_size(self._info, opts)
        text = f"≈ {format_size(size)}" if size else "inconnue"
        self.options_panel.size_label.setText(f"Taille estimée : {text}")

    # ------------------------------------------------------------------ download

    def start_download(self) -> None:
        url = self.url_edit.text().strip()
        ytdlp = self._tools.get(tools.YT_DLP)
        if (
            self._busy
            or not url
            or self._info is None
            or ytdlp is None
            or self._tools.get(tools.FFMPEG) is None
        ):
            return
        output_dir = Path(self.dir_edit.text())
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Dossier de sortie", f"Dossier inutilisable : {exc}")
            return

        options = self.options_panel.build_options(url, output_dir)
        args = build_download_args(options, self._tool_paths())

        job = DownloadJob(str(ytdlp.path), args, output_dir, self)
        job.progress.connect(self._on_progress)
        job.stage.connect(self.stage_label.setText)
        job.log.connect(self._append_log)
        job.succeeded.connect(self._on_succeeded)
        job.failed.connect(self._on_failed)
        job.cancelled.connect(self._on_cancelled)
        self._job = job
        self._last_dir = None
        self.stage_label.setText("Préparation…")
        self.status_label.clear()
        self._set_bar(None)
        self._append_log(f"--- {url}")
        self._update_controls()
        job.start()

    def cancel_download(self) -> None:
        if self._job:
            self.stage_label.setText("Annulation…")
            self.cancel_button.setEnabled(False)
            self._job.cancel()

    def _on_progress(self, progress: Progress) -> None:
        self._set_bar(progress.fraction)
        self.status_label.setText(format_progress(progress))

    def _end_job(self, stage: str, fraction: float) -> None:
        job, self._job = self._job, None
        if job:
            job.deleteLater()
        self.stage_label.setText(stage)
        self.status_label.clear()
        self._set_bar(fraction)
        self._update_controls()

    def _on_succeeded(self, final: Path | None) -> None:
        self._last_dir = final.parent if final else Path(self.dir_edit.text())
        missing = self._job.failed_subtitles if self._job else []
        stage = f"Terminé : {final.name}" if final else "Terminé."
        if missing:
            stage += f"\nSous-titres non récupérés : {', '.join(missing)} (réessaie plus tard)."
        self._end_job(stage, 1.0)
        self.url_edit.clear()

    def _on_failed(self, message: str) -> None:
        self._end_job(f"Échec : {friendly_error(message)}", 0.0)
        self._append_log(f"Échec : {message}")

    def _on_cancelled(self) -> None:
        self._end_job("Téléchargement annulé.", 0.0)

    # ------------------------------------------------------------------ tools

    def _run_task(self, work: Callable[[Reporter], dict], label: str) -> None:
        task = ToolTask(work, self)
        task.report.connect(self._on_task_report)
        task.succeeded.connect(self._on_task_done)
        task.failed.connect(self._on_task_failed)
        task.finished.connect(self._on_task_finished)
        self._task = task
        self.stage_label.setText(label)
        self._set_bar(None)
        self._update_controls()
        task.start()

    def _on_task_report(self, text: str, fraction: float | None) -> None:
        self.stage_label.setText(text)
        self._set_bar(fraction)

    def _on_task_done(self, found: dict[str, Tool | None]) -> None:
        self._tools = found
        for name, tool in found.items():
            if tool:
                origin = "gérée" if tool.managed else "système"
                self._append_log(f"{name} {tool.version} ({origin}) : {tool.path}")
            else:
                self._append_log(f"{name} : introuvable")
        self._refresh_tools_banner()
        self.stage_label.setText("Prêt.")
        self._task_ok = True

    def _on_task_failed(self, message: str) -> None:
        self.stage_label.setText(f"Échec : {message}")
        self._append_log(f"Échec : {message}")
        self._task_ok = False

    def _on_task_finished(self) -> None:
        task, self._task = self._task, None
        if task:
            task.deleteLater()
        self._set_bar(0.0)
        self._update_controls()
        # A URL pasted while the tools were being detected is analysed now.
        if self._task_ok and is_youtube_url(self.url_edit.text()):
            self.analyze()

    def _install_missing(self) -> None:
        if self._busy:
            return
        missing = [name for name in tools.TOOL_NAMES if self._tools.get(name) is None]
        if tools.FFMPEG in missing and tools.current_platform().os == "linux":
            answer = QMessageBox.question(
                self,
                "Installer ffmpeg",
                "Le build statique de ffmpeg pèse environ 350 Mo.\n"
                "« sudo apt install ffmpeg » est préférable sur Ubuntu.\n\n"
                "Télécharger quand même le build statique ?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                missing.remove(tools.FFMPEG)
        if missing:
            self._run_task(lambda report: _install_tools(missing, report), "Installation…")

    def _update_yt_dlp(self) -> None:
        if self._busy:
            return
        current = self._tools.get(tools.YT_DLP)

        def work(report: Reporter) -> dict[str, Tool | None]:
            if current is not None and current.managed:
                report("Mise à jour de yt-dlp…", None)
                output = tools.update_yt_dlp(current)
                report(output.splitlines()[-1] if output else "", None)
                return tools.find_all()
            # A system copy cannot be updated by the app: install a managed one, used from now on.
            return _install_tools([tools.YT_DLP], report)

        self._run_task(work, "Mise à jour de yt-dlp…")

    # ------------------------------------------------------------------ closing

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._task is not None:
            QMessageBox.information(
                self, "Opération en cours", "Attends la fin de l'opération sur les outils."
            )
            event.ignore()
            return
        if self._job is not None:
            answer = QMessageBox.question(
                self, "Téléchargement en cours", "Annuler le téléchargement et quitter ?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            job = self._job
            job.cancel()
            job.wait()
        if self._info_job is not None:
            info_job, self._info_job = self._info_job, None
            info_job.cancel()
            info_job.wait()
        event.accept()
