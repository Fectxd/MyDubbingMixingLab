"""Run the whole dubbing pipeline on one machine.

Steps: ① separate the picture (TIGER-DnR) -> ② enhance dry takes (NVIDIA
RE-USE, skipped with a note if mamba-ssm is unavailable) -> ③ analyse
loudness/dynamics against the original dialogue (master.py) -> ④ assemble
the Reaper project (files mode: mastered wavs) -> ⑤ merge the mix with the
original video (audio cut to the original-audio window = video duration).

Usage:
    python run_all.py
    python run_all.py --video 原片.mp4 --chunk 6
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PY = sys.executable
SKIP_DIRS = {"work", "models", "vendor", ".git", ".venv", "__pycache__"}


def run(step: str, cmd: list[str]) -> None:
    print(f"\n===== {step} =====", flush=True)
    subprocess.run([PY, *cmd], cwd=ROOT, check=True)


def is_skipped(p: Path) -> bool:
    try:
        rel = p.relative_to(ROOT)
    except ValueError:
        return False
    return any(part in SKIP_DIRS for part in rel.parts[:-1])


def find_video(path: str | None) -> Path:
    if path and Path(path).exists():
        return Path(path)
    for ext in ("*.mp4", "*.mov", "*.mkv", "*.m4a", "*.MP4", "*.MOV", "*.MKV"):
        hits = sorted(ROOT.glob(ext))
        if hits:
            return hits[0]
    raise SystemExit("no video/audio found; put the picture file next to run_all.py or pass --video")


def find_actor_wavs(items: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in items:
        p = Path(item)
        if p.is_dir():
            files.extend(f for f in p.iterdir() if f.suffix.lower() == ".wav")
        elif p.is_file():
            files.append(p)
    if not files:
        files = [f for f in ROOT.iterdir() if f.suffix.lower() == ".wav"]
    files = [f for f in files if f.exists() and not is_skipped(f)]
    return sorted(set(files), key=lambda p: p.name.lower())


def reuse_available() -> bool:
    out = subprocess.run(
        [PY, "-c", "import importlib.util; print(importlib.util.find_spec('mamba_ssm') is not None)"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() == "True"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", default=None, help="picture file (auto-detect if omitted)")
    parser.add_argument("--actors", nargs="+", default=[], help="dry-take wav files or a folder")
    parser.add_argument("--chunk", type=float, default=12.0, help="TIGER window seconds (3GB cards: use 6)")
    parser.add_argument("--skip-enhance", action="store_true", help="skip the RE-USE repair step")
    args = parser.parse_args()

    video = find_video(args.video)
    actors = find_actor_wavs(args.actors)
    if not actors:
        raise SystemExit("no actor wav files found next to the script")
    print(f"video : {video.name}", flush=True)
    print(f"actors: {[f.name for f in actors]}", flush=True)

    run("① 分离原片 (TIGER-DnR)", ["separate.py", "--input", str(video), "--chunk", str(args.chunk), "--device", "auto"])

    if not args.skip_enhance:
        if reuse_available():
            run("② 修复干声 (NVIDIA RE-USE)", ["enhance.py", "--inputs", *[str(f) for f in actors]])
        else:
            print("\n===== ② 修复干声 (NVIDIA RE-USE) 已跳过 =====", flush=True)
            print("本机缺少 mamba-ssm（RE-USE 依赖，要求 CUDA 11.8+，你的驱动只到 CUDA 11.4），", flush=True)
            print("这一步无法本地跑。选项：a) 用 colab_full_pipeline.ipynb 在免费 GPU 跑修复；", flush=True)
            print("b) 让我接一个不需要新驱动的本地降噪模型（如 DeepFilterNet）。", flush=True)
    else:
        print("\n===== ② 修复干声 已按 --skip-enhance 跳过 =====", flush=True)

    run("③ 响度/动态分析 (master.py，参数写入 master_report.json)",
        ["master.py", "--actors", *[str(f) for f in actors]])

    run("④ 排 Reaper 工程 (默认 files 模式：引用 mastered 成品)",
        ["assemble_rpp.py", "--actors", *[str(f) for f in actors]])

    run("⑤ 合并成片 (混音 + 原视频，音频取原音窗口、与视频等长)",
        ["merge_video.py", "--video", str(video)])

    print("\n全部完成！产物：", flush=True)
    print("  分离  : work/separated/（对白/音效/音乐 + 参考混音 + report）", flush=True)
    print("  修复  : work/enhanced/（如执行了 RE-USE）", flush=True)
    print("  分析  : work/mastered/master_report.json（各轨增益/压缩参数）", flush=True)
    print("  工程  : work/reaper/EP05_配音工程.rpp + manifest", flush=True)
    print("  成片  : work/final/EP05_配音成片.mp4（混音 + 原视频，音频与视频等长）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
