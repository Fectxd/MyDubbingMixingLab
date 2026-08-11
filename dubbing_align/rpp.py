"""用 reathon 把 item 计划写成 Reaper .RPP。"""

from __future__ import annotations

from pathlib import Path

from reathon.nodes import Item, Project, Source, Track

from .segments import ItemPlan


def build_rpp(
    plans: list[ItemPlan], out_path: str | Path, audio_root: str | Path
) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    project = Project()
    tracks: dict[str, Track] = {}

    # 先建轨道，保持 manifest 里的演员出现顺序
    for plan in plans:
        tracks.setdefault(plan.actor, Track(name=plan.actor))

    # 轨道挂进工程（reathon 需要显式 add，否则序列化时不会出现）
    for track in tracks.values():
        project.add(track)

    for plan in plans:
        if plan.status == "missing":
            continue
        source = Source(file=str((Path(audio_root) / plan.source_file).resolve()))
        length = (plan.tgt_out - plan.tgt_in) if plan.stretch else (plan.src_out - plan.src_in)
        item = Item(source, position=plan.tgt_in, length=length)
        item.props.append(["SOFFS", f"{plan.src_in:.6f}"])
        if plan.stretch:
            item.props.append(["PLAYRATE", f"{plan.playrate:.6f}"])
        tracks[plan.actor].add(item)

    # 每条字幕一个 marker，便于在 Reaper 里对号入座
    for plan in plans:
        label = plan.text.replace('"', "'")[:48] or f"line {plan.line_id}"
        project.add_marker(plan.line_id, plan.tgt_in, label, color=0)

    project.write(str(out_path))
