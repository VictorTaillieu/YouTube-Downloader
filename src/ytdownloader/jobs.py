"""Background work for the UI: yt-dlp downloads (QProcess) and tool installs (QThread)."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, Signal

from .tools import ToolError
from .transcript import unique_path, vtt_to_text
from .ytdlp import (
    DownloadOptions,
    FinalFile,
    Progress,
    ToolPaths,
    build_download_args,
    is_subtitle_file,
    parse_info_json,
    parse_output_line,
    postprocess_stage,
    subtitle_failure,
)

_STREAM_FILE = re.compile(r"^(?P<stem>.+)\.f[\w-]+\.\w+$")

Reporter = Callable[[str, float | None], None]
"""Called by a tool task with (status text, fraction or None)."""


class LineSplitter:
    """Reassemble complete text lines from the arbitrary chunks QProcess delivers."""

    def __init__(self) -> None:
        self._pending = b""

    def feed(self, chunk: bytes) -> list[str]:
        *lines, self._pending = (self._pending + chunk).split(b"\n")
        return [line.decode("utf-8", "replace").rstrip("\r") for line in lines]

    def flush(self) -> list[str]:
        rest, self._pending = self._pending, b""
        return [rest.decode("utf-8", "replace").rstrip("\r")] if rest else []


def partial_paths(filename: str | Path, output_dir: Path) -> list[Path]:
    """Existing files left behind by an interrupted download of ``filename``."""
    path = Path(filename)
    if not path.is_absolute():
        path = output_dir / path
    folder = path.parent
    if not folder.is_dir():
        return []

    names = {path.name, f"{path.name}.part", f"{path.name}.ytdl"}
    fragments = f"{path.name}.part-"
    stream = _STREAM_FILE.match(path.name)
    merge_prefix = f"{stream['stem']}.temp." if stream else None

    found = []
    for entry in folder.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if (
            name in names
            or name.startswith(fragments)
            or (merge_prefix and name.startswith(merge_prefix))
        ):
            found.append(entry)
    return found


def kill_process_tree(pid: int) -> None:
    """Kill a process and its children (standalone yt-dlp re-executes itself, then ffmpeg)."""
    if sys.platform.startswith("win"):
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
        )
    else:
        # The job runs in its own session, so its process group id is its pid.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)


def _new_process(program: str, args: Sequence[str], parent: QObject) -> QProcess:
    """QProcess for yt-dlp: UTF-8 output, and its own session so the whole tree can be killed."""
    process = QProcess(parent)
    process.setProgram(program)
    process.setArguments(list(args))
    env = QProcessEnvironment.systemEnvironment()
    env.insert("PYTHONUTF8", "1")
    env.insert("PYTHONIOENCODING", "utf-8")
    process.setProcessEnvironment(env)
    if not sys.platform.startswith("win"):
        process.setUnixProcessParameters(QProcess.UnixProcessFlag.CreateNewSession)
    return process


def _terminate(process: QProcess) -> None:
    pid = process.processId()
    if not pid:
        process.kill()
        return
    try:
        kill_process_tree(pid)
    except (OSError, subprocess.SubprocessError):
        process.kill()


class InfoJob(QObject):
    """Fetch video metadata. Emits one of the signals below, then deletes itself."""

    succeeded = Signal(object)  # VideoInfo
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, program: str, args: Sequence[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancelling = False
        self._done = False
        self._process = _new_process(program, args, self)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

    def start(self) -> None:
        self._process.start()

    def cancel(self) -> None:
        if self._done or self._process.state() == QProcess.ProcessState.NotRunning:
            return
        self._cancelling = True
        _terminate(self._process)

    def wait(self, msecs: int = 5000) -> bool:
        return self._process.waitForFinished(msecs)

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._done:
            return
        self._done = True
        if self._cancelling:
            self.cancelled.emit()
        elif status == QProcess.ExitStatus.NormalExit and code == 0:
            output = self._process.readAllStandardOutput().data().decode("utf-8", "replace")
            try:
                self.succeeded.emit(parse_info_json(output))
            except ValueError:
                self.failed.emit("Réponse de yt-dlp illisible.")
        else:
            self.failed.emit(self._error_message(code))
        self.deleteLater()

    def _error_message(self, code: int) -> str:
        stderr = self._process.readAllStandardError().data().decode("utf-8", "replace")
        errors = [line for line in stderr.splitlines() if line.startswith("ERROR:")]
        if errors:
            return errors[-1].removeprefix("ERROR:").strip()
        return f"yt-dlp s'est arrêté avec le code {code}."

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and not self._done:
            self._done = True
            self.failed.emit(f"Impossible de lancer yt-dlp : {self._process.errorString()}")
            self.deleteLater()


class DownloadJob(QObject):
    """One yt-dlp run. Emits exactly one of ``succeeded``, ``failed`` or ``cancelled``."""

    progress = Signal(object)  # Progress
    stage = Signal(str)
    log = Signal(str)
    succeeded = Signal(object)  # Path of the final file, or None
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self, program: str, args: Sequence[str], output_dir: Path, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._output_dir = output_dir
        self._stdout = LineSplitter()
        self._stderr = LineSplitter()
        self._streams: list[str] = []
        self._written: set[str] = set()
        self._final: Path | None = None
        self._last_error: str | None = None
        self.failed_subtitles: list[str] = []
        """Languages yt-dlp could not fetch; the download itself may still succeed."""
        self._cancelling = False
        self._done = False

        self._process = _new_process(program, args, self)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

    @property
    def running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    def start(self) -> None:
        self._process.start()

    def cancel(self) -> None:
        if self._done or not self.running:
            return
        self._cancelling = True
        _terminate(self._process)

    def wait(self, msecs: int = 5000) -> bool:
        return self._process.waitForFinished(msecs)

    def _read_stdout(self) -> None:
        for line in self._stdout.feed(self._process.readAllStandardOutput().data()):
            self._handle_stdout(line)

    def _read_stderr(self) -> None:
        for line in self._stderr.feed(self._process.readAllStandardError().data()):
            self._handle_stderr(line)

    def _handle_stdout(self, line: str) -> None:
        event = parse_output_line(line)
        if isinstance(event, Progress):
            self._track(event)
            self.progress.emit(event)
        elif isinstance(event, FinalFile):
            self._final = event.path
        elif line.strip():
            self.log.emit(line)
            if label := postprocess_stage(line):
                self.stage.emit(label)

    def _handle_stderr(self, line: str) -> None:
        if not line.strip():
            return
        self.log.emit(line)
        if (language := subtitle_failure(line)) and language not in self.failed_subtitles:
            self.failed_subtitles.append(language)
        if line.startswith("ERROR:"):
            self._last_error = line.removeprefix("ERROR:").strip()

    def _track(self, event: Progress) -> None:
        name = event.filename
        if not name:
            return
        # A file only reported as "finished" already existed: never delete it on cancel.
        if event.status == "downloading":
            if name not in self._written and is_subtitle_file(name):
                # Subtitles are converted before the video downloads: the .srt is ours too,
                # unless it was already there.
                converted = (self._output_dir / name).with_suffix(".srt")
                if not converted.exists():
                    self._written.add(str(converted))
            self._written.add(name)
        if name in self._streams:
            return
        self._streams.append(name)
        if is_subtitle_file(name):
            self.stage.emit("Téléchargement des sous-titres…")
        else:
            count = sum(not is_subtitle_file(s) for s in self._streams)
            self.stage.emit(f"Téléchargement du flux {count}…")

    def _cleanup(self) -> None:
        for name in self._written:
            for path in partial_paths(name, self._output_dir):
                try:
                    path.unlink()
                    self.log.emit(f"Supprimé : {path}")
                except OSError as exc:
                    self.log.emit(f"Impossible de supprimer {path} : {exc}")

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._done:
            return
        self._read_stdout()
        self._read_stderr()
        for line in self._stdout.flush():
            self._handle_stdout(line)
        for line in self._stderr.flush():
            self._handle_stderr(line)
        self._done = True
        self._conclude(code, status)

    def _conclude(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._cancelling:
            self._cleanup()
            self.cancelled.emit()
        elif status == QProcess.ExitStatus.NormalExit and code == 0:
            self.succeeded.emit(self._final)
        elif self._last_error:
            self.failed.emit(self._last_error)
        else:
            self.failed.emit(f"yt-dlp s'est arrêté avec le code {code}.")

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and not self._done:
            self._done = True
            self._cleanup()
            self.failed.emit(f"Impossible de lancer yt-dlp : {self._process.errorString()}")


class TranscriptJob(DownloadJob):
    """Fetch subtitles into a temporary folder, then write them as text in the output folder.

    Nothing reaches the output folder before yt-dlp has finished, so a cancel leaves no file.
    """

    def __init__(
        self,
        program: str,
        options: DownloadOptions,
        tools: ToolPaths | None = None,
        parent: QObject | None = None,
    ) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="ytdownloader-"))
        args = build_download_args(replace(options, output_dir=temp_dir), tools)
        super().__init__(program, args, temp_dir, parent)
        self._target_dir = options.output_dir
        self._languages = list(options.subtitles.languages)
        self._failure_line: str | None = None
        self.transcripts: list[Path] = []

    def _handle_stderr(self, line: str) -> None:
        super()._handle_stderr(line)
        if subtitle_failure(line):
            self._failure_line = line

    def _cleanup(self) -> None:
        shutil.rmtree(self._output_dir, ignore_errors=True)

    def _write_transcripts(self) -> None:
        tracks = [p for p in self._output_dir.iterdir() if p.suffix == ".vtt"]
        missing: list[str] = []
        for language in self._languages:
            track = next((p for p in tracks if p.name.endswith(f".{language}.vtt")), None)
            text = vtt_to_text(track.read_text("utf-8", "replace")) if track else ""
            if not text:
                missing.append(language)
                continue
            target = unique_path(self._target_dir / track.with_suffix(".txt").name)
            target.write_text(text, encoding="utf-8")
            self.transcripts.append(target)
        self.failed_subtitles = missing

    def _conclude(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._cancelling:
            self._cleanup()
            self.cancelled.emit()
            return
        try:
            self._write_transcripts()
        except OSError as exc:
            for path in self.transcripts:
                path.unlink(missing_ok=True)
            self._cleanup()
            self.failed.emit(f"Impossible d'écrire la transcription : {exc}")
            return
        self._cleanup()
        if self.transcripts:
            self.succeeded.emit(self.transcripts[0])
        else:
            detail = self._last_error or self._failure_line
            message = "Aucun sous-titre n'a pu être récupéré."
            self.failed.emit(f"{message} {detail}" if detail else message)


class ToolTask(QThread):
    """Run a blocking tool operation (install, update, detection) off the UI thread."""

    report = Signal(str, object)  # status text, fraction or None
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable[[Reporter], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        try:
            result = self._work(self.report.emit)
        except (ToolError, OSError, subprocess.SubprocessError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
