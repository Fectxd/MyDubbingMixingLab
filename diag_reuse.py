"""Diagnose RE-USE GPU memory usage step by step (run inside Colab after pull)."""

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path("vendor/reuse").resolve()))
from huggingface_hub import hf_hub_download

from reuse_models.generator_SEMamba_time_d4 import SEMamba
from reuse_models.stfts import mag_phase_stft


def make_even(v: int) -> int:
    v = int(round(v))
    return v if v % 2 == 0 else v + 1


torch.cuda.empty_cache()
cfg_path = hf_hub_download("nvidia/RE-USE", "config.json", cache_dir="models/hf")
cfg = json.load(open(cfg_path))
m = SEMamba.from_pretrained("nvidia/RE-USE", cfg=cfg, cache_dir="models/hf").cuda().eval()
print("params MB:", round(sum(p.numel() for p in m.parameters()) * 4 / 1e6, 1), flush=True)
print("after load alloc GB:", round(torch.cuda.memory_allocated() / 1e9, 2), flush=True)

sr = 44100
chunk_secs = 0.6
x = torch.zeros(1, int(sr * chunk_secs), device="cuda")
stft_cfg = cfg["stft_cfg"]
n_fft = make_even(stft_cfg["n_fft"] * sr // stft_cfg["sampling_rate"])
hop = make_even(stft_cfg["hop_size"] * sr // stft_cfg["sampling_rate"])
win = make_even(stft_cfg["win_size"] * sr // stft_cfg["sampling_rate"])
compress = cfg["model_cfg"]["compress_factor"]

with torch.no_grad():
    torch.cuda.reset_peak_memory_stats()
    mag, pha, _ = mag_phase_stft(
        x, n_fft=n_fft, hop_size=hop, win_size=win,
        compress_factor=compress, center=True, addeps=False,
    )
    print("mag shape:", tuple(mag.shape), "after stft alloc GB:", round(torch.cuda.memory_allocated() / 1e9, 2), flush=True)
    try:
        amp_g, pha_g, _ = m(mag, pha)
        print("after model alloc GB:", round(torch.cuda.memory_allocated() / 1e9, 2), flush=True)
    except torch.cuda.OutOfMemoryError:
        print("model forward OOM; peak GB:", round(torch.cuda.max_memory_allocated() / 1e9, 2), flush=True)
print("DONE", flush=True)
