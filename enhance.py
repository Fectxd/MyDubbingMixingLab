"""Enhance low-quality dry takes with NVIDIA RE-USE (CUDA required).

RE-USE is a universal speech enhancement model (denoising, dereverberation,
bandwidth limitation, codec artifacts, low-quality mics). The official
inference code refuses CPU, so this script requires a CUDA GPU (Google Colab /
Kaggle free GPU works). Each input is downmixed to mono, enhanced in 5s
overlapping chunks (hann window overlap-add), and saved to work/enhanced/.

Usage:
    python enhance.py --inputs test/*.wav --outdir work/enhanced
    python enhance.py --inputs take1.wav take2.wav --bwe 32000

Note: RE-USE is released under NVIDIA's non-commercial license (NSCLv1).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf


PROJECT_ROOT = Path(__file__).resolve().parent
REUSE_DIR = PROJECT_ROOT / "vendor" / "reuse"
MODEL_CACHE = PROJECT_ROOT / "models" / "hf"
sys.path.insert(0, str(REUSE_DIR))

RELU = None


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


def make_even(value: int) -> int:
    value = int(round(value))
    return value if value % 2 == 0 else value + 1


def collect_inputs(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in paths:
        p = Path(item)
        if p.is_dir():
            files.extend(
                sorted(
                    f
                    for f in p.iterdir()
                    if f.suffix.lower() in (".wav", ".flac", ".mp3", ".m4a", ".mp4")
                )
            )
        elif p.is_file():
            files.append(p)
    return sorted(set(files), key=lambda p: p.name.lower())


def load_model(device: str):
    import torch
    import torch.nn as nn
    from huggingface_hub import hf_hub_download

    try:
        from reuse_models.generator_SEMamba_time_d4 import SEMamba
        from reuse_models.stfts import mag_phase_istft, mag_phase_stft  # noqa: F401
        from reuse_utils.util import pad_or_trim_to_match  # noqa: F401
    except ModuleNotFoundError as e:
        raise SystemExit(
            f"RE-USE 依赖缺失（{e.name}）。mamba-ssm 要求 CUDA 11.8+，老驱动机器无法本地跑；"
            "请用 colab_full_pipeline.ipynb 在免费 GPU 上跑，或改用其他降噪模型。"
        )

    global RELU
    RELU = nn.ReLU()

    cfg_path = hf_hub_download(
        repo_id="nvidia/RE-USE", filename="config.json", cache_dir=str(MODEL_CACHE)
    )
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    model = SEMamba.from_pretrained("nvidia/RE-USE", cfg=cfg, cache_dir=str(MODEL_CACHE))
    model.to(device)
    model.eval()
    return model, cfg, torch, mag_phase_stft, mag_phase_istft, pad_or_trim_to_match


def enhance_file(
    path: Path,
    out_path: Path,
    model,
    cfg: dict,
    torch,
    mag_phase_stft,
    mag_phase_istft,
    pad_or_trim_to_match,
    device: str,
    chunk_secs: float,
    hop_portion: float,
    bwe: int | None,
) -> None:
    data, sr = sf.read(str(path), always_2d=True, dtype="float32")
    noisy = data.mean(axis=1, keepdims=True).T  # downmix to mono, [1, T]
    if not (8000 <= sr <= 48000):
        raise ValueError(f"{path.name}: sample rate {sr} outside RE-USE range")

    if bwe is not None:
        raise NotImplementedError("--bwe needs librosa; not wired in this build")

    noisy_t = torch.FloatTensor(noisy).to(device)
    chunk_size = int(chunk_secs * sr)
    hop_length = int(hop_portion * chunk_size)
    window = torch.hann_window(chunk_size).to(device)

    stft_cfg = cfg["stft_cfg"]
    n_fft = make_even(stft_cfg["n_fft"] * sr // stft_cfg["sampling_rate"])
    hop = make_even(stft_cfg["hop_size"] * sr // stft_cfg["sampling_rate"])
    win = make_even(stft_cfg["win_size"] * sr // stft_cfg["sampling_rate"])
    compress = stft_cfg["compress_factor"]

    enhanced = torch.zeros_like(noisy_t)
    window_sum = torch.zeros_like(noisy_t)
    n_chunks = max(1, math.ceil((noisy_t.shape[1] - chunk_size) / hop_length) + 1)

    for i in range(n_chunks):
        chunk = noisy_t[:, i * hop_length : i * hop_length + chunk_size]
        noisy_mag, noisy_pha, _ = mag_phase_stft(
            chunk, n_fft=n_fft, hop_size=hop, win_size=win,
            compress_factor=compress, center=True, addeps=False,
        )
        amp_g, pha_g, _ = model(noisy_mag, noisy_pha)
        mag = torch.expm1(RELU(amp_g))
        zero_portion = torch.sum(mag == 0, 1) / mag.shape[1]
        amp_g[:, :, (zero_portion > 0.5)[0]] = 0
        audio_g = mag_phase_istft(
            amp_g, pha_g, n_fft, hop, win, compress
        )
        audio_g = pad_or_trim_to_match(chunk.detach(), audio_g, pad_value=1e-8)
        enhanced[:, i * hop_length : i * hop_length + chunk_size] += (
            audio_g * window[0 : audio_g.shape[1]]
        )
        window_sum[:, i * hop_length : i * hop_length + chunk_size] += window[
            0 : audio_g.shape[1]
        ]

    nonzero = window_sum > 1e-8
    enhanced[:, nonzero[0]] = enhanced[:, nonzero[0]] / window_sum[:, nonzero[0]]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), enhanced[0].detach().cpu().numpy(), sr, subtype="PCM_16")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", required=True,
                        help="wav files or a folder to enhance")
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "work" / "enhanced"))
    parser.add_argument("--chunk-size", type=float, default=5.0,
                        help="enhancement window in seconds (default 5)")
    parser.add_argument("--hop-portion", type=float, default=0.5,
                        help="hop as a fraction of the chunk (default 0.5)")
    parser.add_argument("--bwe", type=int, default=None,
                        help="optional target bandwidth for extension (not implemented)")
    args = parser.parse_args()

    import torch

    if not torch.cuda.is_available():
        raise SystemExit("RE-USE requires CUDA (official model does not support CPU). "
                         "Use Google Colab/Kaggle free GPU, or a machine with an NVIDIA GPU.")
    device = "cuda"

    keep_system_awake(True)
    print("已阻止系统休眠/熄屏（脚本运行期间）", flush=True)

    files = collect_inputs(args.inputs)
    if not files:
        print("no input files found")
        return 1

    print(f"[1/3] loading RE-USE (nvidia/RE-USE) on cuda ...", flush=True)
    model, cfg, torch, mps, mpi, pttm = load_model(device)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"[2/3] enhancing {len(files)} file(s) ...", flush=True)
    for f in files:
        out = outdir / f"{f.stem}.wav"
        if out.exists():
            print(f"     skip {f.name} (already enhanced)", flush=True)
            continue
        t0 = time.time()
        enhance_file(
            f, out, model, cfg, torch, mps, mpi, pttm, device,
            args.chunk_size, args.hop_portion, args.bwe,
        )
        print(f"     {f.name} -> {out.name} ({time.time() - t0:.1f}s)", flush=True)

    print(f"[3/3] done. enhanced files in {outdir}", flush=True)
    keep_system_awake(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
