#!/usr/bin/env python3
"""Run Qwen Realtime with builtin Mac audio and page-change turn prompts."""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    import dashscope
    from dashscope.audio.qwen_omni import (
        AudioFormat,
        MultiModality,
        OmniRealtimeConversation,
    )
except ImportError:  # pragma: no cover - validated at runtime.
    dashscope = None
    AudioFormat = None
    MultiModality = None
    OmniRealtimeConversation = None

try:
    import pyaudio
except ImportError:  # pragma: no cover - depends on local audio setup.
    pyaudio = None

try:
    from .basic_vad_video_file import (
        DEFAULT_ASR_MODEL,
        DEFAULT_MODEL,
        DEFAULT_REALTIME_URL,
        BasicRealtimeCallback,
        HttpJpegFrameSource,
        PCMOutputPlayer,
        RealtimeHistoryLogger,
        VideoFrameSource,
        VideoRunRecorder,
        append_audio_and_maybe_video,
        default_instructions,
        load_env_files,
        require_runtime,
        update_session,
    )
    from .context_builder import (
        VLM_ALIASES,
        as_bool,
        as_int,
        first_value,
        latest_vlm_event,
    )
except ImportError:  # pragma: no cover - direct script execution.
    from basic_vad_video_file import (
        DEFAULT_ASR_MODEL,
        DEFAULT_MODEL,
        DEFAULT_REALTIME_URL,
        BasicRealtimeCallback,
        HttpJpegFrameSource,
        PCMOutputPlayer,
        RealtimeHistoryLogger,
        VideoFrameSource,
        VideoRunRecorder,
        append_audio_and_maybe_video,
        default_instructions,
        load_env_files,
        require_runtime,
        update_session,
    )
    from context_builder import (
        VLM_ALIASES,
        as_bool,
        as_int,
        first_value,
        latest_vlm_event,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_DIR = (
    Path(__file__).resolve().parent
    / "turnstep_prompt"
    / "trunprompt_allsteps"
)
DEFAULT_VIDEO = PROJECT_ROOT / "legoagentbackend/testvideo/step8test.mp4"


BUILTIN_INPUT_MATCHERS = (
    ("macbook", "microphone"),
    ("built-in", "microphone"),
    ("built in", "microphone"),
    ("internal", "microphone"),
)
BUILTIN_OUTPUT_MATCHERS = (
    ("macbook", "speaker"),
    ("built-in", "output"),
    ("built in", "output"),
    ("internal", "speaker"),
    ("internal", "output"),
)


def matches_tokens(name: str, matchers: tuple[tuple[str, ...], ...]) -> bool:
    normalized = name.casefold()
    return any(all(token in normalized for token in matcher) for matcher in matchers)


def builtin_device_index(audio: Any, *, kind: str) -> int | None:
    matchers = BUILTIN_INPUT_MATCHERS if kind == "input" else BUILTIN_OUTPUT_MATCHERS
    channel_key = "maxInputChannels" if kind == "input" else "maxOutputChannels"
    for index in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(index)
        if int(info.get(channel_key, 0)) <= 0:
            continue
        if matches_tokens(str(info.get("name", "")), matchers):
            return index
    try:
        default_info = (
            audio.get_default_input_device_info()
            if kind == "input"
            else audio.get_default_output_device_info()
        )
        return int(default_info.get("index"))
    except Exception:
        return None


def describe_device(audio: Any, index: int | None) -> str:
    if index is None:
        return "default"
    try:
        info = audio.get_device_info_by_index(index)
        return f"{index}: {info.get('name')}"
    except Exception:
        return str(index)


def open_builtin_microphone(audio: Any, *, device_index: int | None, chunk_ms: int) -> Any:
    chunk_frames = max(160, int(16000 * chunk_ms / 1000))
    return audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=chunk_frames,
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_realtime_metrics_summary(run_dir: Path) -> None:
    try:
        try:
            from .summarize_realtime_history import FIELDNAMES, summarize_run
        except ImportError:  # pragma: no cover - direct script execution.
            from summarize_realtime_history import FIELDNAMES, summarize_run

        summary = summarize_run(
            run_dir,
            wrong_steps=set(),
            step_total_override=None,
        )
        csv_path = run_dir / "realtime_metrics_summary.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerow(summary)
        (run_dir / "realtime_metrics_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[history] metrics summary written {csv_path}", flush=True)
    except Exception as exc:
        print(f"[history] metrics summary skipped: {exc}", flush=True)


def infer_next_step(vlm_event: dict[str, Any]) -> tuple[bool, int | None]:
    change_page = as_bool(first_value(vlm_event, VLM_ALIASES["change_page"], False))
    explicit_next = as_int(first_value(vlm_event, VLM_ALIASES["next_step"]))
    now_step = as_int(first_value(vlm_event, VLM_ALIASES["now_step"]))
    if explicit_next is not None:
        return change_page, explicit_next
    if change_page and now_step is not None:
        return True, now_step + 1
    return change_page, now_step


class TurnPromptMonitor:
    def __init__(
        self,
        *,
        vlm_output_path: Path,
        prompt_dir: Path,
        poll_seconds: float,
        stop_event: threading.Event,
        conversation: Any,
        history_logger: RealtimeHistoryLogger | None = None,
    ) -> None:
        self.vlm_output_path = vlm_output_path
        self.prompt_dir = prompt_dir
        self.poll_seconds = poll_seconds
        self.stop_event = stop_event
        self.conversation = conversation
        self.history_logger = history_logger
        self.last_signature: tuple[Any, ...] | None = None
        self.sent_steps: set[int] = set()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="turn-prompt-monitor")
        self.thread.start()

    def join(self, timeout: float = 2.0) -> None:
        if self.thread is not None:
            self.thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                print(f"\n[turn-prompt] skipped: {exc}", flush=True)
            self.stop_event.wait(self.poll_seconds)

    def _poll_once(self) -> None:
        if not self.vlm_output_path.exists():
            return
        event = latest_vlm_event(load_json(self.vlm_output_path))
        change_page, next_step = infer_next_step(event)
        signature = (
            event.get("time"),
            event.get("frame_index"),
            event.get("now_step"),
            event.get("changepage"),
            event.get("change_page"),
            next_step,
        )
        if signature == self.last_signature:
            return
        self.last_signature = signature
        if not change_page or next_step is None:
            return
        if next_step in self.sent_steps:
            return

        prompt_path = self.prompt_dir / f"step{next_step}.md"
        if not prompt_path.exists():
            print(f"\n[turn-prompt] missing {prompt_path}", flush=True)
            return

        prompt = prompt_path.read_text(encoding="utf-8").strip()
        if not prompt:
            return
        if self.history_logger is not None:
            self.history_logger.enqueue_step_trigger(
                step=next_step,
                prompt=prompt,
                vlm_event=event,
            )
        self.conversation.create_response(
            instructions=prompt,
            output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
        )
        self.sent_steps.add(next_step)
        print(f"\n[turn-prompt] sent step{next_step}.md", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Qwen Realtime VAD with builtin Mac audio and turn prompts."
    )
    parser.add_argument("--vlm-output", type=Path, help="VLM output JSON to poll.")
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPT_DIR)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--stream-url", help="HTTP MJPEG glasses stream URL.")
    parser.add_argument("--video-fps", type=float, default=1.0)
    parser.add_argument("--video-max-width", type=int, default=640)
    parser.add_argument("--no-video", action="store_true")
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
    parser.add_argument("--duration", type=float)
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
    parser.add_argument("--instructions", default=default_instructions())
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    require_runtime()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Set DASHSCOPE_API_KEY in the shell or project .env file.")
    dashscope.api_key = api_key

    audio = pyaudio.PyAudio()
    input_index = (
        args.input_device_index
        if args.input_device_index is not None
        else builtin_device_index(audio, kind="input")
    )
    output_index = (
        args.output_device_index
        if args.output_device_index is not None
        else builtin_device_index(audio, kind="output")
    )

    video_source = None
    if not args.no_video:
        if args.stream_url:
            video_source = HttpJpegFrameSource(
                args.stream_url,
                fps=args.video_fps,
                max_width=args.video_max_width,
            )
            print(f"[video] using stream_url={args.stream_url}", flush=True)
        elif args.video and args.video.exists():
            video_source = VideoFrameSource(
                args.video,
                fps=args.video_fps,
                max_width=args.video_max_width,
                loop=True,
            )

    mic_stream = None
    conversation = None
    monitor = None
    video_recorder = None
    history_logger = RealtimeHistoryLogger.create(
        model=args.model,
        history_dir=args.history_dir,
        metadata={
            "entrypoint": Path(__file__).name,
            "vlm_output": str(args.vlm_output) if args.vlm_output else None,
            "prompt_dir": str(args.prompt_dir),
            "video": str(args.video) if args.video else None,
            "stream_url": args.stream_url,
            "video_fps": args.video_fps,
            "asr_model": args.asr_model,
            "voice": args.voice,
        },
    )
    print(f"[history] writing {history_logger.run_dir}", flush=True)
    if not args.no_video:
        if args.stream_url:
            video_recorder = VideoRunRecorder(args.stream_url, history_logger.video_path)
            if video_recorder.start():
                history_logger.record_event(
                    "video_recording_started",
                    {
                        "source_url": args.stream_url,
                        "video_path": str(history_logger.video_path),
                    },
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
        output_device_index=output_index,
    )

    try:
        print(f"[audio] input={describe_device(audio, input_index)}", flush=True)
        print(f"[audio] output={describe_device(audio, output_index)}", flush=True)
        mic_stream = open_builtin_microphone(
            audio,
            device_index=input_index,
            chunk_ms=args.chunk_ms,
        )
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

        if args.vlm_output:
            monitor = TurnPromptMonitor(
                vlm_output_path=args.vlm_output,
                prompt_dir=args.prompt_dir,
                poll_seconds=args.poll_seconds,
                stop_event=stop_event,
                conversation=conversation,
                history_logger=history_logger,
            )
            monitor.start()
            print(f"[turn-prompt] watching {args.vlm_output}", flush=True)

        chunk_frames = max(160, int(16000 * args.chunk_ms / 1000))
        started_at = time.monotonic()
        audio_chunks = 0
        suppressed_audio_chunks = 0
        video_frames = 0
        print("[ready] realtime VAD is running. Press Ctrl+C to stop.", flush=True)

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
        if monitor is not None:
            monitor.join()
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
        history_logger.close()
        write_realtime_metrics_summary(history_logger.run_dir)
        audio.terminate()


def main() -> int:
    load_env_files()
    args = parse_args()
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
