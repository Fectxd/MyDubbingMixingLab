"""Separate an audio file with TIGER-DnR through the public HuggingFace Space API.

The fffiloni/TIGER-audio-extraction space runs on an A10G GPU and exposes a
Gradio API that returns dialogue / sound effects / music stems. This script
uploads the file, submits the job, polls with reconnects (the queue can be
long), and downloads the three stems.

Usage:
    python cloud_separate.py --input work/tmp/原片_44k_stereo.wav
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


SPACE = "https://fffiloni-tiger-audio-extraction.hf.space"
API_FN = "separate_dialog_effects_music_from_audio"
OUT_NAMES = ["dialog", "effect", "music"]


def multipart_upload(filepath: Path) -> str:
    """POST a file to /gradio_api/upload, return the server-side path."""
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
    req = urllib.request.Request(
        f"{SPACE}/gradio_api/upload", data=body, method="POST"
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)[0]


def submit(server_path: str) -> str:
    payload = json.dumps(
        {"data": [{"path": server_path, "meta": {"_type": "gradio.FileData"}}]}
    ).encode()
    req = urllib.request.Request(
        f"{SPACE}/gradio_api/call/{API_FN}", data=payload, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["event_id"]


def poll(event_id: str, cap_s: float) -> dict:
    """Poll the SSE stream line-by-line with reconnect; return final outputs."""
    path = f"/gradio_api/call/{API_FN}/{event_id}"
    host = SPACE.removeprefix("https://")
    deadline = time.time() + cap_s
    last_note = ""
    while time.time() < deadline:
        try:
            conn = http.client.HTTPSConnection(host, timeout=25)
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
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="44.1k stereo WAV to separate")
    parser.add_argument("--outdir", default=str(Path(__file__).resolve().parent / "work" / "separated"))
    parser.add_argument("--cap", type=int, default=1200, help="max seconds to wait (default 1200)")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"input not found: {src}")
        return 1
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] uploading {src.name} ({src.stat().st_size / 1e6:.1f} MB) ...", flush=True)
    server_path = multipart_upload(src)
    print(f"      server path: {server_path}", flush=True)

    print("[2/4] submitting separation job ...", flush=True)
    event_id = submit(server_path)
    print(f"      event id: {event_id}", flush=True)

    print(f"[3/4] waiting for the GPU job (cap {args.cap}s) ...", flush=True)
    t0 = time.time()
    result = poll(event_id, args.cap)
    outputs = result.get("output", {})
    data = outputs.get("data", []) if isinstance(outputs, dict) else []
    print(f"      job finished in {time.time() - t0:.0f}s", flush=True)

    print("[4/4] downloading stems ...", flush=True)
    stem = src.stem
    for name, item in zip(OUT_NAMES, data):
        url = item.get("url") if isinstance(item, dict) else None
        if not url:
            print(f"      !! no url for {name}: {item}", flush=True)
            continue
        dest = outdir / f"{stem}_{name}.wav"
        download(url, dest)
        print(f"      {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
