"""SRT 字幕解析：提供目标时间轴（字幕里每句的 in/out 点）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import srt


@dataclass(frozen=True)
class SubtitleLine:
    """字幕文件里的一行。index 对应字幕序号，start/end 是目标时间轴秒数。"""

    index: int
    text: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def parse_srt(path: str | Path) -> list[SubtitleLine]:
    """解析 SRT 文件为 SubtitleLine 列表（保持原始序号）。"""
    raw = Path(path).read_text(encoding="utf-8-sig")
    lines: list[SubtitleLine] = []
    for sub in srt.parse(raw):
        text = _clean(sub.content)
        if not text:
            continue
        lines.append(
            SubtitleLine(
                index=int(sub.index),
                text=text,
                start=sub.start.total_seconds(),
                end=sub.end.total_seconds(),
            )
        )
    return lines


def _clean(text: str) -> str:
    parts = [p.strip() for p in text.replace("\r", " ").splitlines() if p.strip()]
    return " ".join(parts).strip()
