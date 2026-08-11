"""无 torch 冒烟测试：伪造对齐 JSON → 生成 RPP，验证 reathon 链路。

运行：python scripts/smoke_test.py   （在项目根目录）
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dubbing_align.manifest import TrackSpec  # noqa: E402
from dubbing_align.rpp import build_rpp  # noqa: E402
from dubbing_align.segments import AlignedSegment, build_item_plans  # noqa: E402
from dubbing_align.transcript import SubtitleLine  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "audio").mkdir()
        fake_wav = tmp / "audio" / "actor1.wav"
        fake_wav.write_bytes(b"RIFFfake")  # 占位文件，Reaper 打开前需换成真素材

        lines = [
            SubtitleLine(index=1, text="Hola, ¿qué tal?", start=10.0, end=12.5),
            SubtitleLine(index=2, text="Muy bien, gracias.", start=13.0, end=15.0),
        ]
        lines_by_id = {line.index: line for line in lines}

        track = TrackSpec(actor="actor1", file="audio/actor1.wav", line_ids=[1, 2])
        aligned = [
            AlignedSegment(start=1.0, end=3.5, text="Hola, ¿qué tal?", score=0.9),
            AlignedSegment(start=4.0, end=5.9, text="Muy bien, gracias.", score=0.85),
        ]

        plans = build_item_plans(track, lines_by_id, aligned, max_stretch=0.05)
        assert len(plans) == 2, plans
        assert plans[0].status == "ok", plans[0]
        # 源 1.9s vs 目标 2.0s → 差 5%，恰好落在容差内 → stretch
        assert plans[1].status == "stretch", plans[1]
        assert abs(plans[1].playrate - 0.95) < 1e-9, plans[1]

        out = tmp / "out" / "demo.rpp"
        build_rpp(plans, out, audio_root=tmp)
        text = out.read_text(encoding="utf-8")
        assert 'NAME "actor1"' in text
        assert "POSITION 10.0" in text
        assert "SOURCE WAVE" in text
        assert "MARKER" in text
        assert "PLAYRATE 0.950000" in text
        assert "SOFFS 4.000000" in text

        print("Smoke test passed")
        print(f"RPP written to {out}")


if __name__ == "__main__":
    main()
