import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer

from ytdownloader.jobs import DownloadJob, InfoJob, LineSplitter, ToolTask, partial_paths
from ytdownloader.tools import ToolError
from ytdownloader.ytdlp import PROGRESS_MARKER, Progress


def run_until(signals, timeout_ms=10000, start=None):
    """Spin the event loop until one of the signals fires; return its name and args.

    A thread must be passed as ``start``: it could finish before the signals are connected.
    """
    loop = QEventLoop()
    result = []
    for name, signal in signals.items():
        signal.connect(lambda *args, n=name: (result.append((n, args)), loop.quit()))
    if start:
        start()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    assert result, "timed out"
    return result[0]


def outcomes(job):
    return {"ok": job.succeeded, "failed": job.failed, "cancelled": job.cancelled}


def python_job(script, tmp_path):
    return DownloadJob(sys.executable, ["-u", "-c", script], tmp_path)


def _subtitle_event(path):
    return Progress("downloading", 1, 2, None, None, str(path))


def test_cancel_removes_converted_subtitle(qapp, tmp_path):
    job = DownloadJob("true", [], tmp_path)
    job._track(_subtitle_event(tmp_path / "a.en.vtt"))
    (tmp_path / "a.en.srt").write_text("converted")
    job._cleanup()
    assert not (tmp_path / "a.en.srt").exists()


def test_cancel_keeps_preexisting_subtitle(qapp, tmp_path):
    (tmp_path / "a.en.srt").write_text("mine")
    job = DownloadJob("true", [], tmp_path)
    job._track(_subtitle_event(tmp_path / "a.en.vtt"))
    job._cleanup()
    assert (tmp_path / "a.en.srt").read_text() == "mine"


def test_line_splitter_handles_partial_chunks():
    splitter = LineSplitter()
    assert splitter.feed(b"one\ntw") == ["one"]
    assert splitter.feed("o\r\né".encode()) == ["two"]
    assert splitter.flush() == ["é"]
    assert splitter.flush() == []


def test_partial_paths_finds_only_related_files(tmp_path):
    for name in (
        "clip.f137.mp4",
        "clip.f137.mp4.part",
        "clip.f137.mp4.ytdl",
        "clip.f137.mp4.part-Frag3",
        "clip.temp.mp4",
        "clip.mp4",
        "other.mp4.part",
    ):
        (tmp_path / name).touch()
    found = {p.name for p in partial_paths(tmp_path / "clip.f137.mp4", tmp_path)}
    assert found == {
        "clip.f137.mp4",
        "clip.f137.mp4.part",
        "clip.f137.mp4.ytdl",
        "clip.f137.mp4.part-Frag3",
        "clip.temp.mp4",
    }


def test_job_reports_progress_and_final_file(qapp, tmp_path):
    script = (
        f"print('{PROGRESS_MARKER}"
        '{"status":"downloading","downloaded_bytes":5,"total_bytes":10,"filename":"a.mp4"}\');'
        "print('[ytdl-file]/out/a.mp4')"
    )
    job = python_job(script, tmp_path)
    seen = []
    job.progress.connect(seen.append)
    job.start()
    name, args = run_until(outcomes(job))
    assert name == "ok"
    assert args[0] == Path("/out/a.mp4")
    assert seen[0].fraction == 0.5


def test_job_reports_last_error(qapp, tmp_path):
    script = "import sys; print('ERROR: video unavailable', file=sys.stderr); sys.exit(1)"
    job = python_job(script, tmp_path)
    job.start()
    assert run_until(outcomes(job)) == ("failed", ("video unavailable",))


def test_job_reports_missing_program(qapp, tmp_path):
    job = DownloadJob(str(tmp_path / "nope"), [], tmp_path)
    job.start()
    name, _ = run_until(outcomes(job)) if job.running else ("failed", ())
    assert name == "failed"


def test_cancel_kills_children_and_keeps_existing_files(qapp, tmp_path):
    existing = tmp_path / "existing.mp4"
    existing.write_text("keep me")
    partial = tmp_path / "new.mp4.part"
    pid_file = tmp_path / "child.pid"
    script = f"""
import json, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open({str(pid_file)!r}, "w").write(str(child.pid))
open({str(partial)!r}, "w").write("partial")
m = {PROGRESS_MARKER!r}
print(m + json.dumps({{"status": "finished", "filename": {str(existing)!r}}}))
print(m + json.dumps({{"status": "downloading", "filename": {str(tmp_path / "new.mp4")!r}}}))
time.sleep(60)
"""
    job = python_job(script, tmp_path)
    job.progress.connect(lambda p: p.status == "downloading" and job.cancel())
    job.start()
    name, _ = run_until(outcomes(job))

    assert name == "cancelled"
    assert existing.exists()
    assert not partial.exists()
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and _alive(child):
        time.sleep(0.05)
    assert not _alive(child)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):  # exited since the check above
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def test_tool_task_reports_result_and_errors(qapp):
    task = ToolTask(lambda report: 42)
    signals = {"ok": task.succeeded, "failed": task.failed}
    assert run_until(signals, start=task.start) == ("ok", (42,))
    task.wait()

    def boom(report):
        raise ToolError("nope")

    task = ToolTask(boom)
    signals = {"ok": task.succeeded, "failed": task.failed}
    assert run_until(signals, start=task.start) == ("failed", ("nope",))
    task.wait()


def info_outcomes(job):
    return {"ok": job.succeeded, "failed": job.failed, "cancelled": job.cancelled}


def test_info_job_parses_metadata(qapp):
    script = """print('{"id": "x", "title": "Clip é", "formats": []}')"""
    job = InfoJob(sys.executable, ["-c", script])
    signals = info_outcomes(job)
    job.start()
    name, args = run_until(signals)
    assert name == "ok"
    assert args[0].title == "Clip é"


def test_info_job_reports_error(qapp):
    script = "import sys; print('ERROR: private video', file=sys.stderr); sys.exit(1)"
    job = InfoJob(sys.executable, ["-c", script])
    signals = info_outcomes(job)
    job.start()
    assert run_until(signals) == ("failed", ("private video",))


def test_info_job_can_be_cancelled(qapp):
    job = InfoJob(sys.executable, ["-c", "import time; time.sleep(60)"])
    signals = info_outcomes(job)
    job.start()
    assert job.wait(0) is False
    job.cancel()
    assert run_until(signals)[0] == "cancelled"
