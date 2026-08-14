"""Assemble a Reaper project from aligned actor takes + TIGER-DnR stems.

The five actor dry takes in ``test/`` are already aligned to the picture, so
this script simply places every whole take at position 0 on its own track,
adds the separated music/effect stems as background tracks, and keeps the
separated dialogue as a muted QC reference track.

Two mix modes:

* ``raw`` (default, non-destructive): reference the enhanced/original dry
  takes directly and put the loudness match from ``master_report.json`` on
  the track fader (VOLPAN). No audio file is touched; compression/limiting
  can be added inside REAPER with ``scripts/apply_dynamics.lua``.
* ``files`` (deterministic): reference the rendered ``work/mastered/`` wavs
  (compression + limiter + loudness already baked by ``master.py``).

Neither mode converts or rewrites audio files: sample-rate and channel
differences are left to REAPER's own resampling, so sources stay untouched.

Usage:
    python assemble_rpp.py --actors test
    python assemble_rpp.py --actors "test/5*.wav" --out work/reaper/EP05_配音工程.rpp
    python assemble_rpp.py --actors test --mix-mode files
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from reathon.nodes import Item, Project, Source, Track


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ACTOR_DIR = PROJECT_ROOT / "test"
SEPARATED_DIR = PROJECT_ROOT / "work" / "separated"
ENHANCED_DIR = PROJECT_ROOT / "work" / "enhanced"
MASTERED_DIR = PROJECT_ROOT / "work" / "mastered"
MASTER_REPORT = MASTERED_DIR / "master_report.json"
DEFAULT_OUT = PROJECT_ROOT / "work" / "reaper" / "EP05_配音工程.rpp"
SAMPLE_RATE = 44100
DYNAMICS_LUA_NAME = "EP05_dynamics.lua"


def ffprobe_json(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels,sample_fmt:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}:\n{proc.stderr}")
    return json.loads(proc.stdout)


def probe_audio(path: Path) -> tuple[float, int, int, str]:
    data = ffprobe_json(path)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0.0) or 0.0)
    sr = int(stream.get("sample_rate", 0) or 0)
    channels = int(stream.get("channels", 0) or 0)
    sample_fmt = stream.get("sample_fmt", "")
    return duration, sr, channels, sample_fmt


def parse_actor_role(stem: str) -> tuple[str, str]:
    """Best-effort actor/role split from the take filenames."""
    s = stem.strip()
    m = re.match(r"^\d+,\s*(.+)$", s)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) >= 2:
            return parts[0], ",".join(parts[1:]).strip()
        return parts[0], ""
    m = re.match(r"^\d+_(.+)$", s)
    if m:
        parts = [p.strip() for p in m.group(1).split("_") if p.strip()]
        if len(parts) >= 2:
            return parts[0], " ".join(parts[1:])
        return parts[0], ""
    parts = [p.strip() for p in s.split("_") if p.strip()]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return s, ""


def collect_actors(actor_dir: Path) -> list[Path]:
    files = sorted(
        [p for p in actor_dir.iterdir() if p.suffix.lower() == ".wav"],
        key=lambda p: p.name.lower(),
    )
    if not files:
        raise SystemExit(f"no .wav files found in {actor_dir}")
    return files


def find_stems(separated_dir: Path) -> dict[str, Path]:
    """Locate dialog/effect/music stems for whichever episode was separated."""
    candidates = sorted(separated_dir.glob("*_dialog.wav"))
    if not candidates:
        raise SystemExit(
            f"no separated stems (*_dialog.wav) found in {separated_dir}; "
            "run separate.py first"
        )
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise SystemExit(
            f"multiple episodes found ({names}); separate one episode per "
            "work dir before assembling"
        )
    stem = candidates[0].name[: -len("_dialog.wav")]
    return {name: separated_dir / f"{stem}_{name}.wav" for name in ("dialog", "effect", "music")}


def load_master_report() -> dict[str, dict]:
    """Full per-take entries from master_report.json, keyed by file name."""
    if not MASTER_REPORT.exists():
        return {}
    data = json.loads(MASTER_REPORT.read_text(encoding="utf-8"))
    return {t["name"]: t for t in data.get("tracks", [])}


def write_dynamics_lua(out_path: Path, tracks: list[dict]) -> Path:
    """Write per-track compressor/limiter settings for scripts/apply_dynamics.lua."""
    dyn_path = out_path.with_name(DYNAMICS_LUA_NAME)
    lines = [
        "-- auto-generated by assemble_rpp.py (raw non-destructive mode)",
        "-- read by scripts/apply_dynamics.lua inside REAPER",
        "return {",
    ]
    for t in tracks:
        dyn = t.get("dynamics")
        if not dyn:
            continue
        fields = []
        if dyn.get("threshold_db") is not None:
            fields.append(f"threshold_db={dyn['threshold_db']:.1f}")
            fields.append(f"ratio={dyn['ratio']:.2f}")
            fields.append(f"attack={dyn['attack']:.0f}")
            fields.append(f"release={dyn['release']:.0f}")
        fields.append(f"limiter_ceiling_db={dyn['limiter_ceiling_db']:.2f}")
        key = t["name"].replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f"  [\"{key}\"] = {{ {', '.join(fields)} }},")
    lines.append("}")
    dyn_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dyn_path


def build_project(actor_files: list[Path], out_path: Path, mix_mode: str) -> dict:
    stems = find_stems(SEPARATED_DIR)
    missing = [str(p) for p in stems.values() if not p.exists()]
    if missing:
        raise SystemExit(
            "separated stems not found, run separate.py first:\n  " + "\n  ".join(missing)
        )

    project = Project(SAMPLERATE=SAMPLE_RATE)
    manifest_tracks = []
    report = load_master_report() if mix_mode == "raw" else {}

    for file in actor_files:
        actor, role = parse_actor_role(file.stem)
        label = f"{actor} ({role})" if role else actor
        entry = report.get(file.name) or report.get(file.stem)
        mastered_path = MASTERED_DIR / f"{file.stem}.wav"
        enhanced_path = ENHANCED_DIR / f"{file.stem}.wav"
        if mix_mode == "files":
            if mastered_path.exists():
                src = mastered_path
                flags = {"mastered": True, "enhanced": False}
            elif enhanced_path.exists():
                src = enhanced_path
                flags = {"mastered": False, "enhanced": True}
            else:
                src = file
                flags = {"mastered": False, "enhanced": False}
            gain = None
        else:
            # Non-destructive: reference the take as-is (REAPER resamples
            # 48k and handles mono sources natively), loudness on the fader.
            src = enhanced_path if enhanced_path.exists() else file
            flags = {"mastered": False, "enhanced": enhanced_path.exists()}
            gain = float(entry["gain_db"]) if entry and "gain_db" in entry else None
        duration, _, _, _ = probe_audio(src)
        track = Track(name=label)
        if gain is not None:
            vol = float(10 ** (gain / 20.0))
            track.props.append(["VOLPAN", f"{vol:.4f} 0"])
        project.add(track)
        source = Source(file=str(src.resolve()))
        if source.name == "SOURCE SECTION" and src.suffix.lower() == ".wav":
            source.name = "SOURCE WAVE"  # reathon only maps lowercase ".wav"
        track.add(Item(source, position=0.0, length=duration))
        dynamics = None
        if mix_mode == "raw" and entry is not None:
            dynamics = {"limiter_ceiling_db": float(entry.get("limiter_ceiling_db", -0.4))}
            if entry.get("threshold_db") is not None:
                dynamics.update(
                    threshold_db=float(entry["threshold_db"]),
                    ratio=float(entry.get("ratio", 1.2)),
                    attack=float(entry.get("attack", 5.0)),
                    release=float(entry.get("release", 120.0)),
                )
        manifest_tracks.append(
            {
                "kind": "actor",
                "name": label,
                "actor": actor,
                "role": role,
                "source": str(src.resolve()),
                "volume_gain_db": gain,
                "dynamics": dynamics,
                **flags,
                "duration_s": round(duration, 3),
            }
        )

    for label, name in (("背景-音乐", "music"), ("背景-音效", "effect")):
        src = stems[name]
        duration, _, _, _ = probe_audio(src)
        track = Track(name=label)
        project.add(track)
        track.add(Item(Source(file=str(src.resolve())), position=0.0, length=duration))
        manifest_tracks.append(
            {"kind": "background", "name": label, "source": str(src.resolve()),
             "duration_s": round(duration, 3)}
        )

    dialog = stems["dialog"]
    duration, _, _, _ = probe_audio(dialog)
    ref_track = Track(name="参考-原片对白（静音）")
    ref_track.props.append(["MUTE", "1 <1"])
    project.add(ref_track)
    ref_track.add(Item(Source(file=str(dialog.resolve())), position=0.0, length=duration))
    manifest_tracks.append(
        {"kind": "reference", "name": "参考-原片对白（静音）",
         "source": str(dialog.resolve()), "muted": True, "duration_s": round(duration, 3)}
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        project.write(str(out_path))
    except PermissionError:
        raise SystemExit(
            f"无法写入 {out_path}\n"
            "该文件可能正被 Reaper 打开——请先关闭工程，或换 --out 路径。"
        )
    return {"project": str(out_path.resolve()), "sample_rate": SAMPLE_RATE,
            "tracks": manifest_tracks}


def verify_rpp(out_path: Path, expected_tracks: int, expected_volpan: int = 0) -> None:
    text = out_path.read_text(encoding="utf-8")
    track_blocks = text.count("<TRACK")
    assert track_blocks == expected_tracks, f"expected {expected_tracks} TRACKs, got {track_blocks}"
    assert text.count("<ITEM") == expected_tracks, "each track must have exactly one ITEM"
    assert "SOURCE WAVE" in text and "POSITION 0.0" in text, "item layout unexpected"
    assert "SAMPLERATE 44100" in text, "project sample rate must be 44100"
    volpan = text.count("VOLPAN ")
    assert volpan == expected_volpan, f"expected {expected_volpan} VOLPAN lines, got {volpan}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actors", nargs="+", default=[str(DEFAULT_ACTOR_DIR)],
                        help="actor dir or explicit .wav files")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--mix-mode", choices=["files", "raw"], default="raw",
                        help="raw: enhanced/original takes + track-volume gains, "
                             "compression via apply_dynamics.lua (default, non-destructive); "
                             "files: use rendered mastered wavs (deterministic)")
    args = parser.parse_args()

    files: list[Path] = []
    for item in args.actors:
        p = Path(item)
        if p.is_dir():
            files.extend(collect_actors(p))
        elif p.is_file():
            files.append(p)
    files = sorted(set(files), key=lambda p: p.name.lower())

    manifest = build_project(files, Path(args.out), args.mix_mode)
    expected_volpan = sum(
        1 for t in manifest["tracks"] if t.get("volume_gain_db") is not None
    )
    verify_rpp(Path(args.out), expected_tracks=len(manifest["tracks"]),
               expected_volpan=expected_volpan)

    manifest_path = Path(args.out).with_suffix(".json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"project written: {manifest['project']}")
    print(f"tracks: {len(manifest['tracks'])}  manifest: {manifest_path}")
    if args.mix_mode == "raw":
        dyn_path = write_dynamics_lua(Path(args.out), manifest["tracks"])
        print(f"dynamics: {dyn_path}")
    for t in manifest["tracks"]:
        note = ""
        if t.get("mastered"):
            note += " (mastered: 压缩+响度对齐已渲染)"
        if t.get("enhanced"):
            note += " (RE-USE enhanced)"
        if t.get("volume_gain_db") is not None:
            note += f" (轨道音量 {t['volume_gain_db']:+.1f} dB)"
        if t.get("dynamics"):
            note += " (压缩/限制参数已写入 dynamics)"
        print(f"  - {t['name']}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
