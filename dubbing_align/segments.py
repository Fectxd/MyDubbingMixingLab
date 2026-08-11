"""把 easyaligner 的对齐结果与字幕时间轴合并成 Reaper item 计划。

纯逻辑模块：不依赖 torch / easyaligner，只依赖 rapidfuzz。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from .manifest import TrackSpec
from .transcript import SubtitleLine

logger = logging.getLogger(__name__)


@dataclass
class AlignedSegment:
    """easyaligner 对齐 JSON 里的一条 AlignmentSegment。"""

    start: float
    end: float
    text: str = ""
    score: float | None = None
    words: list[dict] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class ItemPlan:
    """一条字幕在 Reaper 里的摆放计划。"""

    line_id: int
    actor: str
    source_file: str
    src_in: float
    src_out: float
    tgt_in: float
    tgt_out: float
    playrate: float
    stretch: bool
    status: str  # ok / stretch / out_of_range / missing
    note: str = ""
    score: float | None = None
    text: str = ""


def load_alignment_json(path: str | Path) -> list[AlignedSegment]:
    """读取 easyaligner 输出的对齐 JSON（speeches[].alignments[]）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[AlignedSegment] = []
    for speech in data.get("speeches") or []:
        for a in speech.get("alignments") or []:
            out.append(
                AlignedSegment(
                    start=float(a["start"]),
                    end=float(a["end"]),
                    text=a.get("text", ""),
                    score=a.get("score"),
                    words=a.get("words", []),
                )
            )
    return out


def build_item_plans(
    track: TrackSpec,
    lines_by_id: dict[int, SubtitleLine],
    aligned: list[AlignedSegment],
    max_stretch: float,
) -> list[ItemPlan]:
    """对齐段按顺序匹配到该文件配置的字幕行，生成摆放计划。"""
    lines = [lines_by_id[i] for i in track.line_ids]

    if len(aligned) != len(lines):
        logger.warning(
            "%s: 对齐出 %d 段，字幕行 %d 行，尝试按文本模糊匹配",
            track.file,
            len(aligned),
            len(lines),
        )
        matched = _fuzzy_assign(lines, aligned)
    else:
        matched = list(zip(lines, aligned))

    plans: list[ItemPlan] = []
    for line, seg in matched:
        if seg is None:
            plans.append(_missing_plan(track, line))
            continue

        tgt_dur = line.duration
        src_dur = seg.duration
        ratio = src_dur / tgt_dur if tgt_dur > 0 else 1.0
        delta = ratio - 1.0

        if abs(delta) <= max_stretch:
            plans.append(
                ItemPlan(
                    line_id=line.index,
                    actor=track.actor,
                    source_file=track.file,
                    src_in=seg.start,
                    src_out=seg.end,
                    tgt_in=line.start,
                    tgt_out=line.end,
                    playrate=ratio,
                    stretch=abs(delta) > 1e-6,
                    status="ok" if abs(delta) <= 1e-6 else "stretch",
                    note=f"变速 {delta * 100:+.1f}%",
                    score=seg.score,
                    text=line.text,
                )
            )
        else:
            plans.append(
                ItemPlan(
                    line_id=line.index,
                    actor=track.actor,
                    source_file=track.file,
                    src_in=seg.start,
                    src_out=seg.end,
                    tgt_in=line.start,
                    tgt_out=line.start + src_dur,
                    playrate=1.0,
                    stretch=False,
                    status="out_of_range",
                    note=(
                        f"源 {src_dur:.2f}s vs 目标 {tgt_dur:.2f}s，"
                        f"超出 ±{max_stretch * 100:.0f}%，仅对齐起点，需人工处理"
                    ),
                    score=seg.score,
                    text=line.text,
                )
            )
    return plans


def _missing_plan(track: TrackSpec, line: SubtitleLine) -> ItemPlan:
    return ItemPlan(
        line_id=line.index,
        actor=track.actor,
        source_file=track.file,
        src_in=0.0,
        src_out=0.0,
        tgt_in=line.start,
        tgt_out=line.end,
        playrate=1.0,
        stretch=False,
        status="missing",
        note="未找到对齐结果",
        text=line.text,
    )


def _norm(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _fuzzy_assign(
    lines: list[SubtitleLine], aligned: list[AlignedSegment]
) -> list[tuple[SubtitleLine, AlignedSegment | None]]:
    """单调地把字幕行匹配到对齐段（演员漏录/多录时兜底）。"""
    result: list[tuple[SubtitleLine, AlignedSegment | None]] = []
    cursor = 0
    for line in lines:
        needle = _norm(line.text)
        best_idx: int | None = None
        best_score = 0.0
        for j in range(cursor, len(aligned)):
            score = fuzz.partial_ratio(needle, _norm(aligned[j].text))
            if score > best_score:
                best_idx, best_score = j, score
        if best_idx is not None and best_score >= 60:
            result.append((line, aligned[best_idx]))
            cursor = best_idx + 1
        else:
            result.append((line, None))
    return result
