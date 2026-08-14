"""Assemble a Reaper project from aligned actor takes + TIGER-DnR stems.

The five actor dry takes in ``test/`` are already aligned to the picture, so
this script simply places every whole take at position 0 on its own track,
adds the separated music/effect stems as background tracks, and keeps the
separated dialogue as a muted QC reference track.

Two mix modes:

* ``files`` (default, deterministic): reference the rendered ``work/mastered/``
  wavs — compression + limiter + loudness already baked by ``master.py`` — so
  opening the project already plays the auto-leveled mix (volume alignment,
  compression and limiting are in the audio itself).
* ``raw`` (non-destructive): reference the enhanced/original dry takes and put
  the loudness match from ``master_report.json`` on the track fader (VOLPAN).
  No audio file is touched; compression/limiting can be added inside REAPER
  with ``scripts/apply_dynamics.lua``.

Neither mode converts or rewrites audio files: sample-rate and channel
differences are left to REAPER's own resampling, so sources stay untouched.

Usage:
    python assemble_rpp.py --actors test
    python assemble_rpp.py --actors "test/5*.wav" --out work/reaper/EP05_配音工程.rpp
    python assemble_rpp.py --actors test --mix-mode raw
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf

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
# 背景自动锚定：音乐平均响度压在对白下 --music-under-db，音效峰值压在对白
# 峰值下 --effect-peak-under-db（音效多为瞬态，用峰值锚定而不是平均响度）。
DEFAULT_MUSIC_UNDER_DB = 8.0
DEFAULT_EFFECT_PEAK_UNDER_DB = 3.0
BG_GAIN_CLAMP = (-12.0, 6.0)


def measure_levels(path: Path) -> tuple[float, float]:
    """Full-file integrated loudness (LUFS) and peak (dBFS) of a wav."""
    data, rate = sf.read(str(path), always_2d=True, dtype="float32")
    mono = data.mean(axis=1)
    lufs = float(pyln.Meter(rate).integrated_loudness(mono))
    peak = 20.0 * math.log10(float(np.max(np.abs(mono))) + 1e-12)
    return lufs, peak


def anchor_background_gains(dialog: Path, stems: dict[str, Path],
                            music_under_db: float,
                            effect_peak_under_db: float) -> dict[str, float]:
    """Auto-balance music/effect under the dialogue.

    music_gain makes the music bed sit ``music_under_db`` under the dialogue's
    average loudness; effect_gain makes the effect stem's PEAK sit
    ``effect_peak_under_db`` under the dialogue's peak (effects are transient-
    heavy, so anchoring their average would still leave loud hits punching
    above the dialogue). Gains are clamped to BG_GAIN_CLAMP and never boosted
    beyond +6 dB.
    """
    d_lufs, d_peak = measure_levels(dialog)
    m_lufs, _ = measure_levels(stems["music"])
    _, e_peak = measure_levels(stems["effect"])
    music_gain = (d_lufs - music_under_db) - m_lufs
    effect_gain = (d_peak - effect_peak_under_db) - e_peak
    lo, hi = BG_GAIN_CLAMP
    return {
        "music": round(min(hi, max(lo, music_gain)), 2),
        "effect": round(min(hi, max(lo, effect_gain)), 2),
    }


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


def lua_str(s: str) -> str:
    """Encode a string as a Lua string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_dynamics_lua(out_path: Path, tracks: list[dict]) -> Path:
    """Write per-track compressor/limiter settings for scripts/apply_dynamics.lua."""
    dyn_path = out_path.with_name(DYNAMICS_LUA_NAME)
    lines = [
        "-- auto-generated by assemble_rpp.py (raw non-destructive mode)",
        "-- read by scripts/apply_dynamics.lua inside REAPER",
        "return {",
    ]
    actors = []
    for t in tracks:
        if t.get("kind") == "actor":
            actors.append(lua_str(t["name"]))
        dyn = t.get("dynamics")
        if not dyn:
            continue
        fields = []
        if dyn.get("threshold_db") is not None:
            fields.append(f"threshold_db={dyn['threshold_db']:.1f}")
            fields.append(f"ratio={dyn['ratio']:.2f}")
            fields.append(f"attack={dyn['attack']:.0f}")
            fields.append(f"release={dyn['release']:.0f}")
        if dyn.get("gain_db") is not None:
            fields.append(f"gain_db={dyn['gain_db']:.2f}")
        fields.append(f"limiter_ceiling_db={dyn['limiter_ceiling_db']:.2f}")
        lines.append(f"  [{lua_str(t['name'])}] = {{ {', '.join(fields)} }},")
    if actors:
        lines.append(f"  actors = {{ {', '.join(actors)} }},")
    lines.append("}")
    dyn_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dyn_path


def build_project(actor_files: list[Path], out_path: Path, mix_mode: str,
                  voice_gain_db: float = 1.0,
                  music_under_db: float = DEFAULT_MUSIC_UNDER_DB,
                  effect_peak_under_db: float = DEFAULT_EFFECT_PEAK_UNDER_DB) -> dict:
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
            # 人声组整体增益：统一加到轨道音量（VOLPAN），成片用同参数保持一致
            gain = voice_gain_db if voice_gain_db else None
            voice_gain = gain
        else:
            # Non-destructive: reference the take as-is (REAPER resamples
            # 48k and handles mono sources natively), loudness on the fader.
            src = enhanced_path if enhanced_path.exists() else file
            flags = {"mastered": False, "enhanced": enhanced_path.exists()}
            gain = float(entry["gain_db"]) if entry and "gain_db" in entry else None
            voice_gain = None
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
            if entry.get("gain_db") is not None:
                dynamics["gain_db"] = float(entry["gain_db"])
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
                "position": 0.0,
                "volume_gain_db": gain if mix_mode == "raw" else None,
                "voice_gain_db": voice_gain,
                "dynamics": dynamics,
                **flags,
                "duration_s": round(duration, 3),
            }
        )

    # 背景自动锚定：音乐/音效压在对白之下（见 anchor_background_gains）
    bg_gains = anchor_background_gains(stems["dialog"], stems,
                                       music_under_db, effect_peak_under_db)
    for label, name in (("背景-音乐", "music"), ("背景-音效", "effect")):
        src = stems[name]
        duration, _, _, _ = probe_audio(src)
        track = Track(name=label)
        # 4 channels so the dialogue-trigger sidechain can land on 3/4.
        # Standard REAPER token is "NCHAN" ("I_NCHAN" is not recognized).
        track.props.append(["NCHAN", "4"])
        g = bg_gains[name]
        if g:
            track.props.append(["VOLPAN", f"{10 ** (g / 20.0):.4f} 0"])
        project.add(track)
        track.add(Item(Source(file=str(src.resolve())), position=0.0, length=duration))
        manifest_tracks.append(
            {"kind": "background", "name": label, "source": str(src.resolve()),
             "position": 0.0, "duration_s": round(duration, 3), "channels": 4,
             "gain_db": g}
        )

    dialog = stems["dialog"]
    duration, _, _, _ = probe_audio(dialog)
    ref_track = Track(name="参考-原片对白（静音）")
    project.add(ref_track)
    ref_item = Item(Source(file=str(dialog.resolve())), position=0.0, length=duration)
    # Track-level "MUTE" is not a valid chunk token in modern REAPER (it only
    # parses inside <ITEM>). Item mute is: MUTE <muted> <automation-state>,
    # so "1 0" = muted without automation — matches what REAPER itself writes.
    ref_item.props.append(["MUTE", "1 0"])
    ref_track.add(ref_item)
    manifest_tracks.append(
        {"kind": "reference", "name": "参考-原片对白（静音）",
         "source": str(dialog.resolve()), "muted": True, "position": 0.0,
         "duration_s": round(duration, 3)}
    )

    # Grouped sidechain trigger bus: the 5 actor tracks feed it, and it feeds
    # the background tracks' channels 3/4; ReaComp on the background tracks
    # then ducks whenever any actor speaks.  Master send is off so the trigger
    # itself is inaudible (scripts/apply_sidechain.lua wires the sends inside
    # REAPER, keeping the .rpp simple and robust).
    bus = Track(name="对白触发")
    # MAINSEND <send> <automation-state>; "0 0" = no send to master.
    # "B_MAINSEND" is a legacy token modern REAPER no longer recognizes.
    bus.props.append(["MAINSEND", "0 0"])
    project.add(bus)
    manifest_tracks.append(
        {"kind": "trigger_bus", "name": "对白触发",
         "muted_to_master": True, "note": "侧链触发总线（无声）"}
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
    assert text.count("<ITEM") == expected_tracks - 1, "bus track has no ITEM; every other track exactly one"
    assert "SOURCE WAVE" in text and "POSITION 0.0" in text, "item layout unexpected"
    assert "SAMPLERATE 44100" in text, "project sample rate must be 44100"
    volpan = text.count("VOLPAN ")
    assert volpan == expected_volpan, f"expected {expected_volpan} VOLPAN lines, got {volpan}"
    assert text.count("NCHAN 4") == 2, "both background tracks must have 4 channels"
    assert text.count("MUTE 1 0") == 1, "reference dialogue item must be muted"
    assert "MAINSEND 0 0" in text, "trigger bus must not feed the master"
    for bad in ("I_NCHAN", "B_MAINSEND", "MUTE 1 <1"):
        assert bad not in text, f"legacy/invalid token {bad!r} must not be emitted"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actors", nargs="+", default=[str(DEFAULT_ACTOR_DIR)],
                        help="actor dir or explicit .wav files")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--mix-mode", choices=["files", "raw"], default="files",
                        help="files: use rendered mastered wavs (compression + "
                             "limiter + loudness baked, deterministic, default); "
                             "raw: enhanced/original takes + track-volume gains, "
                             "compression via apply_dynamics.lua (adjustable)")
    parser.add_argument("--voice-gain-db", type=float, default=1.0,
                        help="人声组整体增益 dB（files 模式写到轨道音量，成片用 "
                             "merge_video.py 同参数保持一致；0 关闭，默认 1.0）")
    parser.add_argument("--music-under-db", type=float, default=DEFAULT_MUSIC_UNDER_DB,
                        help="音乐平均响度压在对白下多少 dB（默认 8，越温和设越小）")
    parser.add_argument("--effect-peak-under-db", type=float, default=DEFAULT_EFFECT_PEAK_UNDER_DB,
                        help="音效峰值压在对白峰值下多少 dB（默认 3，越温和设越小）")
    args = parser.parse_args()

    files: list[Path] = []
    for item in args.actors:
        p = Path(item)
        if p.is_dir():
            files.extend(collect_actors(p))
        elif p.is_file():
            files.append(p)
    files = sorted(set(files), key=lambda p: p.name.lower())

    manifest = build_project(files, Path(args.out), args.mix_mode,
                             voice_gain_db=args.voice_gain_db,
                             music_under_db=args.music_under_db,
                             effect_peak_under_db=args.effect_peak_under_db)
    expected_volpan = sum(
        1 for t in manifest["tracks"]
        if t.get("volume_gain_db") is not None or t.get("voice_gain_db") is not None
        or t.get("gain_db")
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
        if t.get("voice_gain_db") is not None:
            note += f" (人声组 {t['voice_gain_db']:+.1f} dB)"
        if t.get("volume_gain_db") is not None:
            note += f" (轨道音量 {t['volume_gain_db']:+.1f} dB)"
        if t.get("dynamics"):
            note += " (压缩/限制参数已写入 dynamics)"
        print(f"  - {t['name']}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
