import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer

from ytdownloader.jobs import DownloadJob, LineSplitter, ToolTask, partial_paths
from ytdownloader.tools import ToolError
from ytdownloader.ytdlp import PROGRESS_MARKER


def run_until(signals, timeout_ms=10000):
    """Spin the event loop until one of the signals fires; return its name and args."""
    loop = QEventLoop()
    result = []
    for name, signal in signals.items():
        signal.connect(lambda *args, n=name: (result.append((n, args)), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    assert result, "timed out"
    return result[0]


def outcomes(job):
    return {"ok": job.succeeded, "failed": job.failed, "cancelled": job.cancelled}


def python_job(script, tmp_path):
    return DownloadJob(sys.executable, ["-u", "-c", script], tmp_path)


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
    stat = Path(f"/proc/{pid}/stat")
    return stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[0] != "Z"


def test_tool_task_reports_result_and_errors(qapp):
    task = ToolTask(lambda report: 42)
    signals = {"ok": task.succeeded, "failed": task.failed}
    task.start()
    assert run_until(signals) == ("ok", (42,))
    task.wait()

    def boom(report):
        raise ToolError("nope")

    task = ToolTask(boom)
    signals = {"ok": task.succeeded, "failed": task.failed}
    task.start()
    assert run_until(signals) == ("failed", ("nope",))
    task.wait()
