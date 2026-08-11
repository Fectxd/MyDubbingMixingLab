"""Separate a video/audio mix into dialogue, effect and music stems with TIGER-DnR.

Usage:
    python separate.py --input test/原片.mp4
    python separate.py --input some.wav --outdir work/separated

The model (JusperLee/TIGER-DnR) is downloaded on first run from HuggingFace
into a project-local cache, so later runs work offline.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf


PROJECT_ROOT = Path(__file__).resolve().parent
VENDOR_DIR = PROJECT_ROOT / "vendor" / "tiger_lite"
sys.path.insert(0, str(VENDOR_DIR))

MODEL_ID = "JusperLee/TIGER-DnR"
MODEL_CACHE = PROJECT_ROOT / "models" / "hf"
SAMPLE_RATE = 44100

STEMS = [
    ("dialog", "对话人声"),
    ("effect", "音效"),
    ("music", "音乐"),
]


def keep_system_awake(enable: bool = True) -> None:
    """Prevent sleep/display-off while the long separation runs (Windows only).

    SetThreadExecutionState needs no admin rights and is automatically cleared
    when the process exits, so a crash or Ctrl+C leaves the machine as before.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        flags = ES_CONTINUOUS
        if enable:
            flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg and raise with stderr on failure."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed:\n" + (proc.stderr or proc.stdout or "unknown error")
        )


def decode_to_wav(input_path: Path, wav_path: Path) -> None:
    """Decode any audio/video to a 44.1 kHz stereo WAV (one lossy step only)."""
    run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "2",
            "-ar",
            str(SAMPLE_RATE),
            "-sample_fmt",
            "s16",
            str(wav_path),
        ]
    )


def load_audio(wav_path: Path) -> tuple[np.ndarray, int]:
    """Load WAV as float32 [channels, samples], ready for the model."""
    data, sr = sf.read(str(wav_path), always_2d=True, dtype="float32")
    if sr != SAMPLE_RATE:
        raise ValueError(f"expected {SAMPLE_RATE} Hz, got {sr} Hz from {wav_path}")
    return data.T, sr


def save_stem(wav_path: Path, tensor) -> None:
    """Save a [channels, samples] float tensor as a float32 WAV."""
    array = tensor.detach().cpu().numpy().T
    sf.write(str(wav_path), array, SAMPLE_RATE, subtype="FLOAT")


def load_model(device: str):
    import torch  # imported lazily so --help etc. stay light
    import look2hear.models  # noqa: F401  (registers TIGERDNR)

    model = look2hear.models.TIGERDNR.from_pretrained(
        MODEL_ID,
        cache_dir=str(MODEL_CACHE),
    )
    model.to(device)
    model.eval()
    return model, torch


def install_progress(model, total_sessions: int) -> list[dict]:
    """Print per-chunk progress while the model splits the long file.

    TIGER-DnR chops the mixture into 12s sessions with a 4s hop; each
    `forward` call below processes exactly one session, so counting calls
    gives us reliable progress for each of the three sub-models.
    """
    states = []
    for label, sub in (("对白", model.dialog), ("音效", model.effect), ("音乐", model.music)):
        original = sub.forward
        st = {"n": 0, "total": total_sessions}
        states.append(st)

        def wrapped(x, _orig=original, _label=label, _st=st):
            _st["n"] += 1
            print(f"     {_label}: 第 {_st['n']}/{_st['total']} 段", flush=True)
            return _orig(x)

        sub.forward = wrapped
    return states


def count_sessions(n_samples: int, sr: int, chunk_secs: float = 12.0, hop_secs: float = 4.0) -> int:
    """Replicate the model's chunk-count math for progress reporting."""
    session = int(sr * chunk_secs)
    hop = int(sr * hop_secs)
    padded = n_samples + 2 * (session - hop)  # hop-pad 8s on both sides
    return (padded - session) // hop + 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="video or audio file (mp4/wav/m4a/...)")
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "work" / "separated"))
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"])
    parser.add_argument(
        "--chunk",
        type=float,
        default=12.0,
        help="inference window in seconds (default 12). Longer windows give the "
             "model more context but run slower; hop is always chunk/3 so every "
             "sample is still averaged from 3 overlapping windows.",
    )
    args = parser.parse_args()

    keep_system_awake(True)
    print("已阻止系统休眠/熄屏（脚本运行期间）", flush=True)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"input not found: {input_path}")
        return 1

    outdir = Path(args.outdir)
    workdir = outdir.parent / "tmp"
    for d in (outdir, workdir, MODEL_CACHE):
        d.mkdir(parents=True, exist_ok=True)

    stem = input_path.stem
    wav_path = workdir / f"{stem}_44k_stereo.wav"
    if wav_path.exists():
        print(f"[1/3] reusing existing decoded WAV: {wav_path.name}")
    else:
        print(f"[1/3] decoding {input_path.name} -> {SAMPLE_RATE} Hz stereo WAV ...")
        decode_to_wav(input_path, wav_path)

    device = args.device
    if device == "auto":
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        import torch

        torch.backends.cudnn.benchmark = True
    print(f"[2/3] loading TIGER-DnR ({MODEL_ID}) on {device} ...")
    model, torch = load_model(device)

    audio, sr = load_audio(wav_path)
    print(
        f"     input: {audio.shape[1] / sr:.1f}s, "
        f"{audio.shape[0]} channel(s), {audio.dtype}"
    )

    mixture = torch.from_numpy(audio).to(device)  # [channels, samples]
    chunk_secs = max(6.0, min(float(args.chunk), 60.0))
    hop_secs = chunk_secs / 3.0
    total_sessions = count_sessions(audio.shape[1], sr, chunk_secs, hop_secs)
    states = install_progress(model, total_sessions)
    print(
        f"[3/3] separating ({chunk_secs:.0f}s windows x {total_sessions} chunks "
        f"x dialogue/effect/music, overlap-add averaging), "
        "saving each stem as soon as it finishes ..."
    )
    elapsed = 0.0
    stems = {}
    with torch.no_grad():
        for name, label in STEMS:
            t0 = time.time()
            idx = {"dialog": 2, "effect": 1, "music": 0}[name]
            sub = {"dialog": model.dialog, "effect": model.effect, "music": model.music}[name]
            while True:
                try:
                    track = model.wav_chunk_inference(
                        sub, mixture[None],
                        target_length=chunk_secs, hop_length=hop_secs,
                    )[idx]
                    break
                except RuntimeError as e:
                    if "out of memory" not in str(e).lower():
                        raise
                    if chunk_secs <= 3.0:
                        raise
                    chunk_secs = max(3.0, chunk_secs / 2.0)
                    hop_secs = chunk_secs / 3.0
                    for st in states:
                        st["total"] = count_sessions(audio.shape[1], sr, chunk_secs, hop_secs)
                    torch.cuda.empty_cache()
                    print(
                        f"     显存不足，窗口自动降到 {chunk_secs:.0f}s 重试 ...",
                        flush=True,
                    )
            elapsed += time.time() - t0
            out_path = outdir / f"{stem}_{name}.wav"
            save_stem(out_path, track)
            stems[name] = out_path
            print(
                f"     {label} done in {time.time() - t0:.1f}s -> {out_path.name} "
                f"({out_path.stat().st_size / 1e6:.1f} MB)",
                flush=True,
            )
            torch.cuda.empty_cache()

    results = {"input": str(input_path), "sample_rate": SAMPLE_RATE,
               "seconds": float(audio.shape[1] / sr), "elapsed_s": round(elapsed, 1),
               "device": device, "stems": {}}
    for name, label in STEMS:
        out_path = stems[name]
        results["stems"][name] = {"label": label, "path": str(out_path)}
        print(f"     {label}: {out_path.name}  ({out_path.stat().st_size / 1e6:.1f} MB)")

    report_path = outdir / f"{stem}_report.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\ndone in {elapsed:.1f}s. report: {report_path}")

    # Keep the decoded WAV around (it is the untouched 44.1k mix used later).
    mix_copy = outdir / f"{stem}_mix44k.wav"
    if not mix_copy.exists():
        shutil.copy2(wav_path, mix_copy)
    print(f"reference mix (44.1k stereo): {mix_copy}")
    keep_system_awake(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
