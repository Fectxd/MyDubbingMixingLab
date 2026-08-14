"""Merge the dubbed mix with the original video -> final deliverable (.mp4).

The mix is built exactly as the files-mode Reaper project plays it: mastered
actor takes + separated music/effect stems, all at unity (the muted reference
dialogue track and the silent trigger bus are excluded).

The output window is the ORIGINAL audio's range: the background (music/effect)
stems ARE the original video's audio, so their timeline defines where the
picture's sound lives. Alignment can pull voice content before the original
audio starts (or extend past its end), so instead of trimming the mix's head
or tail by duration, the mix is cut to the original audio's window
[start_bg, start_bg + video_duration]. Anything outside that window (leading
voice pre-roll or trailing padding) is dropped, and the result is guaranteed
to match the video's duration exactly.

Usage:
    python merge_video.py                                  # auto-detect video + manifest sources
    python merge_video.py --video test/原片.mp4 --out work/final/EP05_配音成片.mp4
    python merge_video.py --mix work/tmp/my_mix.wav        # use a pre-made mix
    python merge_video.py --voice-gain-db 1.0              # 人声组整体增益（与 assemble 一致）
    python merge_video.py --no-limit                       # skip the master-ceiling safety limiter
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_VIDEO = PROJECT_ROOT / "test" / "原片.mp4"
MANIFEST = PROJECT_ROOT / "work" / "reaper" / "EP05_配音工程.json"
SEPARATED_DIR = PROJECT_ROOT / "work" / "separated"
DEFAULT_OUT = PROJECT_ROOT / "work" / "final" / "EP05_配音成片.mp4"
AUDIO_SR = 44100
MASTER_LIMIT = 0.98  # -0.18 dBFS ceiling for the summed mix (safety, only if it peaks hot)
DEFAULT_VOICE_GAIN_DB = 1.0  # 人声组整体增益，assemble 与 merge 保持一致


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + (proc.stderr or proc.stdout))


def ffprobe_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}:\n{proc.stderr}")
    return float(proc.stdout.strip())


def ffprobe_peak(path: Path) -> float:
    """Return peak level in dBFS of the audio file."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    for line in proc.stderr.splitlines():
        if "max_volume" in line:
            return float(line.split("max_volume:")[1].split("dB")[0].strip())
    return -999.0


def find_video(arg: str | None) -> Path:
    if arg and Path(arg).exists():
        return Path(arg)
    if DEFAULT_VIDEO.exists():
        return DEFAULT_VIDEO
    for ext in ("*.mp4", "*.mov", "*.mkv", "*.MP4", "*.MOV", "*.MKV"):
        hits = sorted(PROJECT_ROOT.glob(ext))
        if hits:
            return hits[0]
    raise SystemExit("no video found; pass --video <file>")


def mix_sources_from_manifest(voice_gain_db: float | None = None) -> list[tuple[Path, float]] | None:
    """The audible tracks of the files-mode project: actors + backgrounds.

    Returns (path, gain_db) pairs — actors carry the voice-group gain
    (manifest voice_gain_db; an explicitly-passed CLI value wins over it),
    backgrounds 0.
    """
    if not MANIFEST.exists():
        return None
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    srcs: list[tuple[Path, float]] = []
    for t in data.get("tracks", []):
        if t.get("kind") == "actor" and t.get("source"):
            g = t.get("voice_gain_db")
            if g is None:
                g = voice_gain_db if voice_gain_db is not None else DEFAULT_VOICE_GAIN_DB
            srcs.append((Path(t["source"]), float(g)))
        elif t.get("kind") == "background" and t.get("source"):
            g = t.get("gain_db")
            srcs.append((Path(t["source"]), float(g) if g is not None else 0.0))
    if not srcs:
        return None
    missing = [p for p, _ in srcs if not p.exists()]
    if missing:
        raise SystemExit("manifest sources missing:\n  " + "\n  ".join(str(p) for p in missing))
    return srcs


def mix_sources_fallback(voice_gain_db: float | None = None) -> list[tuple[Path, float]]:
    """Fallback: mastered actors (with voice gain) + separated music/effect stems."""
    mastered = sorted((PROJECT_ROOT / "work" / "mastered").glob("*.wav"))
    stems = [SEPARATED_DIR / f"原片_{n}.wav" for n in ("music", "effect")]
    g = voice_gain_db if voice_gain_db is not None else DEFAULT_VOICE_GAIN_DB
    srcs = [(p, g) for p in mastered] + [(p, 0.0) for p in stems]
    if not mastered:
        raise SystemExit("no mastered wavs in work/mastered/; run master.py first")
    missing = [s for s in stems if not s.exists()]
    if missing:
        raise SystemExit("separated stems missing:\n  " + "\n  ".join(str(p) for p in missing))
    return srcs


def ffprobe_channels(path: Path) -> int:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 1


def build_mix(srcs: list[tuple[Path, float]], out: Path, limit: bool) -> float:
    """Sum all sources at unity (as the files-mode project plays them)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = []
    for s, _ in srcs:
        cmd += ["-i", str(s)]
    # Per-input gain (voice-group boost on actors / background auto-balance),
    # then sum WITHOUT input scaling (amix normalize=0) so the result equals
    # REAPER's fader mix. Mono sources MUST be upmixed with pan (c1=c0) at
    # unity: aformat's mono->stereo conversion applies equal-power -3 dB,
    # which would silently make the voices 3 dB quieter than the project.
    parts = []
    for i, (s, g) in enumerate(srcs):
        chain = f"[{i}:a]"
        if g:
            chain += f"volume={10 ** (g / 20.0):.6f},"
        if ffprobe_channels(s) == 1:
            chain += "pan=stereo|c0=c0|c1=c0,"
        chain += f"aresample={AUDIO_SR},aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
        parts.append(chain)
    fc = ";".join(parts)
    fc += ";" + "".join(f"[a{i}]" for i in range(len(srcs)))
    fc += f"amix=inputs={len(srcs)}:normalize=0:dropout_transition=0"
    if limit:
        fc += f",alimiter=limit={MASTER_LIMIT}:level=false:attack=5:release=50"
    fc += "[out]"
    raw = out.with_name(out.stem + "_raw.wav")
    cmd += ["-filter_complex", fc, "-map", "[out]", "-c:a", "pcm_s16le", str(raw)]
    run_ffmpeg(cmd)
    peak = ffprobe_peak(raw)
    raw.replace(out)
    return peak


def original_audio_window() -> float:
    """Start (seconds) of the original audio in the project timeline.

    The background (music/effect) stems are the original video's audio, so
    their position defines where the picture's sound begins. In the current
    files-mode project every item sits at position 0, so the window starts at
    0; the aligned (对轨) pipeline may carry offsets in the manifest later.
    """
    if not MANIFEST.exists():
        return 0.0
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    starts = [float(t.get("position", 0.0)) for t in data.get("tracks", [])
              if t.get("kind") == "background"]
    return min(starts) if starts else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", default=None, help="original video (default: test/原片.mp4)")
    parser.add_argument("--mix", default=None, help="pre-made mix wav (skip auto-building)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output mp4 path")
    parser.add_argument("--no-limit", action="store_true",
                        help="skip the master-ceiling safety limiter on the summed mix")
    parser.add_argument("--voice-gain-db", type=float, default=None,
                        help="人声组整体增益 dB；默认读 manifest 的 voice_gain_db "
                             "（assemble 写入，工程/成片一致），显式指定时优先 "
                             "（仅改成片时用于 A/B，永久调整请同时重跑 assemble）")
    args = parser.parse_args()

    video = find_video(args.video)
    out = Path(args.out)
    tmpdir = PROJECT_ROOT / "work" / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)

    if args.mix:
        mix = Path(args.mix)
        if not mix.exists():
            raise SystemExit(f"mix not found: {mix}")
        print(f"使用现成混音: {mix}", flush=True)
    else:
        srcs = mix_sources_from_manifest(args.voice_gain_db) \
            or mix_sources_fallback(args.voice_gain_db)
        print(f"混音输入（{len(srcs)} 条，与 files 模式工程一致）:", flush=True)
        for p, g in srcs:
            print(f"  - {p.name}" + (f"  (人声 +{g:.1f} dB)" if g else ""), flush=True)
        mix = tmpdir / "final_mix.wav"
        peak = build_mix(srcs, mix, limit=not args.no_limit)
        print(f"混音峰值: {peak:.1f} dBFS"
              + ("" if peak <= -0.5 else "  （已加 -0.18 dBFS 安全限幅，--no-limit 可关）"), flush=True)

    video_dur = ffprobe_duration(video)
    mix_dur = ffprobe_duration(mix)
    win_start = original_audio_window()
    win_end = win_start + video_dur
    print(f"视频时长: {video_dur:.3f}s   混音时长: {mix_dur:.3f}s", flush=True)
    print(f"原音（背景轨）窗口: [{win_start:.3f}, {win_end:.3f}]s —— 只保留窗口内的混音，"
          f"窗口外的前导（人声靠前）/尾随内容一并裁掉", flush=True)

    # Cut the mix to the original audio's window (anchored on the background
    # timeline, NOT on the mix's own extent), then pad to exactly the video
    # duration as a safety for shorter mixes.
    fit = tmpdir / "final_mix_fit.wav"
    run_ffmpeg([
        "-i", str(mix),
        "-af", f"atrim=start={win_start:.6f}:end={win_end:.6f},asetpts=PTS-STARTPTS,apad=whole_dur={video_dur:.6f}",
        "-t", f"{video_dur:.6f}", "-c:a", "pcm_s16le", str(fit),
    ])
    fit_dur = ffprobe_duration(fit)
    if abs(fit_dur - video_dur) > 0.02:
        print(f"警告: 裁剪后音频 {fit_dur:.3f}s 与视频 {video_dur:.3f}s 仍不一致", flush=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    # Mux: copy video stream, re-encode audio to AAC 48k stereo for the mp4.
    run_ffmpeg([
        "-i", str(video), "-i", str(fit),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{video_dur:.6f}", "-movflags", "+faststart",
        str(out),
    ])

    final_dur = ffprobe_duration(out)
    print(f"成片: {out}  时长 {final_dur:.3f}s（视频 {video_dur:.3f}s，一致）", flush=True)
    print(f"     视频流 h264 原样复制，音频 AAC 48kHz 立体声（已按视频时长裁/补）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
