"""生成对齐/摆放报告（JSON + CSV）。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .segments import ItemPlan


def write_report(
    plans: list[ItemPlan], out_json: str | Path, out_csv: str | Path | None = None
) -> None:
    rows = []
    for p in plans:
        rows.append(
            {
                "line_id": p.line_id,
                "actor": p.actor,
                "source_file": p.source_file,
                "src_in": round(p.src_in, 4),
                "src_out": round(p.src_out, 4),
                "tgt_in": round(p.tgt_in, 4),
                "tgt_out": round(p.tgt_out, 4),
                "playrate": round(p.playrate, 4),
                "stretch": p.stretch,
                "status": p.status,
                "score": None if p.score is None else round(p.score, 4),
                "note": p.note,
                "text": p.text,
            }
        )

    out_json = Path(out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if out_csv is not None:
        out_csv = Path(out_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=list(rows[0].keys()) if rows else ["line_id"]
            )
            writer.writeheader()
            writer.writerows(rows)
