"""项目清单（manifest.json）加载与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TrackSpec:
    """一条干声文件：actor 是谁、文件在哪、按顺序包含哪些字幕行。"""

    actor: str
    file: str  # 相对 audio_dir 的路径
    line_ids: list[int] = field(default_factory=list)


@dataclass
class Manifest:
    project: str
    srt: str
    audio_dir: str
    tracks: list[TrackSpec]
    language: str = "es"
    emissions_model: str | None = None  # 缺省按 language 选
    output_dir: str = "work"
    max_stretch: float = 0.05  # 允许的时长伸缩范围（±5%）
    device: str | None = None  # auto / cpu / cuda
    half: bool | None = None  # 是否半精度，默认 GPU 上开启
    start_wildcard: bool = True
    end_wildcard: bool = True
    chunk_size: int = 30
    base_dir: Path = field(default_factory=Path.cwd)

    @property
    def srt_path(self) -> Path:
        return self.base_dir / self.srt

    @property
    def audio_root(self) -> Path:
        return self.base_dir / self.audio_dir

    @property
    def work_dir(self) -> Path:
        return self.base_dir / self.output_dir

    @property
    def alignments_dir(self) -> Path:
        return self.work_dir / "alignments"

    @property
    def rpp_path(self) -> Path:
        return self.work_dir / f"{self.project}.rpp"

    @property
    def report_path(self) -> Path:
        return self.work_dir / f"{self.project}_report.json"


def load_manifest(path: str | Path) -> Manifest:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    tracks = [
        TrackSpec(
            actor=t["actor"],
            file=t["file"],
            line_ids=[int(i) for i in t.get("line_ids", [])],
        )
        for t in data["tracks"]
    ]
    return Manifest(
        project=data["project"],
        srt=data["srt"],
        audio_dir=data["audio_dir"],
        tracks=tracks,
        language=data.get("language", "es"),
        emissions_model=data.get("emissions_model"),
        output_dir=data.get("output_dir", "work"),
        max_stretch=float(data.get("max_stretch", 0.05)),
        device=data.get("device"),
        half=data.get("half"),
        start_wildcard=data.get("start_wildcard", True),
        end_wildcard=data.get("end_wildcard", True),
        chunk_size=int(data.get("chunk_size", 30)),
        base_dir=path.parent,
    )
