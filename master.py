"""Leveling compression + loudness match to a reference dialogue track.

The goal is not just to tame loud peaks but to *pull quiet lines up*: dry
takes (especially phone/condenser mics) often have whole phrases sitting far
below the rest. Per take we:
  1. measure the short-term dynamics (p95-p5 of 100ms block levels)
  2. compress with a low threshold (around the 15th percentile of speech
     blocks) and a moderate ratio, so loud parts are brought down and the
     following makeup gain lifts quiet parts with them
  3. loudness-match the active speech (silence-gated) to the reference's
     active speech loudness
  4. final alimiter catches any remaining peaks

The loudness gain is published in master_report.json; assemble_rpp.py (raw
mode) applies it as a REAPER track-fader gain instead of touching the files,
and scripts/apply_dynamics.lua recreates the same compressor settings inside
REAPER (auto makeup off, fader does the lifting).

Output goes to work/mastered/, used by assemble_rpp.py in preference to
enhanced/original tracks. The loudness gain is also published in
master_report.json, where assemble_rpp.py (raw mode) applies it as a REAPER
track-fader gain instead of touching the files.

Usage:
    python master.py --actors test --reference work/separated/原片_dialog.wav
    python master.py --actors test --target-lufs -23 --target-dr 12
    python master.py --actors test --no-compress
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
BLOCK_SECS = 0.1
SPEECH_GATE_DB = -45.0  # 100ms blocks above this RMS are treated as speech


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


def measure_stats(path: Path) -> dict:
    """Speech-gated dynamics + loudness for a wav.

    Only 100ms blocks above SPEECH_GATE_DB are counted, so long silences in
    sparse takes don't drag the average down (this is what made Adrian's hot
    voice level invisible before).
    """
    data, rate = sf.read(str(path), always_2d=True, dtype="float32")
    mono = data.mean(axis=1)
    win = int(rate * BLOCK_SECS)
    n = len(mono) // win
    frames = mono[: n * win].reshape(n, win)
    rms = np.sqrt((frames**2).mean(axis=1) + 1e-12)
    rms_db = 20.0 * np.log10(rms)
    speech = rms_db > SPEECH_GATE_DB
    if speech.sum() < 20:
        # degenerate case: use the loudest 5% of blocks instead
        speech = np.zeros_like(rms_db, dtype=bool)
        k = max(1, int(n * 0.05))
        speech[np.argsort(rms_db)[-k:]] = True
    p5, p15, p50, p85, p95 = np.percentile(rms_db[speech], [5, 15, 50, 85, 95])
    meter = pyln.Meter(rate)
    speech_lufs = float(meter.integrated_loudness(frames[speech].reshape(-1)))
    return {
        "dr": float(p95 - p5),
        "p15": float(p15),
        "p50": float(p50),
        "p85": float(p85),
        "speech_lufs": speech_lufs,
        "density": float(speech.mean()),
        "rate": rate,
    }


def compress(src: Path, dst: Path, threshold: float, ratio: float, attack: float, release: float) -> None:
    run_ffmpeg(
        [
            "-i", str(src),
            "-af",
            f"acompressor=threshold={threshold:.5f}:ratio={ratio:.2f}:attack={attack:.0f}:release={release:.0f}",
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
    return {"gain_db": round(gain_db, 2), "gain_capped": capped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actors", nargs="+", required=True, help="dry-take wav files or a folder")
    parser.add_argument("--reference", default=str(DEFAULT_REF), help="reference dialogue wav")
    parser.add_argument("--target-lufs", type=float, default=None, help="fixed loudness target (default: reference)")
    parser.add_argument("--target-dr", type=float, default=None, help="fixed dynamic-range target in dB (default: reference)")
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "work" / "mastered"))
    parser.add_argument("--ratio", type=float, default=None, help="override adaptive ratio")
    parser.add_argument("--threshold", type=float, default=None, help="override compressor threshold (linear)")
    parser.add_argument("--limiter-limit", type=float, default=0.95, help="limiter ceiling (linear, 0.95 = -0.4 dBFS)")
    parser.add_argument("--no-limiter", action="store_true", help="skip the final limiter")
    parser.add_argument("--attack", type=float, default=15.0, help="attack ms")
    parser.add_argument("--release", type=float, default=200.0, help="release ms")
    parser.add_argument("--no-compress", action="store_true", help="skip compression entirely")
    args = parser.parse_args()

    keep_system_awake(True)
    takes = collect_actors(args.actors)
    if not takes:
        print("no actor wav files found")
        return 1

    ref = Path(args.reference)
    if args.target_lufs is not None or args.target_dr is not None:
        target_lufs = args.target_lufs if args.target_lufs is not None else -23.0
        target_dr = args.target_dr if args.target_dr is not None else 12.0
        ref_stats = None
        print(f"固定目标: 响度 {target_lufs:.1f} LUFS, 动态范围 {target_dr:.0f} dB", flush=True)
    else:
        if not ref.exists():
            raise SystemExit(f"reference not found: {ref}")
        ref_stats = measure_stats(ref)
        target_lufs = ref_stats["speech_lufs"]
        target_dr = ref_stats["dr"]
        print(f"参照轨: {ref.name}  响度 {target_lufs:.1f} LUFS  动态范围 {target_dr:.1f} dB", flush=True)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tmpdir = outdir.parent / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)

    results = {"reference": str(ref) if ref_stats is not None else None,
               "target_lufs": round(target_lufs, 2), "target_dr": round(target_dr, 1), "tracks": []}
    report_path = outdir / "master_report.json"
    prev_entries: dict[str, dict] = {}
    if report_path.exists():
        try:
            prev_entries = {
                t["name"]: t for t in json.loads(
                    report_path.read_text(encoding="utf-8")
                ).get("tracks", [])
            }
        except Exception:
            prev_entries = {}
    for take in takes:
        src, kind = pick_source(take)
        out = outdir / f"{take.stem}.wav"
        if out.exists():
            print(f"     skip {take.name} (already mastered)", flush=True)
            results["tracks"].append(
                prev_entries.get(take.name)
                or prev_entries.get(take.stem)
                or {"name": take.name, "status": "skipped"}
            )
            continue
        t0 = time.time()
        st = measure_stats(src)
        tmp = tmpdir / f"c_{take.stem}.wav"
        compress_info = {}
        if args.no_compress:
            run_ffmpeg(["-i", str(src), "-c:a", "pcm_s16le", str(tmp)])
            compress_info["compressed"] = False
        else:
            # Low threshold (~p15 of speech blocks) catches quiet phrases;
            # the loudness-match gain applied afterwards lifts everything,
            # so the quiet-loud gap shrinks instead of only shaving peaks.
            threshold_db = max(-48.0, min(-12.0, st["p15"]))
            threshold = float(10 ** (threshold_db / 20.0))
            ratio = args.ratio if args.ratio is not None else min(
                4.0, max(1.5, 1.0 + (st["dr"] - 8.0) / 8.0)
            )
            compress(src, tmp, threshold, ratio, args.attack, args.release)
            compress_info = {"compressed": True, "threshold": round(threshold, 4),
                             "threshold_db": round(threshold_db, 1), "ratio": round(ratio, 2),
                             "attack": round(args.attack, 1), "release": round(args.release, 1)}
        measured = measure_stats(tmp)
        gain = target_lufs - measured["speech_lufs"]
        info = apply_gain_and_save(tmp, out, gain)
        if not args.no_limiter:
            lim_tmp = tmpdir / f"l_{take.stem}.wav"
            run_ffmpeg(
                [
                    "-i", str(out),
                    "-af", f"alimiter=limit={args.limiter_limit}:level=false:attack=5:release=50",
                    "-c:a", "pcm_s16le",
                    str(lim_tmp),
                ]
            )
            lim_tmp.replace(out)
            info["limiter"] = "alimiter"
            info["limiter_ceiling_db"] = round(20.0 * np.log10(args.limiter_limit), 2)
        final = measure_stats(out)
        tmp.unlink(missing_ok=True)
        results["tracks"].append({
            "name": take.name, "source_kind": kind, "source": str(src),
            "measured_dr": round(st["dr"], 1), "target_dr": round(target_dr, 1),
            "measured_p15_db": round(st["p15"], 1), "measured_p50_db": round(st["p50"], 1),
            "measured_p85_db": round(st["p85"], 1),
            **compress_info, "measured_lufs": round(measured["speech_lufs"], 2), **info,
            "final_dr": round(final["dr"], 1), "final_lufs": round(final["speech_lufs"], 2),
            "final_speech_median_db": round(final["p50"], 1),
        })
        tag = f"压缩 {compress_info['ratio']:.1f}:1 (threshold {compress_info['threshold_db']:.0f} dB)" \
            if compress_info.get("compressed") else "不压缩"
        print(f"     {take.name}: {tag}, "
              f"DR {st['dr']:.1f}->{final['dr']:.1f} dB, "
              f"限幅 {'开' if not args.no_limiter else '关'}, 增益 {info['gain_db']:+.1f} dB "
              f"({time.time() - t0:.0f}s)", flush=True)

    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done. outputs in {outdir}  report: {report_path}", flush=True)
    keep_system_awake(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
