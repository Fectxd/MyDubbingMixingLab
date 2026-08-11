"""一键：对齐 → 切段计划 → Reaper 工程 + 报告。

用法：
    python run_pipeline.py --manifest examples/manifest.example.json
    python run_pipeline.py --manifest ... --skip-align   # 复用已有对齐结果重建工程
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dubbing_align.align import run_alignment
from dubbing_align.manifest import load_manifest
from dubbing_align.report import write_report
from dubbing_align.rpp import build_rpp
from dubbing_align.segments import build_item_plans, load_alignment_json
from dubbing_align.transcript import parse_srt


def main() -> None:
    parser = argparse.ArgumentParser(description="干声强制对齐 → Reaper 工程")
    parser.add_argument("--manifest", required=True, help="manifest.json 路径")
    parser.add_argument(
        "--skip-align",
        action="store_true",
        help="跳过对齐，复用 work/alignments 下已有的 JSON",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    manifest = load_manifest(args.manifest)
    lines_by_id = {line.index: line for line in parse_srt(manifest.srt_path)}
    logging.info("字幕共 %d 行", len(lines_by_id))

    if args.skip_align:
        alignment_paths = {
            t.file: manifest.alignments_dir / Path(t.file).with_suffix(".json")
            for t in manifest.tracks
        }
        missing = [p for p in alignment_paths.values() if not p.exists()]
        if missing:
            raise SystemExit(f"缺少对齐结果：{missing}，先跑一次不带 --skip-align 的命令")
    else:
        alignment_paths = run_alignment(manifest, lines_by_id)

    plans = []
    for track in manifest.tracks:
        aligned = load_alignment_json(alignment_paths[track.file])
        plans.extend(build_item_plans(track, lines_by_id, aligned, manifest.max_stretch))

    build_rpp(plans, manifest.rpp_path, audio_root=manifest.audio_root)
    write_report(
        plans, manifest.report_path, manifest.report_path.with_suffix(".csv")
    )

    counts: dict[str, int] = {}
    for p in plans:
        counts[p.status] = counts.get(p.status, 0) + 1
    logging.info("RPP: %s", manifest.rpp_path)
    logging.info("报告: %s", manifest.report_path)
    logging.info("状态统计: %s", counts)


if __name__ == "__main__":
    main()
