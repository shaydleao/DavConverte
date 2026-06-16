#!/usr/bin/env python3
"""
davconvert-cli — versão linha de comando do DAVConvert
Uso: python cli.py <pasta_ou_arquivo> [opções]
"""

import os
import sys
import argparse
import subprocess
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


def detect_hw_encoder() -> str:
    hw_opts = ["h264_nvenc", "h264_vaapi", "h264_qsv", "libx264"]
    result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                            capture_output=True, text=True)
    for enc in hw_opts:
        if enc in result.stdout:
            test = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
                 "-c:v", enc, "-f", "null", "-"],
                capture_output=True
            )
            if test.returncode == 0:
                return enc
    return "libx264"


def convert(src: str, dst: str, enc: str, crf: str, threads: int) -> tuple[bool, float]:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats",
        "-i", src, "-threads", str(threads),
        "-c:v", enc,
    ]
    if enc in ("libx264", "libx265"):
        cmd += ["-crf", crf, "-preset", "fast"]
    else:
        cmd += ["-qp", str(int(crf) - 5)]

    cmd += ["-c:a", "aac", "-b:a", "128k"]
    if dst.endswith(".mp4"):
        cmd += ["-movflags", "+faststart"]
    cmd += ["-y", dst]

    t0 = time.time()
    r = subprocess.run(cmd)
    return r.returncode == 0, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description="DAVConvert CLI")
    ap.add_argument("input", nargs="+", help=".dav file(s) or folder(s)")
    ap.add_argument("-f", "--format", choices=["mp4", "avi"], default="mp4")
    ap.add_argument("-q", "--quality", choices=["high","medium","low"], default="medium")
    ap.add_argument("-j", "--jobs", type=int, default=2, help="Parallel conversions")
    ap.add_argument("-t", "--threads", type=int, default=4, help="CPU threads per job")
    ap.add_argument("-o", "--output", default=None, help="Output directory")
    args = ap.parse_args()

    crf_map = {"high": "18", "medium": "23", "low": "28"}
    crf = crf_map[args.quality]

    files = []
    for inp in args.input:
        p = Path(inp)
        if p.is_dir():
            files += list(p.rglob("*.dav")) + list(p.rglob("*.DAV"))
        elif p.exists():
            files.append(p)

    if not files:
        print("Nenhum arquivo .DAV encontrado.")
        sys.exit(1)

    enc = detect_hw_encoder()
    print(f"Encoder: {enc}  |  {len(files)} arquivo(s)  |  {args.jobs} paralelo(s)\n")

    def task(f):
        out_dir = Path(args.output) if args.output else f.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = str(out_dir / (f.stem + f".{args.format}"))
        ok, t = convert(str(f), dst, enc, crf, args.threads)
        status = "✔" if ok else "✘"
        print(f"  {status}  {f.name}  [{t:.1f}s]")
        return ok

    done = ok = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as exe:
        for result in as_completed([exe.submit(task, f) for f in files]):
            done += 1
            if result.result():
                ok += 1
            print(f"  [{done}/{len(files)}]", end="\r")

    print(f"\nPronto: {ok} ✔  {len(files)-ok} ✘")


if __name__ == "__main__":
    main()
