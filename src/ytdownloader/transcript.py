"""Turn a WebVTT subtitle track into clean, readable text. Pure helpers, no process or Qt."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

PARAGRAPH_PAUSE = 2.0
"""Silence, in seconds, that starts a new paragraph."""

_TIME = r"(?:\d+:)?\d{2}:\d{2}\.\d{3}"
_TIMING = re.compile(rf"^\s*({_TIME})\s+-->\s+({_TIME})")
_WORD_TIME = re.compile(rf"<({_TIME})>")
_TAG = re.compile(r"<[^>]*>")
_ANNOTATION = re.compile(r"\[[^\]]*\]")
_SPEAKER = re.compile(r"(?:^|(?<=\s))>{2,}(?=\s|$)")


@dataclass
class _Cue:
    start: float
    end: float
    lines: list[str]


def _seconds(stamp: str) -> float:
    total = 0.0
    for part in stamp.split(":"):
        total = total * 60 + float(part)
    return total


def _parse_cues(vtt: str) -> list[_Cue]:
    # Only a truly empty line ends a cue: rolling captions contain lines holding a single space.
    cues: list[_Cue] = []
    current: _Cue | None = None
    for line in vtt.replace("\r\n", "\n").split("\n"):
        if timing := _TIMING.match(line):
            current = _Cue(_seconds(timing[1]), _seconds(timing[2]), [])
            cues.append(current)
        elif not line:
            current = None
        elif current is not None:
            current.lines.append(line)
    return cues


def _clean(line: str) -> str:
    # Tags go first, so that an escaped "&lt;b&gt;" stays visible as text.
    text = html.unescape(_TAG.sub("", line))
    text = _SPEAKER.sub(" ", _ANNOTATION.sub(" ", text))
    return " ".join(text.split())


def vtt_to_text(vtt: str) -> str:
    """Text of a VTT track, without timings or markup; empty if it holds no spoken text.

    YouTube's rolling automatic captions repeat each line in the following cues. They are
    recognised by their per-word timings, and only they are deduplicated: a chorus in a manual
    track is real content.
    """
    cues = _parse_cues(vtt)
    rolling = any(_WORD_TIME.search(line) for cue in cues for line in cue.lines)

    paragraphs: list[list[str]] = [[]]
    last_time: float | None = None
    previous: set[str] = set()
    for cue in cues:
        for raw in cue.lines:
            text = _clean(raw)
            if not text:
                continue
            words = _WORD_TIME.findall(raw)
            if rolling and not words and text in previous:
                continue
            if (
                last_time is not None
                and cue.start - last_time >= PARAGRAPH_PAUSE
                and paragraphs[-1]
            ):
                paragraphs.append([])
            paragraphs[-1].append(text)
            # A rolling cue stays on screen through silences: its last word dates the speech.
            last_time = (_seconds(words[-1]) if words else cue.start) if rolling else cue.end
        previous = {text for raw in cue.lines if (text := _clean(raw))}

    body = "\n\n".join(" ".join(lines) for lines in paragraphs if lines)
    return f"{body}\n" if body else ""


def unique_path(path: Path) -> Path:
    """``path``, or the first free ``name (2).ext`` variant: never overwrite a file."""
    candidate = path
    number = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        number += 1
    return candidate
