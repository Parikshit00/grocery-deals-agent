"""Render the animated agent-flow.svg to docs/media/agent-flow.mp4 and agent-flow.gif.

The SVG animates with SMIL only (so GitHub shows it inline through <img>). This script opens it in
headless Chromium, pauses the timeline and steps it deterministically with setCurrentTime at FPS
frames per second for exactly one loop, then encodes the frames with ffmpeg.

    python docs/architecture/render_flow.py                # frames + encode (Playwright + ffmpeg)
    python docs/architecture/render_flow.py --only frames  # dev image: Playwright, no ffmpeg
    python docs/architecture/render_flow.py --only encode  # host: ffmpeg, no Playwright
"""

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SVG = ROOT / "docs/architecture/agent-flow.svg"
MEDIA = ROOT / "docs/media"
W, H, LOOP_S, FPS, GIF_W, GIF_FPS = 1600, 900, 16, 20, 960, 20


def render_frames(frames: Path) -> None:
    from playwright.sync_api import sync_playwright

    frames.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        page.goto(SVG.as_uri())
        page.evaluate("document.documentElement.pauseAnimations()")
        for i in range(LOOP_S * FPS):
            page.evaluate("t => document.documentElement.setCurrentTime(t)", i / FPS)
            page.screenshot(path=str(frames / f"f{i:04d}.png"))
        browser.close()


def encode(frames: Path) -> None:
    ffmpeg = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    src = ["-framerate", str(FPS), "-i", str(frames / "f%04d.png")]
    MEDIA.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            *src,
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(MEDIA / "agent-flow.mp4"),
        ],
        check=True,
    )
    gif_filter = (
        f"fps={GIF_FPS},scale={GIF_W}:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=max_colors=128:stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            *src,
            "-filter_complex",
            gif_filter,
            "-loop",
            "0",
            str(MEDIA / "agent-flow.gif"),
        ],
        check=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--frames", type=Path, default=ROOT / ".agent-flow-frames", help="frame directory"
    )
    ap.add_argument("--only", choices=["frames", "encode"], help="run one step only")
    args = ap.parse_args()
    if args.only != "encode":
        render_frames(args.frames)
    if args.only != "frames":
        encode(args.frames)
        shutil.rmtree(args.frames)


if __name__ == "__main__":
    main()
