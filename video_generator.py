#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GENERATOR_BINARY = ROOT / "photo_to_mov"
GENERATOR_SOURCE = ROOT / "photo_to_mov.m"

FPS = 60
DURATION_SECONDS = 5
TOTAL_FRAMES = FPS * DURATION_SECONDS
START_SCALE = 1.0
END_SCALE = 1.11
TARGET_VIDEO_BITRATE = "20M"
MAX_VIDEO_BITRATE = "24M"
BUFFER_SIZE = "40M"


def _which_or_none(name: str) -> str | None:
    return shutil.which(name)


def _requested_backend() -> str:
    return os.environ.get("GENERATOR_BACKEND", "auto").strip().lower()


def get_backend_name() -> str:
    requested = _requested_backend()
    if requested in {"native", "ffmpeg"}:
        return requested
    if platform.system() == "Darwin":
        return "native"
    return "ffmpeg"


def ensure_generator_ready() -> None:
    backend = get_backend_name()
    if backend == "native":
        _ensure_native_ready()
        return
    if backend == "ffmpeg":
        _ensure_ffmpeg_ready()
        return
    raise RuntimeError(f"Unsupported generator backend: {backend}")


def generate_movie(input_path: Path) -> Path:
    backend = get_backend_name()
    if backend == "native":
        return _generate_movie_native(input_path)
    if backend == "ffmpeg":
        return _generate_movie_ffmpeg(input_path)
    raise RuntimeError(f"Unsupported generator backend: {backend}")


def _ensure_native_ready() -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("The native generator only works on macOS. Use GENERATOR_BACKEND=ffmpeg in Docker/Linux.")
    if not GENERATOR_SOURCE.exists():
        raise RuntimeError("Missing photo_to_mov.m source file.")
    if not GENERATOR_BINARY.exists() or GENERATOR_BINARY.stat().st_mtime < GENERATOR_SOURCE.stat().st_mtime:
        result = subprocess.run(
            ["make", "photo_to_mov"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "Build failed."
            raise RuntimeError(f"Unable to build photo_to_mov: {message}")


def _ensure_ffmpeg_ready() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if _which_or_none(name) is None]
    if missing:
        raise RuntimeError(f"Missing required command(s): {', '.join(missing)}")


def _generate_movie_native(input_path: Path) -> Path:
    result = subprocess.run(
        [str(GENERATOR_BINARY), str(input_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Generation failed."
        raise RuntimeError(message)

    output_path = input_path.with_suffix(".mov")
    if not output_path.exists():
        raise RuntimeError("The movie file was not created.")
    return output_path


def _probe_dimensions(input_path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(input_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unable to inspect the input image."
        raise RuntimeError(message)

    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Unable to inspect the input image.") from exc

    if width <= 0 or height <= 0:
        raise RuntimeError("Invalid source dimensions.")
    return width, height


def _masked_focus_filter(width: int, height: int) -> str:
    min_side = min(width, height)
    radius0 = max(int(min_side * 0.18), 1)
    radius1 = max(int(min_side * 0.52), radius0 + 1)
    radius = f"hypot(X-W/2\\,Y-H/2)"
    mask_expr = (
        f"if(lte({radius}\\,{radius0})\\,255\\,"
        f"if(gte({radius}\\,{radius1})\\,0\\,"
        f"255*(1-(({radius}-{radius0})/({radius1 - radius0})))))"
    )

    return (
        f"[0:v]format=rgba,split=2[base][enhbase];"
        f"[enhbase]eq=contrast=1.12:brightness=0.03:saturation=1.02,"
        f"unsharp=5:5:0.25:5:5:0.0,colorbalance=rs=0.03:bs=-0.02[enh];"
        f"nullsrc=size={width}x{height},format=gray,geq=lum='{mask_expr}'[mask];"
        f"[base][enh][mask]maskedmerge[merged];"
    )


def _zoom_filter(width: int, height: int) -> str:
    duration = f"{DURATION_SECONDS:.6f}"
    zoom_delta = END_SCALE - START_SCALE
    progress_expr = f"clip(t/{duration}\\,0\\,1)"
    zoom_expr = (
        f"{START_SCALE}+{zoom_delta}*"
        f"(3*pow({progress_expr}\\,2)-2*pow({progress_expr}\\,3))"
    )
    return (
        f"[merged]scale="
        f"w='iw*({zoom_expr})':"
        f"h='ih*({zoom_expr})':"
        f"eval=frame:flags=bicubic,"
        f"crop=w={width}:h={height}:"
        f"x='(iw-ow)/2':"
        f"y='(ih-oh)/2':"
        f"exact=1,"
        f"setsar=1,format=yuv420p[outv]"
    )


def _generate_movie_ffmpeg(input_path: Path) -> Path:
    width, height = _probe_dimensions(input_path)
    output_path = input_path.with_suffix(".mov")
    filter_complex = _masked_focus_filter(width, height) + _zoom_filter(width, height)

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            str(DURATION_SECONDS),
            "-i",
            str(input_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-frames:v",
            str(TOTAL_FRAMES),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-b:v",
            TARGET_VIDEO_BITRATE,
            "-maxrate",
            MAX_VIDEO_BITRATE,
            "-bufsize",
            BUFFER_SIZE,
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Generation failed."
        raise RuntimeError(message)

    if not output_path.exists():
        raise RuntimeError("The movie file was not created.")
    return output_path
