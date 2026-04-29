#!/usr/bin/env python3

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from video_generator import ensure_generator_ready, generate_movie, get_backend_name


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
DEFAULT_OUTPUT_DIR = os.environ.get(
    "DEFAULT_OUTPUT_DIR",
    "/Users/hakantaskin/Library/CloudStorage/GoogleDrive-taskin.baba@gmail.com/Other computers/TASKIN_LAPTOP/Etsy/E31T/Etsy",
).strip()
DEFAULT_DURATION_SECONDS = float(os.environ.get("DEFAULT_DURATION_SECONDS", "5").strip())
DEFAULT_START_SCALE = float(os.environ.get("DEFAULT_START_SCALE", "1.0").strip())
DEFAULT_END_SCALE = float(os.environ.get("DEFAULT_END_SCALE", "1.11").strip())
GENERATED_FILES: dict[str, Path] = {}
SUPPORTED_BATCH_IMAGE_NAMES = ("1.png", "1.jpg", "1.jpeg")
SUPPORTED_ENHANCE_IMAGE_NAMES = ("1.png", "1.jpg", "1.jpeg")
MAX_ENHANCE_SUBFOLDER_DEPTH = 3
DEFAULT_ENHANCE_WIDTH = 2000
DEFAULT_ENHANCE_HEIGHT = 2000
TERMINAL_JOB_STATES = {"completed", "stopped", "failed"}
ENHANCE_JOBS: dict[str, dict[str, object]] = {}
ENHANCE_JOBS_LOCK = threading.Lock()
VIDEO_BATCH_JOBS: dict[str, dict[str, object]] = {}
VIDEO_BATCH_JOBS_LOCK = threading.Lock()


def read_json_payload(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        raise ValueError("No request body was received.")

    body = handler.rfile.read(content_length)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must be valid JSON.") from exc


def expand_user_path(raw_path: str) -> Path:
    return Path(raw_path.strip()).expanduser()


def parse_render_settings(payload: dict[str, object]) -> tuple[float, float, float]:
    duration_seconds = float(str(payload.get("duration_seconds", DEFAULT_DURATION_SECONDS)).strip())
    start_scale = float(str(payload.get("start_scale", DEFAULT_START_SCALE)).strip())
    end_scale = float(str(payload.get("end_scale", DEFAULT_END_SCALE)).strip())
    if duration_seconds <= 0:
        raise ValueError("Duration must be greater than zero.")
    if start_scale <= 0 or end_scale <= 0:
        raise ValueError("Zoom values must be greater than zero.")
    return duration_seconds, start_scale, end_scale


def parse_request_payload(handler: BaseHTTPRequestHandler) -> tuple[str, str, float, float, float]:
    payload = read_json_payload(handler)

    source_path = str(payload.get("source_path", "")).strip()
    output_dir = str(payload.get("output_dir", "")).strip()
    duration_seconds, start_scale, end_scale = parse_render_settings(payload)
    if not source_path:
        raise ValueError("Enter a source photo path.")
    return source_path, output_dir, duration_seconds, start_scale, end_scale


def parse_batch_request_payload(handler: BaseHTTPRequestHandler) -> tuple[str, list[str], float, float, float]:
    payload = read_json_payload(handler)

    main_dir = str(payload.get("main_dir", "")).strip()
    raw_subfolders = payload.get("subfolders", "")
    subfolder_names: list[str]
    if isinstance(raw_subfolders, list):
        subfolder_names = [str(item).strip() for item in raw_subfolders if str(item).strip()]
    else:
        normalized = str(raw_subfolders).replace(",", "\n")
        subfolder_names = [item.strip() for item in normalized.splitlines() if item.strip()]

    if not main_dir:
        raise ValueError("Enter the main folder path.")
    if not subfolder_names:
        raise ValueError("Enter at least one subfolder name.")

    duration_seconds, start_scale, end_scale = parse_render_settings(payload)
    return main_dir, dedupe_preserve_order(subfolder_names), duration_seconds, start_scale, end_scale


def parse_enhance_request_payload(handler: BaseHTTPRequestHandler) -> tuple[str, int, int, int]:
    payload = read_json_payload(handler)
    main_dir = str(payload.get("main_dir", "")).strip()
    if not main_dir:
        raise ValueError("Enter the main folder path.")
    raw_max_images = str(payload.get("max_images", "")).strip()
    if not raw_max_images:
        raise ValueError("Enter how many images to process.")
    try:
        max_images = int(raw_max_images)
    except ValueError as exc:
        raise ValueError("Images to process must be an integer.") from exc
    if max_images <= 0:
        raise ValueError("Images to process must be greater than zero.")
    width, height = parse_resolution(payload.get("resolution", f"{DEFAULT_ENHANCE_WIDTH}x{DEFAULT_ENHANCE_HEIGHT}"))
    return main_dir, max_images, width, height


def parse_single_enhance_request_payload(handler: BaseHTTPRequestHandler) -> tuple[str, int, int]:
    payload = read_json_payload(handler)
    source_path = str(payload.get("source_path", "")).strip()
    if not source_path:
        raise ValueError("Enter a source image path.")
    width, height = parse_resolution(payload.get("resolution", f"{DEFAULT_ENHANCE_WIDTH}x{DEFAULT_ENHANCE_HEIGHT}"))
    return source_path, width, height


def parse_avif_convert_request_payload(handler: BaseHTTPRequestHandler) -> tuple[str]:
    payload = read_json_payload(handler)
    source_path = str(payload.get("source_path", "")).strip()
    if not source_path:
        raise ValueError("Enter an AVIF source image path.")
    return (source_path,)


def parse_resolution(raw_resolution: object) -> tuple[int, int]:
    resolution_text = str(raw_resolution).strip().lower()
    if "x" not in resolution_text:
        raise ValueError("Resolution must be in WIDTHxHEIGHT format, for example 2000x2000.")
    width_text, height_text = [part.strip() for part in resolution_text.split("x", 1)]
    if not width_text or not height_text:
        raise ValueError("Resolution must be in WIDTHxHEIGHT format, for example 2000x2000.")
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise ValueError("Resolution width and height must be integers.") from exc
    if width <= 0 or height <= 0:
        raise ValueError("Resolution width and height must be greater than zero.")
    return width, height


def parse_stop_request_payload(handler: BaseHTTPRequestHandler) -> str:
    payload = read_json_payload(handler)
    job_id = str(payload.get("job_id", "")).strip()
    if not job_id:
        raise ValueError("Missing job_id.")
    return job_id


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def resolve_source_path(raw_path: str) -> Path:
    source_path = expand_user_path(raw_path)
    if not source_path.exists():
        raise ValueError(f"Source file does not exist: {source_path}")
    if not source_path.is_file():
        raise ValueError(f"Source path is not a file: {source_path}")
    if not os.access(source_path, os.R_OK):
        raise ValueError(f"Source file is not readable: {source_path}")
    return source_path.resolve()


def resolve_existing_path(raw_path: str) -> Path:
    path = expand_user_path(raw_path)
    if not path.exists():
        raise ValueError(f"Source path does not exist: {path}")
    if not os.access(path, os.R_OK):
        raise ValueError(f"Source path is not readable: {path}")
    return path.resolve()


def preflight_validate_source_image(source_path: Path) -> None:
    if source_path.stat().st_size <= 0:
        raise ValueError(f"Source file is empty: {source_path}")

    ffprobe_binary = shutil.which("ffprobe")
    if not ffprobe_binary:
        return

    result = subprocess.run(
        [
            ffprobe_binary,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(source_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "ffprobe returned no details."
        raise ValueError(f"Source image preflight failed for {source_path}: {details}")


def resolve_output_dir(raw_path: str, source_path: Path) -> Path:
    output_dir = expand_user_path(raw_path) if raw_path else source_path.parent
    if not output_dir.exists():
        raise ValueError(f"Output folder does not exist: {output_dir}")
    if not output_dir.is_dir():
        raise ValueError(f"Output path is not a folder: {output_dir}")
    if not os.access(output_dir, os.W_OK):
        raise ValueError(f"Output folder is not writable: {output_dir}")
    return output_dir.resolve()


def resolve_main_dir(raw_path: str) -> Path:
    main_dir = expand_user_path(raw_path)
    if not main_dir.exists():
        raise ValueError(f"Main folder does not exist: {main_dir}")
    if not main_dir.is_dir():
        raise ValueError(f"Main path is not a folder: {main_dir}")
    if not os.access(main_dir, os.R_OK):
        raise ValueError(f"Main folder is not readable: {main_dir}")
    return main_dir.resolve()


def validate_subfolder_name(subfolder_name: str) -> Path:
    subfolder_path = Path(subfolder_name.strip())
    if not subfolder_path.parts:
        raise ValueError("Subfolder names cannot be empty.")
    if subfolder_path.is_absolute() or ".." in subfolder_path.parts:
        raise ValueError(f"Invalid subfolder name: {subfolder_name}")
    return subfolder_path


def resolve_batch_source_path(main_dir: Path, subfolder_name: str) -> Path:
    validated_subfolder = validate_subfolder_name(subfolder_name)
    etsy_dir = (main_dir / validated_subfolder / "Etsy").resolve()
    if main_dir not in etsy_dir.parents:
        raise ValueError(f"Subfolder escapes main directory: {subfolder_name}")
    if not etsy_dir.exists():
        raise ValueError(f"Missing Etsy folder: {etsy_dir}")
    if not etsy_dir.is_dir():
        raise ValueError(f"Etsy path is not a folder: {etsy_dir}")
    if not os.access(etsy_dir, os.W_OK):
        raise ValueError(f"Etsy folder is not writable: {etsy_dir}")

    for image_name in SUPPORTED_BATCH_IMAGE_NAMES:
        source_path = etsy_dir / image_name
        if source_path.exists() and source_path.is_file():
            if not os.access(source_path, os.R_OK):
                raise ValueError(f"Source file is not readable: {source_path}")
            return source_path

    expected = ", ".join(SUPPORTED_BATCH_IMAGE_NAMES)
    raise ValueError(f"No supported source image found in {etsy_dir}. Expected one of: {expected}")


def build_output_path(source_path: Path, output_dir: Path) -> Path:
    return output_dir / f"{source_path.stem}.mov"


def register_generated_file(file_path: Path) -> dict[str, str]:
    file_id = uuid.uuid4().hex
    resolved_path = file_path.resolve()
    GENERATED_FILES[file_id] = resolved_path
    return {
        "file_name": resolved_path.name,
        "full_path": str(resolved_path),
        "file_url": f"/generated/{file_id}",
        "download_url": f"/generated/{file_id}",
    }


def create_movie_from_source(
    source_path: Path,
    output_dir: Path,
    duration_seconds: float,
    start_scale: float,
    end_scale: float,
) -> Path:
    with tempfile.TemporaryDirectory(prefix="photo-to-mov-") as temp_dir:
        temp_root = Path(temp_dir)
        temp_source = temp_root / source_path.name
        shutil.copy2(source_path, temp_source)
        try:
            temp_output = generate_movie(temp_source, duration_seconds, start_scale, end_scale)
        except RuntimeError as exc:
            raise RuntimeError(f"{exc} Original source: {source_path}") from exc
        final_output = build_output_path(source_path, output_dir)
        if final_output.exists():
            final_output.unlink()
        shutil.move(str(temp_output), str(final_output))
        return final_output


def create_single_enhanced_photo(source_path: Path, width: int, height: int) -> Path:
    destination_path = source_path.parent / "1_etsy.jpg"
    if destination_path.exists():
        destination_path.unlink()
    enhance_image_for_etsy(source_path, destination_path, width, height)
    return destination_path


def convert_avif_to_jpg(source_path: Path) -> Path:
    if source_path.suffix.lower() != ".avif":
        raise ValueError(f"Source file must have .avif extension: {source_path}")

    destination_path = source_path.with_suffix(".jpg")
    ffmpeg_binary = shutil.which("ffmpeg")
    if ffmpeg_binary:
        command = [
            ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(destination_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode == 0:
            if not destination_path.is_file():
                raise RuntimeError(f"Conversion reported success but JPG was not created: {destination_path}")
            if destination_path.stat().st_size <= 0:
                raise RuntimeError(f"Conversion reported success but JPG is empty: {destination_path}")
            try:
                source_path.unlink()
            except OSError as exc:
                raise RuntimeError(f"JPG created but failed to delete source AVIF: {source_path}. {exc}") from exc
            return destination_path
        details = (completed.stderr or completed.stdout).strip() or "ffmpeg conversion failed."
        raise RuntimeError(details)

    if os.name == "posix" and shutil.which("sips"):
        command = [
            "sips",
            "-s",
            "format",
            "jpeg",
            "-s",
            "formatOptions",
            "best",
            str(source_path),
            "--out",
            str(destination_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode == 0:
            if not destination_path.is_file():
                raise RuntimeError(f"Conversion reported success but JPG was not created: {destination_path}")
            if destination_path.stat().st_size <= 0:
                raise RuntimeError(f"Conversion reported success but JPG is empty: {destination_path}")
            try:
                source_path.unlink()
            except OSError as exc:
                raise RuntimeError(f"JPG created but failed to delete source AVIF: {source_path}. {exc}") from exc
            return destination_path
        details = (completed.stderr or completed.stdout).strip() or "sips conversion failed."
        raise RuntimeError(details)

    raise RuntimeError("No supported AVIF conversion backend found. Install ffmpeg.")


def convert_avif_path(source_path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    generated: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    if source_path.is_file():
        output_path = convert_avif_to_jpg(source_path)
        result = register_generated_file(output_path)
        result["source_path"] = str(source_path)
        generated.append(result)
        return generated, failed

    if source_path.is_dir():
        avif_files = sorted(
            file_path.resolve()
            for file_path in source_path.rglob("*")
            if file_path.is_file() and file_path.suffix.lower() == ".avif"
        )
        for avif_file in avif_files:
            try:
                output_path = convert_avif_to_jpg(avif_file)
                result = register_generated_file(output_path)
                result["source_path"] = str(avif_file)
                generated.append(result)
            except Exception as exc:
                failed.append({"source_path": str(avif_file), "error": str(exc)})
        return generated, failed

    raise ValueError(f"Source path is not a file or folder: {source_path}")


def create_movies_from_batch(
    main_dir: Path,
    subfolders: list[str],
    duration_seconds: float,
    start_scale: float,
    end_scale: float,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    succeeded: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    for subfolder_name in subfolders:
        try:
            source_path = resolve_batch_source_path(main_dir, subfolder_name)
            output_path = create_movie_from_source(
                source_path=source_path,
                output_dir=source_path.parent,
                duration_seconds=duration_seconds,
                start_scale=start_scale,
                end_scale=end_scale,
            )
            generated = register_generated_file(output_path)
            generated["subfolder"] = subfolder_name
            generated["source_path"] = str(source_path)
            succeeded.append(generated)
        except Exception as exc:
            failed.append(
                {
                    "subfolder": subfolder_name,
                    "error": str(exc),
                }
            )

    return succeeded, failed


def discover_etsy_source_images(main_dir: Path) -> list[Path]:
    found_sources: list[Path] = []
    for root, dirs, files in os.walk(main_dir):
        current_dir = Path(root)
        rel_parts = current_dir.relative_to(main_dir).parts
        if len(rel_parts) > MAX_ENHANCE_SUBFOLDER_DEPTH + 1:
            dirs[:] = []
            continue

        if current_dir.name != "Etsy":
            continue
        if len(rel_parts) < 2 or len(rel_parts) > MAX_ENHANCE_SUBFOLDER_DEPTH + 1:
            continue
        # Skip folders that already contain an enhanced output.
        if (current_dir / "1_etsy.jpg").exists():
            continue

        lower_name_to_file = {name.lower(): name for name in files}
        for expected_name in SUPPORTED_ENHANCE_IMAGE_NAMES:
            actual_name = lower_name_to_file.get(expected_name.lower())
            if actual_name:
                source_path = current_dir / actual_name
                if source_path.is_file() and os.access(source_path, os.R_OK):
                    found_sources.append(source_path.resolve())
                break
    return found_sources


def normalize_etsy_photo_folders(main_dir: Path) -> dict[str, object]:
    renamed_count = 0
    skipped_count = 0
    error_count = 0
    messages: list[str] = []
    candidate_names = ("Etsy photos", "Etsy photo")

    for level_one_dir in sorted(main_dir.iterdir()):
        if not level_one_dir.is_dir():
            continue

        target_dir = level_one_dir / "Etsy"
        for candidate_name in candidate_names:
            candidate_dir = level_one_dir / candidate_name
            if not candidate_dir.exists() or not candidate_dir.is_dir():
                continue

            if target_dir.exists():
                skipped_count += 1
                messages.append(f"Skipped rename for {candidate_dir}: {target_dir} already exists.")
                break

            try:
                candidate_dir.rename(target_dir)
                renamed_count += 1
                messages.append(f"Renamed {candidate_dir.name} to Etsy in {level_one_dir}.")
            except Exception as exc:
                error_count += 1
                messages.append(f"Failed to rename {candidate_dir} to {target_dir}: {exc}")
            break

    return {
        "renamed_count": renamed_count,
        "skipped_count": skipped_count,
        "error_count": error_count,
        "messages": messages,
    }


def enhance_image_for_etsy(source_path: Path, destination_path: Path, width: int, height: int) -> None:
    ffmpeg_binary = shutil.which("ffmpeg")
    if ffmpeg_binary:
        filter_complex = (
            f"color=white:size={width}x{height}[bg];"
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuvj420p"
        )
        command = [
            ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-vf",
            filter_complex,
            "-q:v",
            "2",
            str(destination_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode == 0 and destination_path.exists():
            return
        message = (completed.stderr or completed.stdout).strip() or "ffmpeg enhancement failed."
        raise RuntimeError(message)

    if os.name == "posix" and shutil.which("sips"):
        with tempfile.TemporaryDirectory(prefix="photo-enhance-") as temp_dir:
            temp_square = Path(temp_dir) / "square.jpg"
            pad_command = [
                "sips",
                "-p",
                str(height),
                str(width),
                "--padToHeightWidth",
                str(height),
                str(width),
                "--padColor",
                "FFFFFF",
                str(source_path),
                "--out",
                str(temp_square),
            ]
            pad_result = subprocess.run(pad_command, capture_output=True, text=True, check=False)
            if pad_result.returncode != 0:
                message = (pad_result.stderr or pad_result.stdout).strip() or "sips enhancement failed."
                raise RuntimeError(message)

            convert_command = [
                "sips",
                "-s",
                "format",
                "jpeg",
                "-s",
                "formatOptions",
                "best",
                str(temp_square),
                "--out",
                str(destination_path),
            ]
            convert_result = subprocess.run(convert_command, capture_output=True, text=True, check=False)
            if convert_result.returncode == 0 and destination_path.exists():
                return
            message = (convert_result.stderr or convert_result.stdout).strip() or "sips conversion failed."
            raise RuntimeError(message)

    raise RuntimeError("No supported image enhancement backend found. Install ffmpeg.")


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _append_log(job: dict[str, object], message: str) -> None:
    logs = job["logs"]
    if not isinstance(logs, list):
        return
    next_seq = int(job.get("next_log_seq", 1))
    logs.append({"seq": next_seq, "timestamp": now_iso(), "message": message})
    job["next_log_seq"] = next_seq + 1


def build_enhance_job_status_payload(job: dict[str, object], since_seq: int) -> dict[str, object]:
    logs = job.get("logs", [])
    if not isinstance(logs, list):
        logs = []
    new_logs = [log for log in logs if int(log.get("seq", 0)) > since_seq]
    return {
        "job_id": job["job_id"],
        "state": job["state"],
        "main_dir": job["main_dir"],
        "resolution": f"{job['width']}x{job['height']}",
        "requested_count": str(job["requested_count"]),
        "discovered_count": str(job["discovered_count"]),
        "planned_count": str(job["planned_count"]),
        "processed_count": str(job["processed_count"]),
        "generated_count": str(job["generated_count"]),
        "failed_count": str(job["failed_count"]),
        "stop_requested": bool(job["stop_requested"]),
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "last_log_seq": str(int(job.get("next_log_seq", 1)) - 1),
        "logs": new_logs,
        "generated": job["generated"],
        "failed": job["failed"],
    }


def run_enhance_job(job_id: str, sources_to_process: list[Path]) -> None:
    with ENHANCE_JOBS_LOCK:
        job = ENHANCE_JOBS.get(job_id)
        if job is None:
            return
        job["state"] = "running"
        job["started_at"] = now_iso()
        _append_log(job, f"Batch job started. Planned {job['planned_count']} images at {job['width']}x{job['height']}.")

    for index, source_path in enumerate(sources_to_process, start=1):
        with ENHANCE_JOBS_LOCK:
            job = ENHANCE_JOBS.get(job_id)
            if job is None:
                return
            if bool(job["stop_requested"]):
                _append_log(job, "Stop requested. Ending batch after current progress checkpoint.")
                break
            planned_count = int(job["planned_count"])
            _append_log(job, f"Processing {index}/{planned_count}: {source_path}")
            width = int(job["width"])
            height = int(job["height"])

        try:
            destination_path = source_path.parent / "1_etsy.jpg"
            if destination_path.exists():
                destination_path.unlink()
            enhance_image_for_etsy(source_path, destination_path, width, height)
            generated = register_generated_file(destination_path)
            generated["source_path"] = str(source_path)
            generated["folder"] = str(source_path.parent)

            with ENHANCE_JOBS_LOCK:
                job = ENHANCE_JOBS.get(job_id)
                if job is None:
                    return
                generated_items = job["generated"]
                if isinstance(generated_items, list):
                    generated_items.append(generated)
                job["processed_count"] = int(job["processed_count"]) + 1
                job["generated_count"] = int(job["generated_count"]) + 1
                _append_log(job, f"Created {destination_path.name} for {source_path}.")
        except Exception as exc:
            with ENHANCE_JOBS_LOCK:
                job = ENHANCE_JOBS.get(job_id)
                if job is None:
                    return
                failed_items = job["failed"]
                failure_item = {"source_path": str(source_path), "error": str(exc)}
                if isinstance(failed_items, list):
                    failed_items.append(failure_item)
                job["processed_count"] = int(job["processed_count"]) + 1
                job["failed_count"] = int(job["failed_count"]) + 1
                _append_log(job, f"Failed for {source_path}: {exc}")

    with ENHANCE_JOBS_LOCK:
        job = ENHANCE_JOBS.get(job_id)
        if job is None:
            return
        if bool(job["stop_requested"]):
            job["state"] = "stopped"
            _append_log(job, "Batch job stopped by user.")
        else:
            job["state"] = "completed"
            _append_log(job, "Batch job completed.")
        job["finished_at"] = now_iso()


def start_enhance_job(main_dir: Path, requested_count: int, width: int, height: int) -> dict[str, object]:
    normalization_result = normalize_etsy_photo_folders(main_dir)
    all_sources = sorted(discover_etsy_source_images(main_dir))
    planned_sources = all_sources[:requested_count]
    job_id = uuid.uuid4().hex
    job: dict[str, object] = {
        "job_id": job_id,
        "state": "queued",
        "main_dir": str(main_dir),
        "width": width,
        "height": height,
        "requested_count": requested_count,
        "discovered_count": len(all_sources),
        "planned_count": len(planned_sources),
        "processed_count": 0,
        "generated_count": 0,
        "failed_count": 0,
        "stop_requested": False,
        "created_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "next_log_seq": 1,
        "logs": [],
        "generated": [],
        "failed": [],
    }
    _append_log(
        job,
        "Folder normalization: "
        f"{normalization_result['renamed_count']} renamed, "
        f"{normalization_result['skipped_count']} skipped, "
        f"{normalization_result['error_count']} errors.",
    )
    normalization_messages = normalization_result.get("messages", [])
    if isinstance(normalization_messages, list):
        for message in normalization_messages:
            _append_log(job, str(message))
    _append_log(job, f"Discovered {len(all_sources)} candidate images.")
    if len(planned_sources) < requested_count:
        _append_log(job, f"Requested {requested_count}, but only {len(planned_sources)} images matched.")

    with ENHANCE_JOBS_LOCK:
        ENHANCE_JOBS[job_id] = job

    worker = threading.Thread(target=run_enhance_job, args=(job_id, planned_sources), daemon=True)
    worker.start()
    return build_enhance_job_status_payload(job, since_seq=0)


def build_video_batch_job_status_payload(job: dict[str, object], since_seq: int) -> dict[str, object]:
    logs = job.get("logs", [])
    if not isinstance(logs, list):
        logs = []
    new_logs = [log for log in logs if int(log.get("seq", 0)) > since_seq]
    return {
        "job_id": job["job_id"],
        "state": job["state"],
        "main_dir": job["main_dir"],
        "requested_count": str(job["requested_count"]),
        "processed_count": str(job["processed_count"]),
        "generated_count": str(job["generated_count"]),
        "failed_count": str(job["failed_count"]),
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "duration_seconds": f"{job['duration_seconds']:g}",
        "start_scale": f"{job['start_scale']:g}",
        "end_scale": f"{job['end_scale']:g}",
        "last_log_seq": str(int(job.get("next_log_seq", 1)) - 1),
        "logs": new_logs,
        "generated": job["generated"],
        "failed": job["failed"],
    }


def run_video_batch_job(
    job_id: str,
    main_dir: Path,
    subfolders: list[str],
    duration_seconds: float,
    start_scale: float,
    end_scale: float,
) -> None:
    with VIDEO_BATCH_JOBS_LOCK:
        job = VIDEO_BATCH_JOBS.get(job_id)
        if job is None:
            return
        job["state"] = "running"
        job["started_at"] = now_iso()
        _append_log(job, f"Batch video job started. Planned {len(subfolders)} folders.")

    for index, subfolder_name in enumerate(subfolders, start=1):
        with VIDEO_BATCH_JOBS_LOCK:
            job = VIDEO_BATCH_JOBS.get(job_id)
            if job is None:
                return
            _append_log(job, f"Processing {index}/{len(subfolders)}: {subfolder_name}")

        try:
            source_path = resolve_batch_source_path(main_dir, subfolder_name)
            output_path = create_movie_from_source(
                source_path=source_path,
                output_dir=source_path.parent,
                duration_seconds=duration_seconds,
                start_scale=start_scale,
                end_scale=end_scale,
            )
            generated = register_generated_file(output_path)
            generated["subfolder"] = subfolder_name
            generated["source_path"] = str(source_path)

            with VIDEO_BATCH_JOBS_LOCK:
                job = VIDEO_BATCH_JOBS.get(job_id)
                if job is None:
                    return
                generated_items = job["generated"]
                if isinstance(generated_items, list):
                    generated_items.append(generated)
                job["processed_count"] = int(job["processed_count"]) + 1
                job["generated_count"] = int(job["generated_count"]) + 1
                _append_log(job, f"Created {output_path.name} for {subfolder_name}.")
        except Exception as exc:
            with VIDEO_BATCH_JOBS_LOCK:
                job = VIDEO_BATCH_JOBS.get(job_id)
                if job is None:
                    return
                failure = {"subfolder": subfolder_name, "error": str(exc)}
                failed_items = job["failed"]
                if isinstance(failed_items, list):
                    failed_items.append(failure)
                job["processed_count"] = int(job["processed_count"]) + 1
                job["failed_count"] = int(job["failed_count"]) + 1
                _append_log(job, f"Failed for {subfolder_name}: {exc}")

    with VIDEO_BATCH_JOBS_LOCK:
        job = VIDEO_BATCH_JOBS.get(job_id)
        if job is None:
            return
        job["state"] = "completed"
        job["finished_at"] = now_iso()
        _append_log(job, "Batch video job completed.")


def start_video_batch_job(
    main_dir: Path,
    subfolders: list[str],
    duration_seconds: float,
    start_scale: float,
    end_scale: float,
) -> dict[str, object]:
    job_id = uuid.uuid4().hex
    job: dict[str, object] = {
        "job_id": job_id,
        "state": "queued",
        "main_dir": str(main_dir),
        "requested_count": len(subfolders),
        "processed_count": 0,
        "generated_count": 0,
        "failed_count": 0,
        "duration_seconds": duration_seconds,
        "start_scale": start_scale,
        "end_scale": end_scale,
        "created_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "next_log_seq": 1,
        "logs": [],
        "generated": [],
        "failed": [],
    }

    with VIDEO_BATCH_JOBS_LOCK:
        VIDEO_BATCH_JOBS[job_id] = job

    worker = threading.Thread(
        target=run_video_batch_job,
        args=(job_id, main_dir, subfolders, duration_seconds, start_scale, end_scale),
        daemon=True,
    )
    worker.start()
    return build_video_batch_job_status_payload(job, since_seq=0)


class PhotoToMovHandler(BaseHTTPRequestHandler):
    server_version = "PhotoToMovHTTP/1.0"

    def do_GET(self) -> None:
        self.serve_file(send_body=True)

    def do_HEAD(self) -> None:
        self.serve_file(send_body=False)

    def serve_file(self, send_body: bool) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            path = "/index.html"
        if path == "/config":
            self.respond_json(
                HTTPStatus.OK,
                {
                    "default_output_dir": DEFAULT_OUTPUT_DIR,
                    "default_duration_seconds": f"{DEFAULT_DURATION_SECONDS:g}",
                    "default_start_scale": f"{DEFAULT_START_SCALE:g}",
                    "default_end_scale": f"{DEFAULT_END_SCALE:g}",
                },
            )
            return
        if path == "/batch-enhance/status":
            query = urllib.parse.parse_qs(parsed.query)
            job_id = str(query.get("job_id", [""])[0]).strip()
            if not job_id:
                self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Missing job_id."})
                return
            try:
                since_seq = int(str(query.get("since_seq", ["0"])[0]).strip() or "0")
            except ValueError:
                self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "since_seq must be an integer."})
                return
            with ENHANCE_JOBS_LOCK:
                job = ENHANCE_JOBS.get(job_id)
                if job is None:
                    self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Job not found."})
                    return
                payload = build_enhance_job_status_payload(job, since_seq)
            self.respond_json(HTTPStatus.OK, payload)
            return
        if path == "/batch-generate/status":
            query = urllib.parse.parse_qs(parsed.query)
            job_id = str(query.get("job_id", [""])[0]).strip()
            if not job_id:
                self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Missing job_id."})
                return
            try:
                since_seq = int(str(query.get("since_seq", ["0"])[0]).strip() or "0")
            except ValueError:
                self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "since_seq must be an integer."})
                return
            with VIDEO_BATCH_JOBS_LOCK:
                job = VIDEO_BATCH_JOBS.get(job_id)
                if job is None:
                    self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Job not found."})
                    return
                payload = build_video_batch_job_status_payload(job, since_seq)
            self.respond_json(HTTPStatus.OK, payload)
            return

        if path.startswith("/generated/"):
            file_id = path.removeprefix("/generated/").strip("/")
            file_path = GENERATED_FILES.get(file_id)
            if file_path is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            base_dir = None
            file_path = file_path.resolve()
        else:
            base_dir = WEB_ROOT
            file_path = (WEB_ROOT / path.lstrip("/")).resolve()

        if base_dir is not None and base_dir not in file_path.parents and file_path != base_dir:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type, _ = mimetypes.guess_type(file_path.name)
        if file_path.suffix.lower() == ".mov":
            content_type = "video/quicktime"

        file_size = file_path.stat().st_size
        range_header = self.headers.get("Range")
        start = 0
        end = file_size - 1
        status = HTTPStatus.OK

        if range_header and range_header.startswith("bytes="):
            range_spec = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
            if "-" in range_spec:
                raw_start, raw_end = range_spec.split("-", 1)
                if raw_start:
                    start = int(raw_start)
                if raw_end:
                    end = int(raw_end)
                if not raw_start:
                    suffix_length = int(raw_end)
                    start = max(file_size - suffix_length, 0)
                    end = file_size - 1
                end = min(end, file_size - 1)
                if start > end or start < 0:
                    self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    return
                status = HTTPStatus.PARTIAL_CONTENT

        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str((end - start) + 1))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        if send_body:
            try:
                with file_path.open("rb") as source_file:
                    source_file.seek(start)
                    remaining = (end - start) + 1
                    while remaining > 0:
                        chunk = source_file.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                # The client disconnected while the file was streaming; treat it as a normal abort.
                return

    def do_POST(self) -> None:
        if self.path not in (
            "/generate",
            "/batch-generate",
            "/batch-generate/start",
            "/batch-enhance/start",
            "/batch-enhance/stop",
            "/enhance-single",
            "/convert-avif-to-jpg",
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            if self.path == "/generate":
                ensure_generator_ready()
                source_path_text, output_dir_text, duration_seconds, start_scale, end_scale = parse_request_payload(self)
                source_path = resolve_source_path(source_path_text)
                preflight_validate_source_image(source_path)
                output_dir = resolve_output_dir(output_dir_text, source_path)
            elif self.path in ("/batch-generate", "/batch-generate/start"):
                ensure_generator_ready()
                main_dir_text, subfolders, duration_seconds, start_scale, end_scale = parse_batch_request_payload(self)
                main_dir = resolve_main_dir(main_dir_text)
            elif self.path == "/batch-enhance/start":
                main_dir_text, requested_count, resolution_width, resolution_height = parse_enhance_request_payload(self)
                main_dir = resolve_main_dir(main_dir_text)
            elif self.path == "/enhance-single":
                source_path_text, resolution_width, resolution_height = parse_single_enhance_request_payload(self)
                source_path = resolve_source_path(source_path_text)
                preflight_validate_source_image(source_path)
            elif self.path == "/convert-avif-to-jpg":
                (source_path_text,) = parse_avif_convert_request_payload(self)
                source_path = resolve_existing_path(source_path_text)
                if source_path.is_file():
                    preflight_validate_source_image(source_path)
            else:
                job_id = parse_stop_request_payload(self)
        except ValueError as exc:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        except Exception:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Unexpected server error."})
            return

        try:
            if self.path == "/generate":
                output_path = create_movie_from_source(source_path, output_dir, duration_seconds, start_scale, end_scale)
                response_payload = register_generated_file(output_path)
                response_payload["source_path"] = str(source_path)
                response_payload["duration_seconds"] = f"{duration_seconds:g}"
                response_payload["start_scale"] = f"{start_scale:g}"
                response_payload["end_scale"] = f"{end_scale:g}"
            elif self.path == "/batch-generate":
                generated, failed = create_movies_from_batch(
                    main_dir=main_dir,
                    subfolders=subfolders,
                    duration_seconds=duration_seconds,
                    start_scale=start_scale,
                    end_scale=end_scale,
                )
                response_payload = {
                    "main_dir": str(main_dir),
                    "requested_count": str(len(subfolders)),
                    "generated_count": str(len(generated)),
                    "failed_count": str(len(failed)),
                    "generated": generated,
                    "failed": failed,
                }
                response_payload["duration_seconds"] = f"{duration_seconds:g}"
                response_payload["start_scale"] = f"{start_scale:g}"
                response_payload["end_scale"] = f"{end_scale:g}"
            elif self.path == "/batch-generate/start":
                response_payload = start_video_batch_job(
                    main_dir=main_dir,
                    subfolders=subfolders,
                    duration_seconds=duration_seconds,
                    start_scale=start_scale,
                    end_scale=end_scale,
                )
            elif self.path == "/batch-enhance/start":
                response_payload = start_enhance_job(main_dir, requested_count, resolution_width, resolution_height)
            elif self.path == "/enhance-single":
                output_path = create_single_enhanced_photo(source_path, resolution_width, resolution_height)
                response_payload = register_generated_file(output_path)
                response_payload["source_path"] = str(source_path)
                response_payload["resolution"] = f"{resolution_width}x{resolution_height}"
            elif self.path == "/convert-avif-to-jpg":
                generated, failed = convert_avif_path(source_path)
                response_payload = {
                    "source_path": str(source_path),
                    "generated_count": str(len(generated)),
                    "failed_count": str(len(failed)),
                    "generated": generated,
                    "failed": failed,
                }
                if len(generated) == 1 and len(failed) == 0:
                    # Keep single-convert compatibility for UI fields.
                    response_payload.update(generated[0])
            else:
                with ENHANCE_JOBS_LOCK:
                    job = ENHANCE_JOBS.get(job_id)
                    if job is None:
                        self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Job not found."})
                        return
                    if str(job["state"]) in TERMINAL_JOB_STATES:
                        response_payload = build_enhance_job_status_payload(job, since_seq=0)
                    else:
                        job["stop_requested"] = True
                        _append_log(job, "Stop requested by user.")
                        response_payload = build_enhance_job_status_payload(job, since_seq=0)
            self.respond_json(HTTPStatus.OK, response_payload)
        except RuntimeError as exc:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except Exception:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Unexpected server error."})

    def log_message(self, format: str, *args) -> None:
        return

    def respond_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    ensure_generator_ready()
    server = ThreadingHTTPServer((HOST, PORT), PhotoToMovHandler)
    print(f"Photo To MOV web app running at http://{HOST}:{PORT} using {get_backend_name()} backend")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
