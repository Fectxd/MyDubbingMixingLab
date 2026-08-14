"""Compress and loudness-match actor tracks to a reference dialogue track.

For every actor take the best available source (RE-USE enhanced if present,
otherwise the original dry take) and:
  1. apply dynamic compression (ffmpeg acomressor, dialogue-friendly defaults)
  2. measure integrated loudness (pyloudnorm, ITU-R BS.1770)
  3. apply gain so the take matches the reference dialogue's loudness
     (default reference: work/separated/原片_dialog.wav, the separated
     original dialogue; override with --reference or use --target-lufs)

Output goes to work/mastered/, which assemble_rpp.py uses in preference over
enhanced/original tracks.

Usage:
    python master.py --actors test --reference work/separated/原片_dialog.wav
    python master.py --actors test --target-lufs -23 --no-compress
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf


PROJECT_ROOT = Path(__file__).resolve().parent
ENHANCED_DIR = PROJECT_ROOT / "work" / "enhanced"
DEFAULT_REF = PROJECT_ROOT / "work" / "separated" / "原片_dialog.wav"


def keep_system_awake(enable: bool = True) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ES_CONTINUOUS = 0x80000000
        flags = ES_CONTINUOUS
        if enable:
            flags |= 0x00000001 | 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + (proc.stderr or proc.stdout))


def collect_actors(items: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in items:
        p = Path(item)
        if p.is_dir():
            files.extend(f for f in p.iterdir() if f.suffix.lower() == ".wav")
        elif p.is_file():
            files.append(p)
    return sorted(set(files), key=lambda p: p.name.lower())


def pick_source(take: Path) -> tuple[Path, str]:
    enhanced = ENHANCED_DIR / f"{take.stem}.wav"
    if enhanced.exists():
        return enhanced, "enhanced"
    return take, "original"


def measure_lufs(path: Path) -> float:
    data, rate = sf.read(str(path), always_2d=True, dtype="float32")
    mono = data.mean(axis=1)
    meter = pyln.Meter(rate)
    return float(meter.integrated_loudness(mono))


def compress(src: Path, dst: Path, threshold: float, ratio: float, attack: float, release: float) -> None:
    run_ffmpeg(
        [
            "-i", str(src),
            "-af",
            f"acompressor=threshold={threshold}:ratio={ratio}:attack={attack}:release={release}",
            "-c:a", "pcm_s16le",
            str(dst),
        ]
    )


def apply_gain_and_save(compressed: Path, out: Path, gain_db: float) -> dict:
    data, rate = sf.read(str(compressed), always_2d=True, dtype="float32")
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    capped = False
    if peak > 1e-12:
        gain_cap = 20.0 * np.log10(0.99 / peak)
        if gain_db > gain_cap:
            gain_db = float(gain_cap)
            capped = True
    data = data * float(10 ** (gain_db / 20.0))
    sf.write(str(out), data, rate, subtype="PCM_16")
    return {"gain_db": round(gain_db, 2), "gain_capped": capped, "peak": round(float(np.max(np.abs(data))), 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actors", nargs="+", required=True, help="dry-take wav files or a folder")
    parser.add_argument("--reference", default=str(DEFAULT_REF), help="reference dialogue wav")
    parser.add_argument("--target-lufs", type=float, default=None, help="fixed target instead of reference")
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "work" / "mastered"))
    parser.add_argument("--threshold", type=float, default=0.1, help="compressor threshold, linear (0.1 = -20 dB)")
    parser.add_argument("--ratio", type=float, default=3.0, help="compressor ratio")
    parser.add_argument("--attack", type=float, default=5.0, help="attack ms")
    parser.add_argument("--release", type=float, default=120.0, help="release ms")
    parser.add_argument("--no-compress", action="store_true", help="skip the compressor, only loudness match")
    args = parser.parse_args()

    keep_system_awake(True)
    takes = collect_actors(args.actors)
    if not takes:
        print("no actor wav files found")
        return 1

    ref = Path(args.reference)
    if args.target_lufs is not None:
        target = float(args.target_lufs)
        ref_lufs = None
        print(f"目标响度: {target:.1f} LUFS（固定值）", flush=True)
    else:
        if not ref.exists():
            raise SystemExit(f"reference not found: {ref}")
        target = measure_lufs(ref)
        ref_lufs = target
        print(f"参照轨: {ref.name}  integrated loudness {target:.1f} LUFS", flush=True)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tmpdir = outdir.parent / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)

    print(f"压缩: {'关' if args.no_compress else f'开 (threshold {args.threshold:.2f}, ratio {args.ratio:.0f}:1, attack {args.attack:.0f}ms, release {args.release:.0f}ms)'}", flush=True)
    results = {"reference": str(ref) if ref_lufs is not None else None,
               "target_lufs": round(target, 2), "tracks": []}
    for take in takes:
        src, kind = pick_source(take)
        out = outdir / f"{take.stem}.wav"
        if out.exists():
            print(f"     skip {take.name} (already mastered)", flush=True)
            results["tracks"].append({"name": take.name, "status": "skipped"})
            continue
        t0 = time.time()
        tmp = tmpdir / f"c_{take.stem}.wav"
        if args.no_compress:
            run_ffmpeg(["-i", str(src), "-c:a", "pcm_s16le", str(tmp)])
        else:
            compress(src, tmp, args.threshold, args.ratio, args.attack, args.release)
        measured = measure_lufs(tmp)
        gain = target - measured
        info = apply_gain_and_save(tmp, out, gain)
        tmp.unlink(missing_ok=True)
        results["tracks"].append({
            "name": take.name, "source_kind": kind, "source": str(src),
            "measured_lufs": round(measured, 2), **info,
        })
        note = " (gain capped)" if info["gain_capped"] else ""
        print(f"     {take.name}: {measured:.1f} -> {target:.1f} LUFS, gain {info['gain_db']:+.1f} dB{note} ({time.time() - t0:.0f}s)", flush=True)

    report = outdir / "master_report.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done. outputs in {outdir}  report: {report}", flush=True)
    keep_system_awake(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
