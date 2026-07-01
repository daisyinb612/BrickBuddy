#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VLM_SCRIPT = PROJECT_ROOT / "qwen_legoagent" / "vlm" / "vlm.py"
REALTIME_SCRIPT = PROJECT_ROOT / "qwen_legoagent" / "realtime" / "run_builtin_realtime_turns.py"
DEFAULT_RAWDATA = PROJECT_ROOT / "legoagentbackend" / "rawdata"
DEFAULT_VIDEO = PROJECT_ROOT / "legoagentbackend" / "testvideo" / "step8test.mp4"
DEFAULT_GLASSES_STREAM_URL = "http://172.20.10.9:8080/"
DEFAULT_SOURCE_MODE = "glasses"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_VLM_INTERVAL_SECONDS = 15.0
DEFAULT_FINAL_FRAME_OFFSET_SECONDS = 0.5
DEFAULT_REALTIME_VIDEO_FPS = 1.0
DEFAULT_VLM_FRAME_TIMEOUT_SECONDS = 30.0
DEFAULT_VLM_MODEL_KEY = "gpt-5.5"
VLM_MODEL_KEY_ALIASES = {
    "gpt": "gpt-5.5",
    "gemini": "gemini-3.5-flash",
    "qwen": "qwen3.5-omni-flash",
    "qwen3": "qwen3-omni-flash",
}
VLM_HISTORY_DIR = PROJECT_ROOT / "qwen_legoagent" / "vlm" / "vlmhistory"
REALTIME_HISTORY_DIR = PROJECT_ROOT / "qwen_legoagent" / "realtime" / "realtimehistory"

SUBSCRIBERS: set[queue.Queue[str]] = set()
SUBSCRIBERS_LOCK = threading.Lock()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the Lego Agent backend for the frontend and VLM worker."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--glasses-stream-url", default=DEFAULT_GLASSES_STREAM_URL)
    parser.add_argument(
        "--source-mode",
        choices=("simulation", "glasses"),
        default=DEFAULT_SOURCE_MODE,
        help="Default video source when /vlm/start does not pass source_mode.",
    )
    parser.add_argument("--rawdata", type=Path, default=DEFAULT_RAWDATA)
    parser.add_argument(
        "--vlm-interval-seconds",
        type=float,
        default=DEFAULT_VLM_INTERVAL_SECONDS,
        help="Default realtime VLM sampling interval. Use 1 for one frame per second.",
    )
    parser.add_argument(
        "--vlm-frame-timeout-seconds",
        type=float,
        default=DEFAULT_VLM_FRAME_TIMEOUT_SECONDS,
        help=(
            "Maximum time to wait for one single-frame VLM subprocess. "
            "When it times out, only that frame is skipped and the scheduler keeps running."
        ),
    )
    parser.add_argument(
        "--vlm-model-series",
        choices=("gpt", "gemini", "qwen"),
        default="gpt",
        help="Default VLM model family used when /vlm/start does not pass a model.",
    )
    parser.add_argument(
        "--vlm-model-key",
        default=None,
        help=(
            "Exact VLM model key or alias. Overrides --vlm-model-series. "
            "Examples: gpt, gemini, qwen, gpt-5.5, qwen3.5-omni-flash."
        ),
    )
    parser.add_argument(
        "--no-realtime",
        action="store_true",
        help="Do not start Qwen Realtime automatically when /vlm/start is called.",
    )
    parser.add_argument(
        "--realtime-video-fps",
        type=float,
        default=DEFAULT_REALTIME_VIDEO_FPS,
        help="Video frame rate sent to Qwen Realtime. Default is 1 frame per second.",
    )
    parser.add_argument(
        "--realtime-no-video",
        action="store_true",
        help="Start Qwen Realtime audio-only while still polling VLM output.",
    )
    parser.add_argument(
        "--realtime-playback-guard-ms",
        type=int,
        default=1200,
        help="Suppress microphone forwarding during playback and this many ms after.",
    )
    parser.add_argument(
        "--no-realtime-mic-suppression",
        action="store_true",
        help="Allow microphone audio to be sent while assistant playback is active.",
    )
    return parser.parse_args()


def broadcast(payload: dict[str, Any]) -> None:
    message = json.dumps(payload, ensure_ascii=False)
    with SUBSCRIBERS_LOCK:
        stale: list[queue.Queue[str]] = []
        for subscriber in SUBSCRIBERS:
            try:
                subscriber.put_nowait(message)
            except queue.Full:
                stale.append(subscriber)
        for subscriber in stale:
            SUBSCRIBERS.discard(subscriber)


def process_running(process: subprocess.Popen[str] | None) -> bool:
    return process is not None and process.poll() is None


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required tool not found on PATH: {name}")
    return path


def video_duration(video_path: Path) -> float:
    ffprobe = require_tool("ffprobe")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(completed.stdout.strip())


def format_video_time(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def scheduler_sample_timestamps(
    *,
    start_seconds: float,
    end_seconds: float,
    interval_seconds: float,
    max_frames: int | None,
    include_final_frame: bool,
    final_frame_offset_seconds: float,
) -> list[float]:
    timestamps: list[float] = []
    frame_index = 0
    while True:
        if max_frames is not None and len(timestamps) >= max_frames:
            break
        sample_timestamp = start_seconds + frame_index * interval_seconds
        if sample_timestamp > end_seconds:
            break
        timestamps.append(round(sample_timestamp, 3))
        frame_index += 1

    if include_final_frame and max_frames is None:
        final_timestamp = max(
            start_seconds,
            end_seconds - max(0.0, final_frame_offset_seconds),
        )
        if not timestamps or final_timestamp - timestamps[-1] > 1.0:
            timestamps.append(round(final_timestamp, 3))

    return timestamps


def extract_frame(video_path: Path, frame_path: Path, timestamp: float) -> None:
    ffmpeg = require_tool("ffmpeg")
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-y",
            str(frame_path),
        ],
        check=True,
    )


def capture_jpeg_bytes_from_stream(
    stream_url: str,
    *,
    timeout: float = 3.0,
    max_bytes: int = 5_000_000,
) -> bytes:
    parsed = urlparse(stream_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("stream_url must be an absolute http(s) URL")

    request = urllib.request.Request(
        stream_url,
        headers={
            "Accept": "image/jpeg,multipart/x-mixed-replace,*/*",
            "User-Agent": "LegoGlass-VLM/1.0",
        },
    )
    buffer = bytearray()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        while len(buffer) < max_bytes:
            chunk = response.read(32768)
            if not chunk:
                break
            buffer.extend(chunk)
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                continue
            end = buffer.find(b"\xff\xd9", start + 2)
            if end >= 0:
                return bytes(buffer[start : end + 2])

    raise TimeoutError("No complete JPEG frame was received from the stream")


def extract_stream_frame(stream_url: str, frame_path: Path) -> None:
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(capture_jpeg_bytes_from_stream(stream_url))


def slugify_filename_part(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return text[:80] or "unknown"


def source_mode_from_payload(
    payload: dict[str, Any],
    *,
    default_source_mode: str = "simulation",
) -> str:
    source_mode = str(
        payload.get("source_mode")
        or payload.get("source")
        or payload.get("mode")
        or default_source_mode
    ).strip().lower()
    if source_mode in {"glasses", "glass", "http", "stream", "live"}:
        return "glasses"
    if source_mode == "rtmp":
        return "rtmp"
    return "simulation"


def stream_url_from_payload(
    payload: dict[str, Any],
    *,
    default_stream_url: str,
) -> str:
    return str(
        payload.get("stream_url")
        or payload.get("source_url")
        or payload.get("video_url")
        or default_stream_url
    ).strip()


def resolve_vlm_model_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        text = DEFAULT_VLM_MODEL_KEY
    return VLM_MODEL_KEY_ALIASES.get(text.lower(), text)


def model_series_for_run_dir(model_key: str) -> str:
    lowered = model_key.lower()
    if lowered.startswith("gpt"):
        return "gpt"
    if lowered.startswith("gemini"):
        return "gemini"
    if lowered.startswith("qwen"):
        return "qwen"
    return slugify_filename_part(model_key)


def create_vlm_run_dir(model_key: str) -> Path:
    run_dir_prefix = f"vlm-{model_series_for_run_dir(model_key)}"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = VLM_HISTORY_DIR / f"{run_dir_prefix}-{stamp}"
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
        return base
    for index in range(1, 100):
        candidate = VLM_HISTORY_DIR / f"{run_dir_prefix}-{stamp}-{index:02d}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
    raise RuntimeError("Unable to create unique VLM run directory")


class LegoAgentBackendHandler(BaseHTTPRequestHandler):
    server_version = "LegoAgentBackend/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"status": "ok", "service": "lego-agent-backend"})
            return
        if parsed.path == "/vlm/status":
            self.send_json(self.server.vlm_status())  # type: ignore[attr-defined]
            return
        if parsed.path == "/events":
            self.serve_events()
            return
        if parsed.path in {"/video", "/video/step8test.mp4"}:
            self.serve_video(self.server.video_path)  # type: ignore[attr-defined]
            return
        if parsed.path in {"/glasses-stream/frame", "/api/glasses-stream/frame"}:
            self.serve_glasses_stream_frame(parsed.query)
            return
        if parsed.path.startswith("/rawdata/"):
            self.serve_rawdata(parsed.path.removeprefix("/rawdata/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/vlm/start":
            payload = self.read_json_body()
            if payload is None:
                return
            self.send_json(self.server.start_vlm(payload))  # type: ignore[attr-defined]
            return
        if parsed.path == "/vlm/stop":
            self.send_json(self.server.stop_vlm())  # type: ignore[attr-defined]
            return
        if parsed.path != "/events":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        payload = self.read_json_body()
        if payload is None:
            return
        broadcast(payload)
        self.send_json({"ok": True})

    def read_json_body(self) -> dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            content_length = 0
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
            return None
        if not isinstance(payload, dict):
            self.send_error(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
            return None
        return payload

    def send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        subscriber: queue.Queue[str] = queue.Queue(maxsize=100)
        with SUBSCRIBERS_LOCK:
            SUBSCRIBERS.add(subscriber)
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = subscriber.get(timeout=15)
                    self.wfile.write(f"data: {message}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError):
            pass
        finally:
            with SUBSCRIBERS_LOCK:
                SUBSCRIBERS.discard(subscriber)

    def serve_video(self, video_path: Path) -> None:
        if not video_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, f"Video not found: {video_path}")
            return

        file_size = video_path.stat().st_size
        range_header = self.headers.get("Range")
        start = 0
        end = file_size - 1
        status = HTTPStatus.OK

        if range_header and range_header.startswith("bytes="):
            range_value = range_header.removeprefix("bytes=").split(",", 1)[0]
            start_text, _, end_text = range_value.partition("-")
            try:
                if start_text:
                    start = int(start_text)
                if end_text:
                    end = int(end_text)
                start = max(0, min(start, file_size - 1))
                end = max(start, min(end, file_size - 1))
                status = HTTPStatus.PARTIAL_CONTENT
            except ValueError:
                start = 0
                end = file_size - 1
                status = HTTPStatus.OK

        content_length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(content_length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()

        try:
            with video_path.open("rb") as handle:
                handle.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionError, ConnectionResetError):
            return

    def serve_glasses_stream_frame(self, query: str) -> None:
        params = parse_qs(query)
        stream_url = (
            params.get("url", [self.server.default_glasses_stream_url])[0]  # type: ignore[attr-defined]
            or self.server.default_glasses_stream_url  # type: ignore[attr-defined]
        )
        try:
            jpeg_bytes = capture_jpeg_bytes_from_stream(stream_url)
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            self.send_error(HTTPStatus.BAD_GATEWAY, f"Unable to capture stream frame: {exc}")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(jpeg_bytes)))
        self.end_headers()
        self.wfile.write(jpeg_bytes)

    def serve_rawdata(self, relative_path: str) -> None:
        rawdata_path = self.server.rawdata_path  # type: ignore[attr-defined]
        target = (rawdata_path / relative_path).resolve()
        try:
            target.relative_to(rawdata_path)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Rawdata file not found")
            return

        suffix = target.suffix.lower()
        content_type = {
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class LegoAgentBackendServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        video_path: Path,
        default_glasses_stream_url: str,
        default_source_mode: str,
        rawdata_path: Path,
        default_vlm_model_key: str,
        default_vlm_interval_seconds: float,
        vlm_frame_timeout_seconds: float,
        start_realtime_by_default: bool,
        default_realtime_video_fps: float,
        realtime_no_video: bool,
        realtime_playback_guard_ms: int,
        suppress_realtime_mic_during_playback: bool,
    ) -> None:
        super().__init__(server_address, LegoAgentBackendHandler)
        self.video_path = video_path
        self.default_glasses_stream_url = default_glasses_stream_url
        self.default_source_mode = default_source_mode
        self.rawdata_path = rawdata_path
        self.default_vlm_model_key = default_vlm_model_key
        self.default_vlm_interval_seconds = default_vlm_interval_seconds
        self.vlm_frame_timeout_seconds = vlm_frame_timeout_seconds
        self.start_realtime_by_default = start_realtime_by_default
        self.default_realtime_video_fps = default_realtime_video_fps
        self.realtime_no_video = realtime_no_video
        self.realtime_playback_guard_ms = realtime_playback_guard_ms
        self.suppress_realtime_mic_during_playback = (
            suppress_realtime_mic_during_playback
        )
        self.vlm_process: subprocess.Popen[str] | None = None
        self.vlm_thread: threading.Thread | None = None
        self.vlm_stop_event = threading.Event()
        self.vlm_started_at: float | None = None
        self.vlm_last_command: list[str] = []
        self.vlm_run_dir: Path | None = None
        self.vlm_source_mode = "simulation"
        self.vlm_stream_url: str | None = None
        self.vlm_frame_timeout_count = 0
        self.vlm_last_timeout: dict[str, Any] | None = None
        self.vlm_last_error: dict[str, Any] | None = None
        self.vlm_exit_reason: str | None = None
        self.vlm_completed_at: float | None = None
        self.vlm_stop_requested_at: float | None = None
        self.realtime_process: subprocess.Popen[str] | None = None
        self.realtime_started_at: float | None = None
        self.realtime_last_command: list[str] = []
        self.realtime_source_mode = "simulation"
        self.realtime_stream_url: str | None = None

    def public_event_url(self) -> str:
        host = self.server_address[0]
        if host in {"", "0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{self.server_address[1]}/events"

    def vlm_status(self) -> dict[str, Any]:
        thread_running = self.vlm_thread is not None and self.vlm_thread.is_alive()
        return {
            "running": thread_running,
            "pid": self.vlm_process.pid if process_running(self.vlm_process) else None,
            "started_at": self.vlm_started_at,
            "command": self.vlm_last_command,
            "run_dir": str(self.vlm_run_dir) if self.vlm_run_dir else None,
            "source_mode": self.vlm_source_mode,
            "stream_url": self.vlm_stream_url,
            "video_path": str(self.video_path),
            "default_model_key": self.default_vlm_model_key,
            "default_interval_seconds": self.default_vlm_interval_seconds,
            "frame_timeout_seconds": self.vlm_frame_timeout_seconds,
            "frame_timeout_count": self.vlm_frame_timeout_count,
            "last_frame_timeout": self.vlm_last_timeout,
            "last_error": self.vlm_last_error,
            "exit_reason": self.vlm_exit_reason,
            "completed_at": self.vlm_completed_at,
            "stop_requested_at": self.vlm_stop_requested_at,
            "realtime": self.realtime_status(),
        }

    def handle_error(
        self,
        request: Any,
        client_address: tuple[str, int],
    ) -> None:
        exc_type, exc, _traceback_value = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    def realtime_status(self) -> dict[str, Any]:
        return {
            "running": process_running(self.realtime_process),
            "pid": (
                self.realtime_process.pid
                if process_running(self.realtime_process)
                else None
            ),
            "started_at": self.realtime_started_at,
            "command": self.realtime_last_command,
            "source_mode": self.realtime_source_mode,
            "stream_url": self.realtime_stream_url,
            "history_dir": str(REALTIME_HISTORY_DIR),
            "default_video_fps": self.default_realtime_video_fps,
            "no_video": self.realtime_no_video,
            "playback_guard_ms": self.realtime_playback_guard_ms,
            "suppress_mic_during_playback": (
                self.suppress_realtime_mic_during_playback
            ),
        }

    def start_vlm(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.vlm_thread is not None and self.vlm_thread.is_alive():
            self.start_realtime_for_run(payload=payload, run_dir=self.vlm_run_dir)
            status = self.vlm_status()
            status["already_running"] = True
            return status

        model_key = resolve_vlm_model_key(
            payload.get("model_key")
            or payload.get("model_series")
            or self.default_vlm_model_key
        )
        run_dir = create_vlm_run_dir(model_key)
        self.vlm_stop_event = threading.Event()
        self.vlm_started_at = time.time()
        self.vlm_run_dir = run_dir
        self.vlm_last_command = []
        self.vlm_frame_timeout_count = 0
        self.vlm_last_timeout = None
        self.vlm_last_error = None
        self.vlm_exit_reason = "running"
        self.vlm_completed_at = None
        self.vlm_stop_requested_at = None
        self.vlm_source_mode = source_mode_from_payload(
            payload,
            default_source_mode=self.default_source_mode,
        )
        self.vlm_stream_url = (
            stream_url_from_payload(
                payload,
                default_stream_url=self.default_glasses_stream_url,
            )
            if self.vlm_source_mode == "glasses"
            else None
        )
        self.vlm_thread = threading.Thread(
            target=self.run_vlm_thread_entry,
            args=(dict(payload), run_dir),
            daemon=True,
        )
        self.vlm_thread.start()
        self.start_realtime_for_run(payload=payload, run_dir=run_dir)
        return {
            "running": True,
            "pid": None,
            "started_at": self.vlm_started_at,
            "command": [],
            "run_dir": str(run_dir),
            "model_key": model_key,
            "source_mode": self.vlm_source_mode,
            "stream_url": self.vlm_stream_url,
            "video_path": str(self.video_path),
            "default_interval_seconds": self.default_vlm_interval_seconds,
            "frame_timeout_seconds": self.vlm_frame_timeout_seconds,
            "exit_reason": self.vlm_exit_reason,
            "realtime": self.realtime_status(),
            "already_running": False,
        }

    def run_vlm_thread_entry(
        self,
        payload: dict[str, Any],
        run_dir: Path,
    ) -> None:
        try:
            self.run_vlm_realtime_scheduler(payload, run_dir)
            self.vlm_exit_reason = (
                "stopped" if self.vlm_stop_event.is_set() else "finished"
            )
        except Exception as exc:
            self.vlm_exit_reason = "error"
            self.vlm_last_error = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(
                "[lego-backend]"
                f" VLM scheduler crashed: {exc.__class__.__name__}: {exc}",
                flush=True,
            )
            traceback.print_exc()
        finally:
            self.vlm_completed_at = time.time()
            if self.vlm_process is not None and not process_running(self.vlm_process):
                self.vlm_process = None

    def should_start_realtime(self, payload: dict[str, Any]) -> bool:
        value = payload.get("start_realtime")
        if value is None:
            return self.start_realtime_by_default
        return bool(value)

    def start_realtime_for_run(
        self,
        *,
        payload: dict[str, Any],
        run_dir: Path | None,
    ) -> None:
        if not self.should_start_realtime(payload):
            return
        if run_dir is None:
            return
        if process_running(self.realtime_process):
            return

        video_fps = float(
            payload.get("realtime_video_fps") or self.default_realtime_video_fps
        )
        no_video = bool(payload.get("realtime_no_video", self.realtime_no_video))
        playback_guard_ms = int(
            payload.get("realtime_playback_guard_ms")
            or self.realtime_playback_guard_ms
        )
        suppress_mic = bool(
            payload.get(
                "suppress_realtime_mic_during_playback",
                self.suppress_realtime_mic_during_playback,
            )
        )
        source_mode = source_mode_from_payload(
            payload,
            default_source_mode=self.default_source_mode,
        )
        stream_url = (
            stream_url_from_payload(
                payload,
                default_stream_url=self.default_glasses_stream_url,
            )
            if source_mode == "glasses"
            else None
        )
        command = [
            sys.executable,
            str(REALTIME_SCRIPT),
            "--vlm-output",
            str(run_dir / "vlmoutput.json"),
            "--history-dir",
            str(REALTIME_HISTORY_DIR),
            "--video-fps",
            str(video_fps),
            "--playback-guard-ms",
            str(playback_guard_ms),
        ]
        if suppress_mic:
            command.append("--suppress-mic-during-playback")
        else:
            command.append("--no-suppress-mic-during-playback")
        if no_video:
            command.append("--no-video")
        elif source_mode == "glasses" and stream_url:
            command.extend(["--stream-url", stream_url])
        else:
            command.extend(["--video", str(self.video_path)])

        self.realtime_source_mode = source_mode
        self.realtime_stream_url = stream_url
        self.realtime_last_command = command
        self.realtime_started_at = time.time()
        print(
            "[lego-backend]"
            f" start_realtime video_fps={video_fps}"
            f" no_video={int(no_video)}",
            f" source_mode={source_mode}"
            f" stream_url={stream_url or ''}",
            f" mic_suppression={int(suppress_mic)}"
            f" playback_guard_ms={playback_guard_ms}",
            flush=True,
        )
        self.realtime_process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
        )

    def stop_realtime(self) -> None:
        process = self.realtime_process
        if not process_running(process):
            self.realtime_process = None
            return
        assert process is not None
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self.realtime_process = None

    def run_vlm_frame_process(
        self,
        *,
        frame_path: Path,
        frame_time_seconds: float,
        frame_index: int,
        run_dir: Path,
        interval_seconds: float,
        model_key: str,
        payload: dict[str, Any],
    ) -> None:
        command = [
            sys.executable,
            str(VLM_SCRIPT),
            "--frame-path",
            str(frame_path),
            "--frame-time-seconds",
            f"{frame_time_seconds:.3f}",
            "--frame-index",
            str(frame_index),
            "--run-dir",
            str(run_dir),
            "--event-url",
            self.public_event_url(),
            "--interval-seconds",
            str(interval_seconds),
            "--model-key",
            model_key,
        ]
        if payload.get("mock"):
            command.append("--mock")

        self.vlm_last_command = command
        timeout_seconds = float(
            payload.get("vlm_frame_timeout_seconds")
            or payload.get("frame_timeout_seconds")
            or self.vlm_frame_timeout_seconds
        )
        print(
            "[lego-backend]"
            f" frame={frame_index}"
            f" t={format_video_time(frame_time_seconds)}"
            f" run_vlm_single_frame"
            f" timeout={timeout_seconds:.1f}s",
            flush=True,
        )
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
        )
        self.vlm_process = process
        started_at = time.perf_counter()
        timed_out = False
        try:
            while process.poll() is None:
                elapsed = time.perf_counter() - started_at
                if timeout_seconds > 0 and elapsed >= timeout_seconds:
                    timed_out = True
                    self.vlm_frame_timeout_count += 1
                    self.vlm_last_timeout = {
                        "frame_index": frame_index,
                        "time": format_video_time(frame_time_seconds),
                        "frame_path": str(frame_path),
                        "timeout_seconds": round(timeout_seconds, 3),
                        "elapsed_seconds": round(elapsed, 3),
                    }
                    print(
                        "[lego-backend]"
                        f" VLM frame timed out"
                        f" frame={frame_index}"
                        f" t={format_video_time(frame_time_seconds)}"
                        f" elapsed={elapsed:.3f}s;"
                        " skip frame and continue",
                        flush=True,
                    )
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                    break
                wait_seconds = 0.2
                if timeout_seconds > 0:
                    wait_seconds = max(
                        0.01,
                        min(wait_seconds, timeout_seconds - elapsed),
                    )
                if self.vlm_stop_event.wait(wait_seconds):
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                    break
            if (
                process.returncode
                and not timed_out
                and not self.vlm_stop_event.is_set()
            ):
                print(
                    "[lego-backend]"
                    f" VLM frame process exited with code {process.returncode}",
                    flush=True,
                )
        finally:
            if self.vlm_process is process:
                self.vlm_process = None

    def run_vlm_realtime_scheduler(
        self,
        payload: dict[str, Any],
        run_dir: Path,
    ) -> None:
        interval_seconds = float(
            payload.get("interval_seconds") or self.default_vlm_interval_seconds
        )
        start_seconds = float(payload.get("start_seconds") or 0.0)
        max_frames = (
            int(payload["max_frames"])
            if payload.get("max_frames") is not None
            else None
        )
        model_key = resolve_vlm_model_key(
            payload.get("model_key")
            or payload.get("model_series")
            or self.default_vlm_model_key
        )
        frames_dir = run_dir / "frames"
        scheduler_started_at = time.perf_counter()

        source_mode = source_mode_from_payload(
            payload,
            default_source_mode=self.default_source_mode,
        )
        if source_mode == "glasses":
            stream_url = stream_url_from_payload(
                payload,
                default_stream_url=self.default_glasses_stream_url,
            )
            self.run_vlm_stream_scheduler(
                payload=payload,
                run_dir=run_dir,
                frames_dir=frames_dir,
                scheduler_started_at=scheduler_started_at,
                interval_seconds=interval_seconds,
                max_frames=max_frames,
                model_key=model_key,
                stream_url=stream_url,
            )
            return

        end_seconds = (
            float(payload["end_seconds"])
            if payload.get("end_seconds") is not None
            else video_duration(self.video_path)
        )
        end_seconds = min(end_seconds, video_duration(self.video_path))
        include_final_frame = bool(payload.get("include_final_frame", True))
        final_frame_offset_seconds = float(
            payload.get("final_frame_offset_seconds")
            or DEFAULT_FINAL_FRAME_OFFSET_SECONDS
        )
        sample_timestamps = scheduler_sample_timestamps(
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            interval_seconds=interval_seconds,
            max_frames=max_frames,
            include_final_frame=include_final_frame,
            final_frame_offset_seconds=final_frame_offset_seconds,
        )

        try:
            for frame_index, sample_timestamp in enumerate(sample_timestamps):
                if self.vlm_stop_event.is_set():
                    break
                target_elapsed = sample_timestamp - start_seconds
                wait_seconds = scheduler_started_at + target_elapsed - time.perf_counter()
                if wait_seconds > 0:
                    print(
                        "[lego-backend]"
                        f" wait={wait_seconds:.3f}s"
                        f" frame={frame_index}"
                        f" t={format_video_time(sample_timestamp)}",
                        flush=True,
                    )
                    if self.vlm_stop_event.wait(wait_seconds):
                        break

                time_text = format_video_time(sample_timestamp)
                frame_path = (
                    frames_dir
                    / f"frame_{frame_index:04d}_{time_text.replace(':', '')}.png"
                )
                extract_frame(self.video_path, frame_path, sample_timestamp)

                self.run_vlm_frame_process(
                    frame_path=frame_path,
                    frame_time_seconds=sample_timestamp,
                    frame_index=frame_index,
                    run_dir=run_dir,
                    interval_seconds=interval_seconds,
                    model_key=model_key,
                    payload=payload,
                )
        finally:
            self.vlm_process = None

    def run_vlm_stream_scheduler(
        self,
        *,
        payload: dict[str, Any],
        run_dir: Path,
        frames_dir: Path,
        scheduler_started_at: float,
        interval_seconds: float,
        max_frames: int | None,
        model_key: str,
        stream_url: str,
    ) -> None:
        print(
            "[lego-backend]"
            f" VLM glasses stream source={stream_url}"
            f" interval_seconds={interval_seconds}",
            flush=True,
        )
        frame_index = 0
        try:
            while not self.vlm_stop_event.is_set():
                if max_frames is not None and frame_index >= max_frames:
                    break
                target_elapsed = frame_index * interval_seconds
                wait_seconds = scheduler_started_at + target_elapsed - time.perf_counter()
                if wait_seconds > 0:
                    print(
                        "[lego-backend]"
                        f" wait={wait_seconds:.3f}s"
                        f" frame={frame_index}"
                        f" source=glasses",
                        flush=True,
                    )
                    if self.vlm_stop_event.wait(wait_seconds):
                        break

                frame_time_seconds = max(0.0, time.perf_counter() - scheduler_started_at)
                frame_path = frames_dir / f"frame_{frame_index:04d}_{int(frame_time_seconds * 1000):08d}.jpg"
                try:
                    extract_stream_frame(stream_url, frame_path)
                except (TimeoutError, OSError, urllib.error.URLError, ValueError) as exc:
                    print(
                        "[lego-backend]"
                        f" glasses stream capture failed frame={frame_index}: {exc}",
                        flush=True,
                    )
                    frame_index += 1
                    continue

                self.run_vlm_frame_process(
                    frame_path=frame_path,
                    frame_time_seconds=frame_time_seconds,
                    frame_index=frame_index,
                    run_dir=run_dir,
                    interval_seconds=interval_seconds,
                    model_key=model_key,
                    payload=payload,
                )
                frame_index += 1
        finally:
            self.vlm_process = None

    def stop_vlm(self) -> dict[str, Any]:
        thread_running = self.vlm_thread is not None and self.vlm_thread.is_alive()
        process = self.vlm_process
        realtime_running = process_running(self.realtime_process)
        if not thread_running and not process_running(process) and not realtime_running:
            return self.vlm_status()

        self.vlm_stop_event.set()
        self.vlm_stop_requested_at = time.time()
        self.stop_realtime()
        if process_running(process):
            assert process is not None
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if self.vlm_thread is not None and self.vlm_thread.is_alive():
            self.vlm_thread.join(timeout=5)
        return self.vlm_status()


def main() -> int:
    args = parse_args()
    video_path = args.video.expanduser()
    if not video_path.is_absolute():
        video_path = PROJECT_ROOT / video_path
    rawdata_path = args.rawdata.expanduser()
    if not rawdata_path.is_absolute():
        rawdata_path = PROJECT_ROOT / rawdata_path
    rawdata_path = rawdata_path.resolve()
    default_vlm_model_key = resolve_vlm_model_key(
        args.vlm_model_key or args.vlm_model_series
    )
    server = LegoAgentBackendServer(
        (args.host, args.port),
        video_path=video_path,
        default_glasses_stream_url=args.glasses_stream_url,
        default_source_mode=args.source_mode,
        rawdata_path=rawdata_path,
        default_vlm_model_key=default_vlm_model_key,
        default_vlm_interval_seconds=float(args.vlm_interval_seconds),
        vlm_frame_timeout_seconds=float(args.vlm_frame_timeout_seconds),
        start_realtime_by_default=not args.no_realtime,
        default_realtime_video_fps=float(args.realtime_video_fps),
        realtime_no_video=bool(args.realtime_no_video),
        realtime_playback_guard_ms=int(args.realtime_playback_guard_ms),
        suppress_realtime_mic_during_playback=not args.no_realtime_mic_suppression,
    )
    print(f"[lego-backend] serving video={video_path}", flush=True)
    print(f"[lego-backend] default_source_mode={args.source_mode}", flush=True)
    print(
        f"[lego-backend] default_glasses_stream_url={args.glasses_stream_url}",
        flush=True,
    )
    print(f"[lego-backend] serving rawdata={rawdata_path}", flush=True)
    print(f"[lego-backend] default_vlm_model_key={default_vlm_model_key}", flush=True)
    print(
        f"[lego-backend] default_vlm_interval_seconds={args.vlm_interval_seconds}",
        flush=True,
    )
    print(
        f"[lego-backend] vlm_frame_timeout_seconds={args.vlm_frame_timeout_seconds}",
        flush=True,
    )
    print(
        "[lego-backend]"
        f" realtime_auto_start={int(not args.no_realtime)}"
        f" realtime_video_fps={args.realtime_video_fps}"
        f" realtime_no_video={int(args.realtime_no_video)}",
        f" realtime_mic_suppression={int(not args.no_realtime_mic_suppression)}"
        f" realtime_playback_guard_ms={args.realtime_playback_guard_ms}",
        flush=True,
    )
    print(f"[lego-backend] events=http://{args.host}:{args.port}/events", flush=True)
    print(f"[lego-backend] start=http://{args.host}:{args.port}/vlm/start", flush=True)
    print(f"[lego-backend] stop=http://{args.host}:{args.port}/vlm/stop", flush=True)
    print(f"[lego-backend] video=http://{args.host}:{args.port}/video/step8test.mp4", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop_vlm()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
