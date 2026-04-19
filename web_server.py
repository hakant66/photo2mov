#!/usr/bin/env python3

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
import urllib.parse
import uuid
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
GENERATED_FILES: dict[str, Path] = {}


def expand_user_path(raw_path: str) -> Path:
    return Path(raw_path.strip()).expanduser()


def parse_request_payload(handler: BaseHTTPRequestHandler) -> tuple[str, str]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        raise ValueError("No request body was received.")

    body = handler.rfile.read(content_length)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must be valid JSON.") from exc

    source_path = str(payload.get("source_path", "")).strip()
    output_dir = str(payload.get("output_dir", "")).strip()
    if not source_path:
        raise ValueError("Enter a source photo path.")
    return source_path, output_dir


def resolve_source_path(raw_path: str) -> Path:
    source_path = expand_user_path(raw_path)
    if not source_path.exists():
        raise ValueError(f"Source file does not exist: {source_path}")
    if not source_path.is_file():
        raise ValueError(f"Source path is not a file: {source_path}")
    if not os.access(source_path, os.R_OK):
        raise ValueError(f"Source file is not readable: {source_path}")
    return source_path.resolve()


def resolve_output_dir(raw_path: str, source_path: Path) -> Path:
    output_dir = expand_user_path(raw_path) if raw_path else source_path.parent
    if not output_dir.exists():
        raise ValueError(f"Output folder does not exist: {output_dir}")
    if not output_dir.is_dir():
        raise ValueError(f"Output path is not a folder: {output_dir}")
    if not os.access(output_dir, os.W_OK):
        raise ValueError(f"Output folder is not writable: {output_dir}")
    return output_dir.resolve()


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


def create_movie_from_source(source_path: Path, output_dir: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="photo-to-mov-") as temp_dir:
        temp_root = Path(temp_dir)
        temp_source = temp_root / source_path.name
        shutil.copy2(source_path, temp_source)
        temp_output = generate_movie(temp_source)
        final_output = build_output_path(source_path, output_dir)
        if final_output.exists():
            final_output.unlink()
        shutil.move(str(temp_output), str(final_output))
        return final_output


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
                },
            )
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
            with file_path.open("rb") as source_file:
                source_file.seek(start)
                remaining = (end - start) + 1
                while remaining > 0:
                    chunk = source_file.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def do_POST(self) -> None:
        if self.path != "/generate":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            ensure_generator_ready()
            source_path_text, output_dir_text = parse_request_payload(self)
            source_path = resolve_source_path(source_path_text)
            output_dir = resolve_output_dir(output_dir_text, source_path)
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
            output_path = create_movie_from_source(source_path, output_dir)
            response_payload = register_generated_file(output_path)
            response_payload["source_path"] = str(source_path)
            self.respond_json(HTTPStatus.OK, response_payload)
        except RuntimeError as exc:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except Exception:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Unexpected server error."})

    def log_message(self, format: str, *args) -> None:
        return

    def respond_json(self, status: HTTPStatus, payload: dict[str, str]) -> None:
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
