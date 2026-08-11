"""Enhance dry takes via the official NVIDIA RE-USE Space API (no GPU needed locally).

Uploads each wav to https://huggingface.co/spaces/nvidia/RE-USE, calls the
`unified_enhance` Gradio endpoint, polls the queue, and downloads the enhanced
audio into work/enhanced/. Queues can be long; use --cap to limit waiting.

Usage:
    python cloud_enhance.py --inputs test/5_*.wav
    python cloud_enhance.py --inputs take1.wav --cap 600
"""

from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path


SPACE = "https://nvidia-re-use.hf.space"
API_FN = "unified_enhance"


def upload(filepath: Path) -> str:
    boundary = "----codex" + os.urandom(8).hex()
    fname = filepath.name
    ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    fdata = filepath.read_bytes()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
        f"filename=\"{fname}\"\r\nContent-Type: {ctype}\r\n\r\n".encode()
        + fdata
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(f"{SPACE}/gradio_api/upload", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)[0]


def submit(server_path: str) -> str:
    audio = {"path": server_path, "meta": {"_type": "gradio.FileData"}}
    payload = json.dumps({"data": [audio, None, "", ""]}).encode()
    req = urllib.request.Request(
        f"{SPACE}/gradio_api/call/{API_FN}", data=payload, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["event_id"]


def poll(event_id: str, cap_s: float) -> dict:
    path = f"/gradio_api/call/{API_FN}/{event_id}"
    host = SPACE.removeprefix("https://")
    deadline = time.time() + cap_s
    last_note = ""
    while time.time() < deadline:
        conn = http.client.HTTPSConnection(host, timeout=25)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            while time.time() < deadline:
                line = resp.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if not text.startswith("data:"):
                    continue
                payload = text[5:].strip()
                if payload in ("", "null", "[DONE]"):
                    continue
                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("msg")
                if kind == "process_completed":
                    return msg
                note = f"  {kind}"
                if msg.get("queue_position") is not None:
                    note += f", queue position {msg['queue_position']}"
                if msg.get("rank") is not None:
                    note += f", running slot {msg['rank'] + 1}"
                if note != last_note:
                    print(note, flush=True)
                    last_note = note
        except (socket.timeout, http.client.HTTPException, OSError) as e:
            print(f"  connection issue ({type(e).__name__}), reconnecting", flush=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        time.sleep(2)
    raise TimeoutError(f"job did not finish within {cap_s:.0f}s")


def download(url: str, dest: Path) -> None:
    full = url if url.startswith("http") else f"{SPACE}{url}"
    req = urllib.request.Request(full)
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


def enhance_one(src: Path, dest: Path, cap_s: float) -> None:
    print(f"[upload] {src.name} ({src.stat().st_size / 1e6:.1f} MB) ...", flush=True)
    server_path = upload(src)
    event_id = submit(server_path)
    print(f"  submitted, waiting (cap {cap_s:.0f}s) ...", flush=True)
    result = poll(event_id, cap_s)
    data = result.get("output", {}).get("data", [])
    enhanced = data[1] if len(data) > 1 else None
    url = enhanced.get("url") if isinstance(enhanced, dict) else None
    if not url:
        raise RuntimeError(f"no enhanced output for {src.name}: {result}")
    download(url, dest)
    print(f"  saved {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--outdir", default=str(Path(__file__).resolve().parent / "work" / "enhanced"))
    parser.add_argument("--cap", type=int, default=1200, help="max seconds per file")
    args = parser.parse_args()

    files: list[Path] = []
    for item in args.inputs:
        p = Path(item)
        if p.is_dir():
            files.extend(f for f in p.iterdir() if f.suffix.lower() == ".wav")
        elif p.is_file():
            files.append(p)
    files = sorted(set(files), key=lambda p: p.name.lower())
    if not files:
        print("no input files")
        return 1

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for f in files:
        dest = outdir / f"{f.stem}.wav"
        if dest.exists():
            print(f"skip {f.name} (already enhanced)", flush=True)
            continue
        try:
            enhance_one(f, dest, args.cap)
        except TimeoutError:
            print(f"  !! {f.name} timed out; try again later or raise --cap", flush=True)
    print(f"\ndone in {time.time() - t0:.0f}s -> {outdir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
