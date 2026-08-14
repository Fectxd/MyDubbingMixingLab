"""Assemble a Reaper project from aligned actor takes + TIGER-DnR stems.

The five actor dry takes in ``test/`` are already aligned to the picture, so
this script simply places every whole take at position 0 on its own track,
adds the separated music/effect stems as background tracks, and keeps the
separated dialogue as a muted QC reference track.

Usage:
    python assemble_rpp.py --actors test
    python assemble_rpp.py --actors "test/5*.wav" --out work/reaper/EP05_配音工程.rpp
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
PROCESSED_DIR = PROJECT_ROOT / "work" / "processed"
ENHANCED_DIR = PROJECT_ROOT / "work" / "enhanced"
MASTERED_DIR = PROJECT_ROOT / "work" / "mastered"
DEFAULT_OUT = PROJECT_ROOT / "work" / "reaper" / "EP05_配音工程.rpp"
SAMPLE_RATE = 44100


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


def normalize(path: Path) -> tuple[Path, bool]:
    """Return a 44.1k/16-bit/stereo WAV for the take, converting only if needed."""
    duration, sr, channels, _ = probe_audio(path)
    if sr == SAMPLE_RATE and channels == 2:
        return path, False
    out = PROCESSED_DIR / f"{path.stem}.wav"
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    # Always reconvert (stale-cache bug) and never use ffmpeg's implicit
    # mono->stereo matrix (it applies -3 dB); duplicate the channel explicitly.
    filters = []
    if channels == 1:
        filters.append("pan=stereo|c0=c0|c1=c0")
    if sr != SAMPLE_RATE:
        filters.append(f"aresample={SAMPLE_RATE}")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(path),
        "-sample_fmt",
        "s16",
    ]
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd.append(str(out))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed on {path}:\n{proc.stderr}")
    return out, True


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


def build_project(actor_files: list[Path], out_path: Path) -> dict:
    stems = find_stems(SEPARATED_DIR)
    missing = [str(p) for p in stems.values() if not p.exists()]
    if missing:
        raise SystemExit(
            "separated stems not found, run separate.py first:\n  " + "\n  ".join(missing)
        )

    project = Project(SAMPLERATE=SAMPLE_RATE)
    manifest_tracks = []

    for file in actor_files:
        actor, role = parse_actor_role(file.stem)
        label = f"{actor} ({role})" if role else actor
        mastered_path = MASTERED_DIR / f"{file.stem}.wav"
        enhanced_path = ENHANCED_DIR / f"{file.stem}.wav"
        if mastered_path.exists():
            src, converted = normalize(mastered_path)
            flags = {"mastered": True, "enhanced": False}
        elif enhanced_path.exists():
            src, converted = normalize(enhanced_path)
            flags = {"mastered": False, "enhanced": True}
        else:
            src, converted = normalize(file)
            flags = {"mastered": False, "enhanced": False}
        duration, _, _, _ = probe_audio(src)
        track = Track(name=label)
        project.add(track)
        source = Source(file=str(src.resolve()))
        if source.name == "SOURCE SECTION" and src.suffix.lower() == ".wav":
            source.name = "SOURCE WAVE"  # reathon only maps lowercase ".wav"
        track.add(Item(source, position=0.0, length=duration))
        manifest_tracks.append(
            {
                "kind": "actor",
                "name": label,
                "actor": actor,
                "role": role,
                "source": str(src.resolve()),
                "converted": converted,
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
    project.write(str(out_path))
    return {"project": str(out_path.resolve()), "sample_rate": SAMPLE_RATE,
            "tracks": manifest_tracks}


def verify_rpp(out_path: Path, expected_tracks: int) -> None:
    text = out_path.read_text(encoding="utf-8")
    track_blocks = text.count("<TRACK")
    assert track_blocks == expected_tracks, f"expected {expected_tracks} TRACKs, got {track_blocks}"
    assert text.count("<ITEM") == expected_tracks, "each track must have exactly one ITEM"
    assert "SOURCE WAVE" in text and "POSITION 0.0" in text, "item layout unexpected"
    assert "SAMPLERATE 44100" in text, "project sample rate must be 44100"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actors", nargs="+", default=[str(DEFAULT_ACTOR_DIR)],
                        help="actor dir or explicit .wav files")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    files: list[Path] = []
    for item in args.actors:
        p = Path(item)
        if p.is_dir():
            files.extend(collect_actors(p))
        elif p.is_file():
            files.append(p)
    files = sorted(set(files), key=lambda p: p.name.lower())

    manifest = build_project(files, Path(args.out))
    verify_rpp(Path(args.out), expected_tracks=len(manifest["tracks"]))

    manifest_path = Path(args.out).with_suffix(".json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"project written: {manifest['project']}")
    print(f"tracks: {len(manifest['tracks'])}  manifest: {manifest_path}")
    for t in manifest["tracks"]:
        note = ""
        if t.get("mastered"):
            note += " (mastered: 压缩+响度对齐)"
        if t.get("enhanced"):
            note += " (RE-USE enhanced)"
        if t.get("converted"):
            note += " (converted)"
        print(f"  - {t['name']}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
