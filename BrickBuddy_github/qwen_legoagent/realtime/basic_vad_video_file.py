#!/usr/bin/env python3
"""Minimal Qwen Omni Realtime VAD demo.

Audio is read from the local microphone. Video frames are sampled from a test
file or image directory and appended only immediately after an audio chunk.
That ordering matters for Qwen Realtime WebSocket image input.
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.request

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional convenience.
    load_dotenv = None

try:
    import pyaudio
except ImportError:  # pragma: no cover - depends on local audio setup.
    pyaudio = None

try:
    import dashscope
    from dashscope.audio.qwen_omni import (
        AudioFormat,
        MultiModality,
        OmniRealtimeCallback,
        OmniRealtimeConversation,
    )
except ImportError:  # pragma: no cover - validated at runtime.
    dashscope = None
    AudioFormat = None
    MultiModality = None
    OmniRealtimeCallback = object
    OmniRealtimeConversation = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover - only needed for non-JPEG frames.
    Image = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "qwen3.5-omni-flash-realtime"
DEFAULT_REALTIME_URL = (
    "wss://llm-k3p346791hkdiqy6.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
)
DEFAULT_ASR_MODEL = "qwen3-asr-flash-realtime"
DEFAULT_SYSTEM_PROMPT_FILE = Path(__file__).with_name("system_prompt.md")
REALTIME_HISTORY_DIR = Path(__file__).with_name("realtimehistory")
FALLBACK_INSTRUCTIONS = (
    "You are a concise realtime Lego assembly assistant. "
    "Reply in the same language as the user. "
    "Use the video frames only as visual context."
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def slugify_filename_part(value: object) -> str:
    text = str(value or "").strip()
    cleaned = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in text
    ).strip("-")
    return cleaned[:80] or "unknown"


def model_series_for_history(model: str) -> str:
    lowered = model.lower()
    if lowered.startswith("qwen"):
        return "qwen"
    if lowered.startswith("gpt"):
        return "gpt"
    if lowered.startswith("gemini"):
        return "gemini"
    return slugify_filename_part(model)


def create_realtime_run_dir(model: str, history_root: Path | None = None) -> Path:
    root = history_root or REALTIME_HISTORY_DIR
    run_dir_prefix = f"realtime-{model_series_for_history(model)}"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = root / f"{run_dir_prefix}-{stamp}"
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
        return base
    for index in range(1, 100):
        candidate = root / f"{run_dir_prefix}-{stamp}-{index:02d}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
    raise RuntimeError("Unable to create unique realtime run directory")


class RealtimeHistoryLogger:
    """Persist one realtime run as compact CSV plus raw JSONL records."""

    CSV_FIELDS = ("id", "turn_id", "role", "timestamp", "message", "audiopath")

    def __init__(
        self,
        run_dir: Path,
        *,
        model: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.model = model
        self.metadata = metadata or {}
        self.started_at = time.time()
        self.lock = threading.Lock()
        self.sequence = 0
        self.csv_sequence = 0
        self.turn_sequence = 0
        self.closed = False
        self.pending_triggers: list[dict[str, Any]] = []
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir = self.run_dir / "audio"
        self.video_dir = self.run_dir / "video"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.video_path = self.video_dir / f"{self.run_dir.name}.mp4"
        self.realtime_log_path = self.run_dir / "realtimelog.csv"
        self.event_log_path = self.run_dir / "events.jsonl"
        self.raw_chat_history_path = self.run_dir / "realtime_rawchathistory.jsonl"
        self.manifest_path = self.run_dir / "run_manifest.json"
        self._init_csv()
        self._write_json(
            self.manifest_path,
            {
                "model": self.model,
                "started_at": datetime.fromtimestamp(self.started_at).isoformat(),
                "run_dir": str(self.run_dir),
                "files": {
                    "realtimelog": str(self.realtime_log_path),
                    "audio_dir": str(self.audio_dir),
                    "video_path": str(self.video_path),
                    "realtime_rawchathistory": str(self.raw_chat_history_path),
                    "events": str(self.event_log_path),
                },
                "metadata": self.metadata,
            },
        )
        self.record_event("run_started", {"metadata": self.metadata})

    @classmethod
    def create(
        cls,
        *,
        model: str,
        history_dir: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "RealtimeHistoryLogger":
        return cls(
            create_realtime_run_dir(model, history_dir),
            model=model,
            metadata=metadata,
        )

    def close(self, stats: dict[str, Any] | None = None) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.record_event("run_finished", {"stats": stats or {}})
        manifest = self._read_json(self.manifest_path)
        manifest["finished_at"] = datetime.now().isoformat()
        manifest["stats"] = stats or {}
        self._write_json(self.manifest_path, manifest)

    def record_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self._append(self.event_log_path, self._base_record(event_type, payload or {}))

    def attach_video_source(self, source_path: Path) -> Path | None:
        if not source_path.exists() or not source_path.is_file():
            return None
        suffix = source_path.suffix or ".mp4"
        self.video_path = self.video_dir / f"{self.run_dir.name}{suffix}"
        shutil.copy2(source_path, self.video_path)
        self._update_manifest_file("video_path", str(self.video_path))
        self.record_event(
            "video_source_saved",
            {"source_path": str(source_path), "video_path": str(self.video_path)},
        )
        return self.video_path

    def prepare_video_placeholder(self, reason: str) -> Path:
        self.video_path.touch(exist_ok=True)
        self._update_manifest_file("video_path", str(self.video_path))
        self.record_event(
            "video_placeholder_created",
            {"reason": reason, "video_path": str(self.video_path)},
        )
        return self.video_path

    def enqueue_step_trigger(
        self,
        *,
        step: int,
        prompt: str,
        vlm_event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        turn_id = self._next_turn_id()
        trigger = {
            "trigger": "step",
            "turn_id": turn_id,
            "step": step,
            "prompt": prompt,
            "vlm_event": vlm_event or {},
        }
        with self.lock:
            self.pending_triggers.append(trigger)
        record = self._base_record("step_prompt_sent", trigger)
        self._append(self.raw_chat_history_path, record)
        self._append_csv_row(
            turn_id=turn_id,
            role="autostep",
            message=prompt,
            audiopath="",
        )
        return trigger

    def enqueue_user_trigger(
        self,
        *,
        transcript: str,
        event: dict[str, Any] | None = None,
        audio_bytes: bytes | None = None,
        audio_sample_rate: int = 16000,
    ) -> dict[str, Any]:
        turn_id = self._next_turn_id()
        audio_path = self.save_audio(
            role="user",
            turn_id=turn_id,
            audio_bytes=audio_bytes,
            sample_rate=audio_sample_rate,
        )
        trigger = {
            "trigger": "user",
            "turn_id": turn_id,
            "user_transcript": transcript,
            "audiopath": str(audio_path) if audio_path else "",
            "event": self._compact_event(event or {}),
        }
        with self.lock:
            self.pending_triggers.append(trigger)
        record = self._base_record("user_transcript", trigger)
        self._append(self.raw_chat_history_path, record)
        self._append_csv_row(
            turn_id=turn_id,
            role="user",
            message=transcript,
            audiopath=str(audio_path) if audio_path else "",
        )
        return trigger

    def record_assistant_response(
        self,
        *,
        transcript: str,
        event: dict[str, Any] | None = None,
        audio_bytes: bytes | None = None,
        audio_sample_rate: int = 24000,
    ) -> None:
        with self.lock:
            trigger = self.pending_triggers.pop(0) if self.pending_triggers else None
        if trigger is None:
            trigger = {"trigger": "unknown", "turn_id": self._next_turn_id()}
        turn_id = int(trigger.get("turn_id") or self._next_turn_id())
        audio_path = self.save_audio(
            role="model",
            turn_id=turn_id,
            audio_bytes=audio_bytes,
            sample_rate=audio_sample_rate,
        )
        payload = {
            "trigger": trigger.get("trigger", "unknown"),
            "turn_id": turn_id,
            "assistant_transcript": transcript,
            "audiopath": str(audio_path) if audio_path else "",
            "trigger_context": trigger,
            "event": self._compact_event(event or {}),
        }
        record = self._base_record("assistant_response", payload)
        self._append(self.raw_chat_history_path, record)
        self._append_csv_row(
            turn_id=turn_id,
            role="model",
            message=transcript,
            audiopath=str(audio_path) if audio_path else "",
        )

    def save_audio(
        self,
        *,
        role: str,
        turn_id: int,
        audio_bytes: bytes | None,
        sample_rate: int,
        channels: int = 1,
        sample_width: int = 2,
    ) -> Path | None:
        if not audio_bytes:
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        stem = f"turn{turn_id}_{role}_{timestamp}"
        mp3_path = self.audio_dir / f"{stem}.mp3"
        wav_path = self.audio_dir / f".{stem}.wav"
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_bytes)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            fallback_path = self.audio_dir / f"{stem}.wav"
            wav_path.replace(fallback_path)
            self.record_event(
                "audio_mp3_conversion_unavailable",
                {"role": role, "turn_id": turn_id, "audiopath": str(fallback_path)},
            )
            return fallback_path
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(wav_path),
                    "-codec:a",
                    "libmp3lame",
                    "-q:a",
                    "4",
                    str(mp3_path),
                ],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            fallback_path = self.audio_dir / f"{stem}.wav"
            wav_path.replace(fallback_path)
            self.record_event(
                "audio_mp3_conversion_failed",
                {
                    "role": role,
                    "turn_id": turn_id,
                    "audiopath": str(fallback_path),
                    "error": str(exc),
                },
            )
            return fallback_path
        try:
            wav_path.unlink()
        except OSError:
            pass
        return mp3_path

    def _base_record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
        now = time.time()
        return {
            "sequence": sequence,
            "type": event_type,
            "timestamp": datetime.fromtimestamp(now).isoformat(),
            "elapsed_seconds": round(now - self.started_at, 3),
            "payload": payload,
        }

    def _append(self, path: Path, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self.lock:
            with path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")

    def _append_csv_row(
        self,
        *,
        turn_id: int,
        role: str,
        message: str,
        audiopath: str,
    ) -> None:
        with self.lock:
            self.csv_sequence += 1
            row_id = self.csv_sequence
            timestamp = datetime.now().isoformat()
            with self.realtime_log_path.open("a", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=self.CSV_FIELDS)
                writer.writerow(
                    {
                        "id": row_id,
                        "turn_id": turn_id,
                        "role": role,
                        "timestamp": timestamp,
                        "message": message,
                        "audiopath": audiopath,
                    }
                )

    def _init_csv(self) -> None:
        with self.realtime_log_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=self.CSV_FIELDS)
            writer.writeheader()

    def _next_turn_id(self) -> int:
        with self.lock:
            self.turn_sequence += 1
            return self.turn_sequence

    def _update_manifest_file(self, key: str, value: str) -> None:
        manifest = self._read_json(self.manifest_path)
        files = manifest.setdefault("files", {})
        files[key] = value
        self._write_json(self.manifest_path, manifest)

    def _compact_event(self, event: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key in ("type", "event_id", "item_id", "response_id", "transcript", "text"):
            if key in event:
                compact[key] = event[key]
        response = event.get("response")
        if isinstance(response, dict):
            compact["response"] = {
                key: response.get(key)
                for key in ("id", "status", "status_details")
                if key in response
            }
        return compact

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
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
            "User-Agent": "LegoGlass-Realtime/1.0",
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


def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / "agent" / ".env")


def default_instructions() -> str:
    try:
        prompt = DEFAULT_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return FALLBACK_INSTRUCTIONS
    return prompt or FALLBACK_INSTRUCTIONS


def require_runtime() -> None:
    missing: list[str] = []
    if pyaudio is None:
        missing.append("pyaudio")
    if dashscope is None or OmniRealtimeConversation is None:
        missing.append("dashscope")
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing dependency: {joined}. Run `uv sync` first.")


def list_audio_devices() -> None:
    require_runtime()
    audio = pyaudio.PyAudio()
    try:
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            inputs = int(info.get("maxInputChannels", 0))
            outputs = int(info.get("maxOutputChannels", 0))
            if inputs or outputs:
                print(
                    f"{index}: {info.get('name')} "
                    f"(inputs={inputs}, outputs={outputs}, "
                    f"default_rate={info.get('defaultSampleRate')})"
                )
    finally:
        audio.terminate()


class PCMOutputPlayer:
    def __init__(
        self,
        audio: Any,
        *,
        enabled: bool,
        output_device_index: int | None,
    ) -> None:
        self.audio = audio
        self.enabled = enabled
        self.output_device_index = output_device_index
        self.stream: Any | None = None
        self.queue: queue.Queue[bytes | None] = queue.Queue()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._is_writing = False
        self._active_until = 0.0

    def start(self) -> None:
        if not self.enabled:
            return
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=24000,
            output=True,
            output_device_index=self.output_device_index,
            frames_per_buffer=2400,
        )
        self.thread = threading.Thread(target=self._run, name="qwen-pcm-output")
        self.thread.start()

    def add_pcm(self, data: bytes) -> None:
        if self.enabled and data:
            duration_seconds = len(data) / (24000 * 2)
            with self._state_lock:
                self._active_until = max(
                    self._active_until,
                    time.monotonic() + duration_seconds,
                )
            self.queue.put(data)

    def is_active(self, grace_seconds: float = 0.0) -> bool:
        if not self.enabled:
            return False
        now = time.monotonic()
        with self._state_lock:
            if self._is_writing or now < self._active_until + grace_seconds:
                return True
        return not self.queue.empty()

    def cancel(self) -> None:
        with self.queue.mutex:
            self.queue.queue.clear()
            self.queue.unfinished_tasks = 0
            self.queue.all_tasks_done.notify_all()
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.start_stream()
            except Exception:
                pass

    def close(self) -> None:
        self.stop_event.set()
        self.queue.put(None)
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                data = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if data is None:
                break
            if self.stream is None:
                continue
            try:
                with self._state_lock:
                    self._is_writing = True
                self.stream.write(data)
                with self._state_lock:
                    self._active_until = max(self._active_until, time.monotonic())
            except Exception as exc:
                print(f"\n[playback error] {exc}", flush=True)
            finally:
                with self._state_lock:
                    self._is_writing = False


class VideoFrameSource:
    def __init__(
        self,
        path: Path,
        *,
        fps: float,
        max_width: int,
        loop: bool,
    ) -> None:
        if fps <= 0:
            raise ValueError("--video-fps must be greater than zero")
        self.path = path
        self.fps = fps
        self.max_width = max_width
        self.loop = loop
        self.period_seconds = 1.0 / fps
        self.next_frame_at = 0.0
        self.index = 0
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self.frames = self._load_frames()
        if not self.frames:
            raise RuntimeError(f"No video frames found in {path}")

    def close(self) -> None:
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    def maybe_next_b64(self, now: float) -> str | None:
        if now < self.next_frame_at:
            return None
        if self.index >= len(self.frames):
            if not self.loop:
                return None
            self.index = 0

        frame = self.frames[self.index]
        self.index += 1
        self.next_frame_at = now + self.period_seconds
        return self._encode_image_b64(frame)

    def _load_frames(self) -> list[Path]:
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        if self.path.is_dir():
            return sorted(
                child
                for child in self.path.iterdir()
                if child.suffix.lower() in IMAGE_SUFFIXES
            )
        if self.path.suffix.lower() in IMAGE_SUFFIXES:
            return [self.path]
        return self._extract_video_frames()

    def _extract_video_frames(self) -> list[Path]:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "Video file input requires ffmpeg. Install ffmpeg or pass an image directory."
            )
        self._tempdir = tempfile.TemporaryDirectory(prefix="qwen_realtime_frames_")
        output_pattern = str(Path(self._tempdir.name) / "frame_%06d.jpg")
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(self.path),
                "-vf",
                f"fps={self.fps},scale={self.max_width}:-2",
                "-q:v",
                "4",
                output_pattern,
            ],
            check=True,
        )
        return sorted(Path(self._tempdir.name).glob("frame_*.jpg"))

    def _encode_image_b64(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return base64.b64encode(path.read_bytes()).decode("ascii")
        if Image is None:
            raise RuntimeError("Pillow is required for non-JPEG image frames.")
        with Image.open(path) as image:
            image = image.convert("RGB")
            if image.width > self.max_width:
                height = max(1, round(image.height * self.max_width / image.width))
                image = image.resize((self.max_width, height))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85)
        return base64.b64encode(output.getvalue()).decode("ascii")


class HttpJpegFrameSource:
    def __init__(
        self,
        stream_url: str,
        *,
        fps: float,
        max_width: int,
        timeout: float = 3.0,
    ) -> None:
        if fps <= 0:
            raise ValueError("--video-fps must be greater than zero")
        self.stream_url = stream_url
        self.fps = fps
        self.max_width = max_width
        self.timeout = timeout
        self.period_seconds = 1.0 / fps
        self.next_frame_at = 0.0

    def close(self) -> None:
        return

    def maybe_next_b64(self, now: float) -> str | None:
        if now < self.next_frame_at:
            return None
        self.next_frame_at = now + self.period_seconds
        try:
            image_bytes = capture_jpeg_bytes_from_stream(
                self.stream_url,
                timeout=self.timeout,
            )
        except Exception as exc:
            print(f"[video] stream frame capture failed: {exc}", flush=True)
            return None
        return self._encode_image_b64(image_bytes)

    def _encode_image_b64(self, image_bytes: bytes) -> str:
        if Image is None:
            return base64.b64encode(image_bytes).decode("ascii")
        with Image.open(io.BytesIO(image_bytes)) as image:
            image = image.convert("RGB")
            if image.width > self.max_width:
                height = max(1, round(image.height * self.max_width / image.width))
                image = image.resize((self.max_width, height))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85)
        return base64.b64encode(output.getvalue()).decode("ascii")


class VideoRunRecorder:
    def __init__(self, source_url: str, output_path: Path) -> None:
        self.source_url = source_url
        self.output_path = output_path
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> bool:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        # Record every frame ffmpeg can receive from the HTTP stream. The
        # realtime --video-fps option only controls frames sent to Qwen.
        self.process = subprocess.Popen(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-use_wallclock_as_timestamps",
                "1",
                "-i",
                self.source_url,
                "-an",
                "-vf",
                "setpts=PTS-STARTPTS",
                "-fps_mode",
                "vfr",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(self.output_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return True

    def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        if self.process is None:
            return {"status": "not_started", "video_path": str(self.output_path)}
        if self.process.poll() is None:
            if self.process.stdin is not None:
                try:
                    self.process.stdin.write(b"q")
                    self.process.stdin.flush()
                    self.process.stdin.close()
                except Exception:
                    pass
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=timeout)
        stderr = b""
        if self.process.stderr is not None:
            try:
                stderr = self.process.stderr.read()
            except Exception:
                stderr = b""
        exists = self.output_path.exists()
        size = self.output_path.stat().st_size if exists else 0
        if not exists:
            self.output_path.touch(exist_ok=True)
        return {
            "status": "saved" if size > 0 else "placeholder",
            "returncode": self.process.returncode,
            "video_path": str(self.output_path),
            "bytes": size,
            "stderr": stderr.decode("utf-8", errors="replace")[-1000:],
        }


class BasicRealtimeCallback(OmniRealtimeCallback):
    def __init__(
        self,
        player: PCMOutputPlayer,
        history_logger: RealtimeHistoryLogger | None = None,
    ) -> None:
        self.player = player
        self.history_logger = history_logger
        self.response_text_parts: list[str] = []
        self.logged_response_ids: set[str] = set()
        self._audio_lock = threading.Lock()
        self._collecting_user_audio = False
        self._user_audio_chunks: list[bytes] = []
        self._response_audio_chunks: dict[str, list[bytes]] = {}
        self._latest_response_audio_key: str | None = None

    def record_user_audio_chunk(self, data: bytes) -> None:
        if not data:
            return
        with self._audio_lock:
            if self._collecting_user_audio:
                self._user_audio_chunks.append(data)

    def on_open(self) -> None:
        print("[connection] opened", flush=True)
        if self.history_logger is not None:
            self.history_logger.record_event("connection_opened")

    def on_close(
        self,
        close_status_code: int | None = None,
        close_msg: str | None = None,
    ) -> None:
        print(
            f"\n[connection] closed code={close_status_code} msg={close_msg}",
            flush=True,
        )
        if self.history_logger is not None:
            self.history_logger.record_event(
                "connection_closed",
                {"code": close_status_code, "message": close_msg},
            )

    def on_error(self, error: Any) -> None:
        print(f"\n[error] {error}", flush=True)
        if self.history_logger is not None:
            self.history_logger.record_event("client_error", {"error": str(error)})

    def on_event(self, response: dict[str, Any]) -> None:
        event_type = response.get("type")
        try:
            if event_type == "session.created":
                session_id = response.get("session", {}).get("id")
                print(f"[session] created {session_id}", flush=True)
                if self.history_logger is not None:
                    self.history_logger.record_event(
                        "session_created",
                        {"session_id": session_id},
                    )
            elif event_type == "session.updated":
                print("[session] updated: server_vad enabled", flush=True)
                if self.history_logger is not None:
                    self.history_logger.record_event("session_updated")
            elif event_type == "input_audio_buffer.speech_started":
                print("\n[VAD] speech started", flush=True)
                self.player.cancel()
                with self._audio_lock:
                    self._collecting_user_audio = True
                    self._user_audio_chunks = []
                if self.history_logger is not None:
                    self.history_logger.record_event("speech_started")
            elif event_type == "input_audio_buffer.speech_stopped":
                print("[VAD] speech stopped", flush=True)
                with self._audio_lock:
                    self._collecting_user_audio = False
                if self.history_logger is not None:
                    self.history_logger.record_event("speech_stopped")
            elif event_type == "input_audio_buffer.committed":
                print("[VAD] audio committed", flush=True)
                if self.history_logger is not None:
                    self.history_logger.record_event("audio_committed")
            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "")
                print(f"\n[user] {transcript}", flush=True)
                with self._audio_lock:
                    user_audio_bytes = b"".join(self._user_audio_chunks)
                    self._user_audio_chunks = []
                    self._collecting_user_audio = False
                if self.history_logger is not None:
                    self.history_logger.enqueue_user_trigger(
                        transcript=transcript,
                        event=response,
                        audio_bytes=user_audio_bytes,
                    )
            elif event_type == "response.audio.delta":
                delta = response.get("delta")
                if delta:
                    pcm = base64.b64decode(delta)
                    self.player.add_pcm(pcm)
                    response_key = self._response_audio_key(response)
                    with self._audio_lock:
                        self._latest_response_audio_key = response_key
                        self._response_audio_chunks.setdefault(response_key, []).append(
                            pcm
                        )
            elif event_type in {"response.audio_transcript.delta", "response.text.delta"}:
                delta = response.get("delta", "")
                if delta:
                    self.response_text_parts.append(delta)
                    print(delta, end="", flush=True)
            elif event_type in {"response.audio_transcript.done", "response.text.done"}:
                transcript = response.get("transcript") or response.get("text")
                final_transcript = transcript or "".join(self.response_text_parts).strip()
                if transcript and not self.response_text_parts:
                    print(f"\n[assistant] {transcript}", flush=True)
                elif self.response_text_parts:
                    print("", flush=True)
                if final_transcript and self.history_logger is not None:
                    response_key = self._response_key(response)
                    if response_key is None or response_key not in self.logged_response_ids:
                        if response_key is not None:
                            self.logged_response_ids.add(response_key)
                        audio_bytes = self._pop_response_audio(response_key)
                        self.history_logger.record_assistant_response(
                            transcript=final_transcript,
                            event=response,
                            audio_bytes=audio_bytes,
                        )
                self.response_text_parts.clear()
            elif event_type == "response.done":
                print("[response] done", flush=True)
                if self.history_logger is not None:
                    self.history_logger.record_event(
                        "response_done",
                        self.history_logger._compact_event(response),
                    )
            elif event_type == "error":
                print(f"\n[server error] {response}", flush=True)
                if self.history_logger is not None:
                    self.history_logger.record_event("server_error", response)
        except Exception as exc:
            print(f"\n[callback error] {exc}", flush=True)
            if self.history_logger is not None:
                self.history_logger.record_event("callback_error", {"error": str(exc)})

    def _response_key(self, response: dict[str, Any]) -> str | None:
        for key in ("response_id", "item_id", "event_id"):
            value = response.get(key)
            if value:
                return str(value)
        nested_response = response.get("response")
        if isinstance(nested_response, dict) and nested_response.get("id"):
            return str(nested_response["id"])
        return None

    def _response_audio_key(self, response: dict[str, Any]) -> str:
        for key in ("response_id", "item_id"):
            value = response.get(key)
            if value:
                return str(value)
        nested_response = response.get("response")
        if isinstance(nested_response, dict) and nested_response.get("id"):
            return str(nested_response["id"])
        return "unknown"

    def _pop_response_audio(self, response_key: str | None) -> bytes:
        with self._audio_lock:
            key = response_key
            if key is None or key not in self._response_audio_chunks:
                key = self._latest_response_audio_key
            if key is None:
                return b""
            chunks = self._response_audio_chunks.pop(key, [])
            if key == self._latest_response_audio_key:
                self._latest_response_audio_key = None
        return b"".join(chunks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal Qwen Realtime server_vad demo with mic audio and test video."
    )
    parser.add_argument("--video", type=Path, help="Path to a video file or image directory.")
    parser.add_argument("--stream-url", help="HTTP MJPEG glasses stream URL.")
    parser.add_argument("--video-fps", type=float, default=1.0)
    parser.add_argument("--video-max-width", type=int, default=640)
    parser.add_argument("--no-video-loop", action="store_true")
    parser.add_argument("--model", default=os.getenv("QWEN_REALTIME_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--url",
        default=os.getenv("DASHSCOPE_REALTIME_URL", DEFAULT_REALTIME_URL),
    )
    parser.add_argument("--voice", default=os.getenv("QWEN_REALTIME_VOICE", "Ethan"))
    parser.add_argument(
        "--asr-model",
        default=os.getenv("QWEN_REALTIME_ASR_MODEL", DEFAULT_ASR_MODEL),
    )
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--silence-duration-ms", type=int, default=800)
    parser.add_argument("--chunk-ms", type=int, default=200)
    parser.add_argument("--duration", type=float, help="Optional max run time in seconds.")
    parser.add_argument("--instructions", default=default_instructions())
    parser.add_argument("--input-device-index", type=int)
    parser.add_argument("--output-device-index", type=int)
    parser.add_argument("--no-playback", action="store_true")
    parser.add_argument(
        "--history-dir",
        type=Path,
        default=None,
        help=(
            "Root directory for per-run realtime history. Defaults to "
            "qwen_legoagent/realtime/realtimehistory."
        ),
    )
    parser.add_argument(
        "--suppress-mic-during-playback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Do not send microphone chunks while assistant audio is playing.",
    )
    parser.add_argument(
        "--playback-guard-ms",
        type=int,
        default=1200,
        help="Extra silence window after playback before microphone audio is sent again.",
    )
    parser.add_argument("--list-devices", action="store_true")
    return parser.parse_args()


def open_microphone(audio: Any, args: argparse.Namespace) -> Any:
    chunk_frames = max(160, int(16000 * args.chunk_ms / 1000))
    return audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        input_device_index=args.input_device_index,
        frames_per_buffer=chunk_frames,
    )


def update_session(conversation: Any, args: argparse.Namespace) -> None:
    conversation.update_session(
        output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
        voice=args.voice,
        input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
        output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
        enable_input_audio_transcription=True,
        input_audio_transcription_model=args.asr_model,
        enable_turn_detection=True,
        turn_detection_type="server_vad",
        turn_detection_threshold=args.threshold,
        turn_detection_silence_duration_ms=args.silence_duration_ms,
        instructions=args.instructions,
    )


def append_audio_and_maybe_video(
    conversation: Any,
    audio_data: bytes,
    video_source: VideoFrameSource | HttpJpegFrameSource | None,
) -> tuple[bool, bool]:
    audio_b64 = base64.b64encode(audio_data).decode("ascii")
    conversation.append_audio(audio_b64)

    sent_video = False
    if video_source is not None:
        frame_b64 = video_source.maybe_next_b64(time.monotonic())
        if frame_b64:
            conversation.append_video(frame_b64)
            sent_video = True
    return True, sent_video


def run(args: argparse.Namespace) -> None:
    require_runtime()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Set DASHSCOPE_API_KEY in the shell or project .env file.")
    dashscope.api_key = api_key

    video_source = None
    if args.stream_url:
        video_source = HttpJpegFrameSource(
            args.stream_url,
            fps=args.video_fps,
            max_width=args.video_max_width,
        )
    elif args.video:
        video_source = VideoFrameSource(
            args.video,
            fps=args.video_fps,
            max_width=args.video_max_width,
            loop=not args.no_video_loop,
        )

    audio = pyaudio.PyAudio()
    mic_stream = None
    conversation = None
    video_recorder = None
    history_logger = RealtimeHistoryLogger.create(
        model=args.model,
        history_dir=args.history_dir,
        metadata={
            "entrypoint": Path(__file__).name,
            "video": str(args.video) if args.video else None,
            "stream_url": args.stream_url,
            "video_fps": args.video_fps,
            "asr_model": args.asr_model,
            "voice": args.voice,
        },
    )
    print(f"[history] writing {history_logger.run_dir}", flush=True)
    if args.stream_url:
        video_recorder = VideoRunRecorder(args.stream_url, history_logger.video_path)
        if video_recorder.start():
            history_logger.record_event(
                "video_recording_started",
                {"source_url": args.stream_url, "video_path": str(history_logger.video_path)},
            )
        else:
            history_logger.prepare_video_placeholder("ffmpeg is not available")
    elif args.video and args.video.is_file():
        history_logger.attach_video_source(args.video)
    elif args.video:
        history_logger.prepare_video_placeholder(
            "video input is not a single mp4 file"
        )
    stop_event = threading.Event()

    def request_stop(signum: int, _frame: Any) -> None:
        print(f"\n[signal] {signum}, stopping...", flush=True)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    player = PCMOutputPlayer(
        audio,
        enabled=not args.no_playback,
        output_device_index=args.output_device_index,
    )

    try:
        mic_stream = open_microphone(audio, args)
        player.start()
        callback = BasicRealtimeCallback(player, history_logger=history_logger)
        conversation = OmniRealtimeConversation(
            model=args.model,
            callback=callback,
            url=args.url,
            api_key=api_key,
        )
        print(f"[connection] connecting model={args.model}", flush=True)
        conversation.connect()
        update_session(conversation, args)

        chunk_frames = max(160, int(16000 * args.chunk_ms / 1000))
        started_at = time.monotonic()
        audio_chunks = 0
        suppressed_audio_chunks = 0
        video_frames = 0
        print(
            "[ready] server_vad is active. Speak into the microphone; press Ctrl+C to stop.",
            flush=True,
        )
        if video_source is not None:
            if args.stream_url:
                print(f"[video] using stream_url={args.stream_url}", flush=True)
            else:
                print(
                    f"[video] loaded {len(video_source.frames)} frame(s) from {args.video}",
                    flush=True,
                )

        while not stop_event.is_set():
            if args.duration and time.monotonic() - started_at >= args.duration:
                break
            audio_data = mic_stream.read(chunk_frames, exception_on_overflow=False)
            if args.suppress_mic_during_playback and player.is_active(
                args.playback_guard_ms / 1000
            ):
                suppressed_audio_chunks += 1
                continue
            _, sent_video = append_audio_and_maybe_video(
                conversation,
                audio_data,
                video_source,
            )
            callback.record_user_audio_chunk(audio_data)
            audio_chunks += 1
            if sent_video:
                video_frames += 1
                print(f"[video] appended frame #{video_frames}", flush=True)

        print(
            f"\n[stats] audio_chunks={audio_chunks}"
            f" suppressed_audio_chunks={suppressed_audio_chunks}"
            f" video_frames={video_frames}",
            flush=True,
        )
        history_logger.close(
            {
                "audio_chunks": audio_chunks,
                "suppressed_audio_chunks": suppressed_audio_chunks,
                "video_frames": video_frames,
            }
        )
    finally:
        stop_event.set()
        player.close()
        if mic_stream is not None:
            try:
                mic_stream.stop_stream()
                mic_stream.close()
            except Exception:
                pass
        if conversation is not None:
            try:
                conversation.end_session()
            except Exception:
                pass
            try:
                conversation.close()
            except Exception:
                pass
        if video_recorder is not None:
            history_logger.record_event(
                "video_recording_finished",
                video_recorder.stop(),
            )
        if video_source is not None:
            video_source.close()
        if history_logger is not None:
            history_logger.close()
        audio.terminate()


def main() -> int:
    load_env_files()
    args = parse_args()
    if args.list_devices:
        list_audio_devices()
        return 0
    try:
        run(args)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
