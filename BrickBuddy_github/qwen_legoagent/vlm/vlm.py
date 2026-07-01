#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import re
import shutil
import subprocess
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QWEN_AGENT_DIR = PROJECT_ROOT / "qwen_legoagent"
LEGO_AGENT_BACKEND_DIR = PROJECT_ROOT / "legoagentbackend"
RAW_DATA_DIR = LEGO_AGENT_BACKEND_DIR / "rawdata"
VLM_DIR = QWEN_AGENT_DIR / "vlm"
VLM_HISTORY_DIR = VLM_DIR / "vlmhistory"

DEFAULT_VIDEO = LEGO_AGENT_BACKEND_DIR / "testvideo" / "step8test.mp4"
DEFAULT_STEP_INFO = RAW_DATA_DIR / "stepinfor_forvlm.json"
DEFAULT_MODEL_CONFIG = VLM_DIR / "testmodel_list.json"
DEFAULT_PROMPT_FILE = VLM_DIR / "vlm_prompt.md"
DEFAULT_EVENT_URL = "http://127.0.0.1:8765/events"

DEFAULT_OUTPUT_JSON = VLM_DIR / "example_vlmoutput.json"
DEFAULT_STEP_MEMORY_JSON = VLM_DIR / "example_vlmstepmemory.json"
DEFAULT_CSV_LOG = VLM_DIR / "example_vlmlog.csv"
DEFAULT_FRAME_DIR = VLM_DIR / "captured_frames"
DEFAULT_RAW_OUTPUT_DIRNAME = "rawoutput"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0

MODEL_KEY = "gpt-5.5"
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash-nothinking"
GPT_FALLBACK_MODEL = "gpt-5.4"
QWEN_FALLBACK_MODEL = "qwen3-omni-flash"
MODEL_KEY_ALIASES = {
    "gpt": "gpt-5.5",
    "gemini": "gemini-3.5-flash",
    "qwen": "qwen3.5-omni-flash",
    "qwen3": "qwen3-omni-flash",
}
STATE_VALUES = (0, 0.2, 0.4, 0.6, 0.8, 1)
CSV_FIELDS = (
    "frame_index",
    "time",
    "sample_time",
    "model",
    "variant",
    "ref_step",
    "ifjudge",
    "now_step",
    "state",
    "changepage",
    "current_frame",
    "vlm_time",
)
REQUEST_VARIANT_FULL_REF = "full_ref"
REQUEST_VARIANT_FULL_NO_REF = "full_no_ref"
REQUEST_VARIANT_FULL_NO_REF_NO_COMPONENTS = "full_no_ref_no_components"
REQUEST_VARIANT_MINIMAL_NO_REF = "minimal_no_ref"
SENSITIVE_B64_TERMS = (
    "adult",
    "blood",
    "bomb",
    "drug",
    "gun",
    "kill",
    "naked",
    "nude",
    "porn",
    "rape",
    "sex",
    "suicide",
    "terror",
    "violence",
    "xxx",
)
IMAGE_ENCODING_CANDIDATES = (
    ("JPEG", "image/jpeg", 640, 82),
)
PROMPT_SAFE_REPLACEMENTS = {
    "蓝黄相间": "blue-yellow alternating",
    "蓝黄装饰": "blue-yellow trim",
    "红蓝": "red-blue",
    "红砖": "red bricks",
    "红色外檐": "red outer eave",
    "中空": "hollow",
    "中心小孔": "center opening",
    "小孔": "opening",
    "窄窗": "narrow window",
    "带窗": "with-window",
    "窗": "window",
    "彩色矮塔": "multi-color low stack",
    "彩色小塔": "multi-color small stack",
    "彩色塔块": "multi-color stack module",
    "彩色塔": "multi-color stack",
    "彩色": "multi-color",
    "矮塔": "low stack",
    "小塔": "small stack",
    "塔块": "stack module",
    "塔身": "stack body",
    "塔": "stack",
    "压稳": "place firmly",
    "对称": "symmetric",
    "颜色": "colors",
    "浅蓝色": "light blue",
    "深绿色": "dark green",
    "亮绿色": "bright green",
    "黄顶": "yellow-top",
    "白色": "white",
    "红色": "red",
    "蓝色": "blue",
    "黄色": "yellow",
    "橙色": "orange",
    "绿色": "green",
    "红": "red",
    "蓝": "blue",
    "绿": "green",
    "橙": "orange",
    "白": "white",
    "黄": "yellow",
    "色": "color",
}


class VLMJsonParseError(ValueError):
    def __init__(
        self,
        raw_text: str,
        *,
        vlm_debug: dict[str, Any] | None = None,
    ) -> None:
        self.raw_text = raw_text
        self.vlm_debug = vlm_debug or {}
        super().__init__(f"VLM did not return a JSON object: {raw_text[:500]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the simplified Lego VLM page-turn test. The model returns one "
            "output JSON per frame; Python derives the CSV log and step memory."
        )
    )
    parser.add_argument(
        "--video",
        type=Path,
        default=DEFAULT_VIDEO,
        help="Input video. Defaults to legoagentbackend/testvideo/step8test.mp4.",
    )
    parser.add_argument(
        "--step-info",
        type=Path,
        default=DEFAULT_STEP_INFO,
        help=(
            "Step info JSON. Defaults to "
            "legoagentbackend/rawdata/stepinfor_forvlm.json."
        ),
    )
    parser.add_argument("--model-config", type=Path, default=DEFAULT_MODEL_CONFIG)
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=DEFAULT_PROMPT_FILE,
        help="Editable VLM prompt policy file.",
    )
    parser.add_argument("--model-key", default=MODEL_KEY)
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--end-seconds", type=float, default=None)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument(
        "--sensitive-retry-count",
        type=int,
        default=3,
        help=(
            "When the model service returns sensitive_words_detected or an "
            "empty/non-JSON response, retry with the next second's frame up "
            "to this many times."
        ),
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help=(
            "Run output directory. Defaults to "
            "qwen_legoagent/vlm/vlmhistory/vlm-{model-series}-YYYYMMDD-HHMMSS."
        ),
    )
    parser.add_argument(
        "--frames-dir",
        type=Path,
        default=None,
        help="Frame output directory. Defaults to <run-dir>/frames.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="VLM output JSON path. Defaults to <run-dir>/vlmoutput.json.",
    )
    parser.add_argument(
        "--step-memory-json",
        type=Path,
        default=None,
        help="Step memory JSON path. Defaults to <run-dir>/vlmstepmemory.json.",
    )
    parser.add_argument(
        "--csv-log",
        type=Path,
        default=None,
        help="CSV log path. Defaults to <run-dir>/vlmlog.csv.",
    )
    parser.add_argument(
        "--raw-output-dir",
        type=Path,
        default=None,
        help="Raw model output directory. Defaults to <run-dir>/rawoutput.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Do not call the model; emit deterministic sample outputs for format tests.",
    )
    parser.add_argument(
        "--frame-path",
        type=Path,
        default=None,
        help="Pre-extracted frame image from backend realtime scheduler.",
    )
    parser.add_argument(
        "--frame-time-seconds",
        type=float,
        default=None,
        help="Video timestamp for --frame-path.",
    )
    parser.add_argument(
        "--frame-index",
        type=int,
        default=None,
        help=(
            "Frame index for --frame-path. Defaults to the next index in "
            "the run output."
        ),
    )
    parser.add_argument(
        "--realtime-stream",
        action="store_true",
        help=(
            "Compatibility flag for backend realtime mode. State scoring still "
            "follows the same strict 0/0.2/0.4/0.6/0.8/1 rubric."
        ),
    )
    parser.add_argument(
        "--event-url",
        default=os.getenv("VLM_EVENT_POST_URL", DEFAULT_EVENT_URL),
        help=(
            "Frontend bridge URL for posting each VLM output. "
            "Defaults to http://127.0.0.1:8765/events; failures are ignored."
        ),
    )
    return parser.parse_args()


def load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(QWEN_AGENT_DIR / ".env")
    load_dotenv(LEGO_AGENT_BACKEND_DIR / ".env")


def resolve_path(path: Path) -> Path:
    path = path.expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def resolve_model_key(model_key: str) -> str:
    normalized = model_key.strip().lower()
    return MODEL_KEY_ALIASES.get(normalized, model_key)


def model_series_for_run_dir(model_name: str) -> str:
    normalized = model_name.strip().lower()
    if normalized.startswith("gpt"):
        return "gpt"
    if normalized.startswith("gemini"):
        return "gemini"
    if normalized.startswith("qwen"):
        return "qwen"
    return slugify_filename_part(normalized)


def default_run_dir(model_name: str) -> Path:
    run_dir_prefix = f"vlm-{model_series_for_run_dir(model_name)}"
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    base = VLM_HISTORY_DIR / f"{run_dir_prefix}-{stamp}"
    if not base.exists():
        return base
    for index in range(2, 100):
        candidate = VLM_HISTORY_DIR / f"{run_dir_prefix}-{stamp}-{index:02d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate a unique run directory for {base}")


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Missing required tool: {name}")
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


def sample_times(
    *,
    duration: float,
    interval: float,
    start: float,
    end: float | None,
    max_frames: int | None,
) -> list[float]:
    if interval <= 0:
        raise ValueError("--interval-seconds must be > 0")
    safe_start = max(0.0, start)
    safe_end = min(duration, end if end is not None else duration)
    times: list[float] = []
    current = safe_start
    while current <= safe_end:
        times.append(round(current, 3))
        if max_frames is not None and len(times) >= max_frames:
            break
        current += interval
    return times or [safe_start]


def format_video_time(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


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


def image_data_url(path: Path) -> tuple[str, dict[str, Any]]:
    from PIL import Image

    for image_format, mime_type, max_side, quality in IMAGE_ENCODING_CANDIDATES:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            save_kwargs: dict[str, Any] = {"optimize": True}
            if quality is not None:
                save_kwargs["quality"] = quality
            image.save(buffer, format=image_format, **save_kwargs)

        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        lower_encoded = encoded.lower()
        hits = {
            term: lower_encoded.count(term)
            for term in SENSITIVE_B64_TERMS
            if term in lower_encoded
        }
        hit_count = sum(hits.values())
        meta = {
            "source_path": str(path),
            "format": image_format,
            "mime_type": mime_type,
            "max_side": max_side,
            "quality": quality,
            "byte_size": len(buffer.getvalue()),
            "base64_length": len(encoded),
            "sensitive_base64_hits": hits,
        }
        url = f"data:{mime_type};base64,{encoded}"
        return url, meta

    raise RuntimeError("No image encoding candidates configured.")


def load_step_info(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("stepinfor_forvlm.json must be a JSON array")
    steps: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        stepid = int(item["stepid"])
        stepimage = str(item["stepimage"])
        stepdescription = str(item["stepdescription"])
        raw_components = item.get("stepcomponents") or []
        if not isinstance(raw_components, list):
            raise ValueError(f"stepcomponents for step {stepid} must be a JSON array")
        stepcomponents: list[dict[str, Any]] = []
        for component in raw_components:
            if not isinstance(component, dict):
                continue
            description = str(component.get("description", ""))
            component_path = str(component.get("path", ""))
            if not component_path:
                continue
            resolved_component_path = (path.parent / component_path).resolve()
            if not resolved_component_path.exists():
                raise FileNotFoundError(resolved_component_path)
            stepcomponents.append(
                {
                    "description": description,
                    "path": component_path,
                    "image_path": resolved_component_path,
                }
            )
        image_path = (path.parent / stepimage).resolve()
        if not image_path.exists():
            raise FileNotFoundError(image_path)
        steps.append(
            {
                "stepid": stepid,
                "stepimage": stepimage,
                "stepcomponents": stepcomponents,
                "stepdescription": stepdescription,
                "image_path": image_path,
            }
        )
    if not steps:
        raise ValueError("No valid steps found in stepinfor_forvlm.json")
    return sorted(steps, key=lambda step: int(step["stepid"]))


def load_prompt_template(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def sanitize_prompt_for_gateway(prompt: str) -> str:
    sanitized = prompt
    for source, replacement in PROMPT_SAFE_REPLACEMENTS.items():
        sanitized = sanitized.replace(source, replacement)
    return sanitized


def build_minimal_prompt(
    *,
    time_text: str,
    frame_index: int,
    ref_step: int,
    total_steps: int,
    realtime_stream: bool = False,
) -> str:
    return f"""
You are a visual progress checker for a Lego build.

Highest-priority rule: completely ignore anything shown inside a computer,
phone, tablet, monitor, projector, or other digital screen. Screen content is
never the student's physical Lego build, even if it shows Lego instructions,
a camera preview, a replay, or a reference image. Judge only real Lego bricks
in the physical world: on the table, on the baseplate, or in the operator's
hands.

Inputs:
- 'time': '{time_text}'
- 'frame_index': {frame_index}
- 'ref_step': {ref_step}
- 'total_steps': {total_steps}
- 'CURRENT_FRAME': the current video frame.
- 'REF_IMAGE': omitted in this retry.

Rules:
1. First mentally mask out all computer/phone/tablet/monitor/projector screen
   regions. Treat those screen regions as blank and irrelevant.
2. After masking screens, decide 'ifjudge'. Use 0 if the physical build area
   is not visible enough, the hand is only searching for parts, the frame is
   unrelated, blurry, blocked, or the only Lego-like content appears on a
   digital screen.
3. If 'ifjudge' is 0, set 'now_step' to 'ref_step', 'state' to 0,
   'changepage' to 0, and explain briefly.
4. If 'ifjudge' is 1, estimate completion for 'ref_step' from the physical-world
   part of 'CURRENT_FRAME' only.
5. 'now_step' must equal 'ref_step'. Do not predict a future step.
6. 'state' must be exactly one of: 0, 0.2, 0.4, 0.6, 0.8, 1.
7. Use 'state' 1 only when the target page is directly visible, physically
   installed on the real physical model, and functionally complete.
8. A component held in the hand, hovering above the model, being aligned, or
   not fully pressed down is not complete. Use 0.8 or lower.
9. Never increase 'state' because of Lego shapes shown on a computer, phone,
   tablet, monitor, projected screen, instruction page, replay, or camera preview.
10. If both a screen and the real Lego build are visible, judge only the
   physical build on the table/in the operator's hands.
11. Describe only what is actually visible in the physical-world part of
   'CURRENT_FRAME'. Do not invent
   a roof/eave/module just because the current reference step expects one.
12. If the real build still shows an earlier wall/baseplate stage and the
   target module for 'ref_step' is absent, give a low 'state' and explain
   the mismatch in 'reason'.
13. For 'ref_step' 1, a visible large white square baseplate on the physical
   table means complete.
14. For 'ref_step' 2, use 1 only when the full wall ring is visible, closed,
   all segments are attached, and the blue-yellow top trim is continuous.
   Partial/cropped/held/moving views are <= 0.8.
15. Set 'changepage' to 0. Python will recalculate page turning.

Return only this JSON object:
{{
  "time": "{time_text}",
  "frame_index": {frame_index},
  "ifjudge": 1,
  "now_step": {ref_step},
  "state": 0,
  "reason": "short visual reason",
  "changepage": 0
}}
""".strip()


def remove_ref_components_from_prompt(prompt: str) -> str:
    return re.sub(
        r"^- `'ref_components'`: .*?$",
        "- `'ref_components'`: omitted for this retry.",
        prompt,
        flags=re.MULTILINE,
    )


def add_realtime_stream_prompt_note(prompt: str) -> str:
    return prompt


def prompt_for_request_variant(
    *,
    base_prompt: str,
    request_variant: str,
    time_text: str,
    frame_index: int,
    ref_step: int,
    total_steps: int,
    realtime_stream: bool = False,
) -> str:
    if request_variant == REQUEST_VARIANT_MINIMAL_NO_REF:
        return build_minimal_prompt(
            time_text=time_text,
            frame_index=frame_index,
            ref_step=ref_step,
            total_steps=total_steps,
            realtime_stream=realtime_stream,
        )
    if request_variant == REQUEST_VARIANT_FULL_NO_REF:
        variant_prompt = (
            base_prompt
            + "\n\nRetry note: 'REF_IMAGE' is omitted in this request. "
            + "Use 'ref_stepdescription', 'ref_components', and 'CURRENT_FRAME' "
            + "to judge the current page."
        )
        return (
            add_realtime_stream_prompt_note(variant_prompt)
            if realtime_stream
            else variant_prompt
        )
    if request_variant == REQUEST_VARIANT_FULL_NO_REF_NO_COMPONENTS:
        variant_prompt = (
            remove_ref_components_from_prompt(base_prompt)
            + "\n\nRetry note: 'REF_IMAGE' and 'ref_components' are omitted in this request. "
            + "Use 'ref_stepdescription' and 'CURRENT_FRAME' to judge the current page."
        )
        return (
            add_realtime_stream_prompt_note(variant_prompt)
            if realtime_stream
            else variant_prompt
        )
    return (
        add_realtime_stream_prompt_note(base_prompt)
        if realtime_stream
        else base_prompt
    )


def include_ref_image_for_variant(request_variant: str) -> bool:
    return request_variant == REQUEST_VARIANT_FULL_REF


def request_attempt_sequence(retry_count: int) -> list[dict[str, Any]]:
    attempts = [
        {"retry_offset_seconds": 0, "request_variant": REQUEST_VARIANT_FULL_REF},
        {"retry_offset_seconds": 0, "request_variant": REQUEST_VARIANT_FULL_NO_REF},
        {
            "retry_offset_seconds": 0,
            "request_variant": REQUEST_VARIANT_FULL_NO_REF_NO_COMPONENTS,
        },
        {
            "retry_offset_seconds": 0,
            "request_variant": REQUEST_VARIANT_MINIMAL_NO_REF,
        },
    ]
    for retry_offset_seconds in range(1, retry_count + 1):
        attempts.append(
            {
                "retry_offset_seconds": retry_offset_seconds,
                "request_variant": REQUEST_VARIANT_MINIMAL_NO_REF,
            }
        )
    return attempts


def find_step(steps: list[dict[str, Any]], stepid: int) -> dict[str, Any]:
    for step in steps:
        if int(step["stepid"]) == stepid:
            return step
    raise KeyError(f"Unknown step {stepid}")


def load_model_config(config_path: Path, model_key: str) -> dict[str, Any]:
    model_key = resolve_model_key(model_key)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    for group in ("omni_model", "vlm_model", "VLM_model", "tts_model"):
        models = data.get(group) or {}
        if model_key in models:
            config = dict(models[model_key])
            config["model_key"] = model_key
            config["model_group"] = group
            if model_key == "gpt-5.5":
                config["model_name"] = (
                    os.getenv("LEGOGLASS_PROGRESS_MODEL")
                    or os.getenv("VLM_GPT5.5_MODEL")
                    or config.get("model_name")
                    or model_key
                )
                config["base_url"] = (
                    os.getenv("VLM_BASE_URL")
                    or os.getenv("LEGOGLASS_PROGRESS_BASE_URL")
                    or os.getenv("LEGOGLASS_BASE_URL")
                    or config.get("base_url")
                )
            elif model_key.startswith("gemini"):
                config["model_name"] = (
                    os.getenv("VLM_GEMINI_MODEL")
                    or config.get("model_name")
                    or model_key
                )
                config["base_url"] = (
                    os.getenv("VLM_BASE_URL")
                    or os.getenv("LEGOGLASS_PROGRESS_BASE_URL")
                    or os.getenv("LEGOGLASS_BASE_URL")
                    or config.get("base_url")
                )
            elif model_key.startswith("qwen"):
                if model_key == QWEN_FALLBACK_MODEL:
                    config["model_name"] = (
                        os.getenv("VLM_QWEN_FALLBACK_MODEL")
                        or config.get("model_name")
                        or model_key
                    )
                else:
                    config["model_name"] = (
                        os.getenv("VLM_QWEN_MODEL")
                        or os.getenv("LEGOGLASS_OMNI_MODEL")
                        or config.get("model_name")
                        or model_key
                    )
                config["base_url"] = (
                    os.getenv("VLM_QWEN_BASE_URL")
                    or os.getenv("LEGOGLASS_OMNI_BASE_URL")
                    or os.getenv("DASHSCOPE_BASE_URL")
                    or config.get("base_url")
                )
            return config
    raise KeyError(f"Unknown model key: {model_key}")


def model_attempt_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    primary = dict(config)
    primary["model_role"] = "primary"
    attempts = [primary]

    model_key = str(config.get("model_key") or "")
    fallback_model = ""
    if model_key.startswith("gemini"):
        fallback_model = os.getenv("VLM_GEMINI_FALLBACK_MODEL") or GEMINI_FALLBACK_MODEL
    elif model_key.startswith("gpt"):
        fallback_model = os.getenv("VLM_GPT_FALLBACK_MODEL") or GPT_FALLBACK_MODEL
    elif model_key.startswith("qwen"):
        fallback_model = os.getenv("VLM_QWEN_FALLBACK_MODEL") or QWEN_FALLBACK_MODEL

    primary_model = str(primary.get("model_name") or "")
    if fallback_model and fallback_model != primary_model:
        fallback = dict(config)
        fallback["model_name"] = fallback_model
        fallback["model_role"] = "fallback"
        attempts.append(fallback)
    return attempts


def build_client(config: dict[str, Any]) -> Any:
    from openai import OpenAI

    env_names = [str(config.get("api_key_env") or "VLM_API_KEY")]
    env_names.extend(str(name) for name in config.get("api_key_fallback_envs", []))
    api_key = ""
    for env_name in env_names:
        api_key = os.getenv(env_name) or ""
        if api_key:
            break
    api_key = api_key or str(config.get("api_key") or "")
    if not api_key:
        raise RuntimeError(f"Set {' or '.join(env_names)} before running VLM.")
    base_url = str(config.get("base_url") or "")
    if not base_url:
        raise RuntimeError("Model config must include base_url.")
    timeout_value = (
        os.getenv("VLM_REQUEST_TIMEOUT_SECONDS")
        or config.get("request_timeout_seconds")
        or config.get("timeout_seconds")
        or DEFAULT_REQUEST_TIMEOUT_SECONDS
    )
    try:
        timeout_seconds = float(timeout_value)
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_REQUEST_TIMEOUT_SECONDS
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "base_url": base_url,
        "max_retries": 0,
    }
    if timeout_seconds > 0:
        client_kwargs["timeout"] = timeout_seconds
    return OpenAI(**client_kwargs)


def build_prompt(
    *,
    step_info: list[dict[str, Any]],
    ref_step: dict[str, Any],
    time_text: str,
    frame_index: int,
    prompt_template: str,
) -> str:
    step_sequence = [
        {
            "stepid": step["stepid"],
            "stepimage": step["stepimage"],
        }
        for step in step_info
    ]
    ref_components = [
        component["description"] for component in ref_step.get("stepcomponents", [])
    ]
    prompt = prompt_template.strip()
    if not prompt:
        raise ValueError("VLM prompt template is empty.")

    replacements = {
        "{{TIME}}": time_text,
        "{{FRAME_INDEX}}": str(frame_index),
        "{{REF_STEP}}": str(ref_step["stepid"]),
        "{{REF_IMAGE}}": str(ref_step["stepimage"]),
        "{{REF_STEP_DESCRIPTION}}": str(ref_step["stepdescription"]),
        "{{REF_COMPONENTS}}": json.dumps(ref_components, ensure_ascii=False),
        "{{STEP_SEQUENCE}}": json.dumps(step_sequence, ensure_ascii=False),
    }
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


def summarize_stream_chunk(chunk: Any) -> dict[str, Any]:
    if hasattr(chunk, "model_dump"):
        data = chunk.model_dump()
    elif isinstance(chunk, dict):
        data = chunk
    else:
        data = {}

    summary: dict[str, Any] = {
        "type": chunk.__class__.__name__,
        "choices_count": len(data.get("choices") or []),
        "has_usage": data.get("usage") is not None,
    }
    if data.get("id"):
        summary["id"] = data["id"]
    if data.get("model"):
        summary["model"] = data["model"]

    choice_summaries = []
    for choice in data.get("choices") or []:
        delta = choice.get("delta") or {}
        content = delta.get("content") or ""
        reasoning_content = delta.get("reasoning_content") or ""
        choice_summaries.append(
            {
                "index": choice.get("index"),
                "finish_reason": choice.get("finish_reason"),
                "delta_keys": sorted(delta.keys()),
                "content_length": len(content),
                "content_preview": content[:160],
                "reasoning_content_length": len(reasoning_content),
            }
        )
    if choice_summaries:
        summary["choices"] = choice_summaries
    return summary


def call_vlm(
    *,
    client: Any,
    config: dict[str, Any],
    current_frame_url: str,
    ref_image_url: str | None,
    prompt: str,
) -> tuple[dict[str, Any], float, str, dict[str, Any]]:
    started_at = time.perf_counter()
    raw_text = ""
    stream_chunks: list[dict[str, Any]] = []
    chunk_count = 0
    model_name = str(config["model_name"])
    content: list[dict[str, Any]] = [
        {"type": "text", "text": prompt},
        {"type": "text", "text": "[CURRENT_FRAME] 当前视频帧里的搭建画面。"},
        {"type": "image_url", "image_url": {"url": current_frame_url}},
    ]
    if ref_image_url:
        content.extend(
            [
                {"type": "text", "text": "[REF_IMAGE] 当前参考步骤图片。"},
                {"type": "image_url", "image_url": {"url": ref_image_url}},
            ]
        )
    else:
        content.append(
            {
                "type": "text",
                "text": "[REF_IMAGE] omitted for this retry request.",
            }
        )
    stream = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "user",
                "content": content,
            }
        ],
        modalities=config.get("text_only_modalities", ["text"]),
        stream=bool(config.get("stream", True)),
        stream_options=config.get("stream_options", {"include_usage": True}),
    )
    for chunk in stream:
        chunk_count += 1
        if len(stream_chunks) < 80:
            stream_chunks.append(summarize_stream_chunk(chunk))
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        raw_text += getattr(choices[0].delta, "content", None) or ""
    elapsed = round(time.perf_counter() - started_at, 3)
    vlm_debug = {
        "model_name": model_name,
        "model_role": config.get("model_role"),
        "chunk_count": chunk_count,
        "raw_text_length": len(raw_text),
        "stream_chunks": stream_chunks,
    }
    try:
        parsed = parse_json_object(raw_text)
    except VLMJsonParseError as exc:
        exc.vlm_debug = vlm_debug
        raise
    return parsed, elapsed, raw_text, vlm_debug


def is_sensitive_words_error(exc: Exception) -> bool:
    return "sensitive_words_detected" in str(exc).lower()


def is_retryable_vlm_error(exc: Exception) -> bool:
    return is_sensitive_words_error(exc) or isinstance(exc, VLMJsonParseError)


def vlm_error_label(exc: Exception) -> str:
    if is_sensitive_words_error(exc):
        return "sensitive_words_detected"
    if isinstance(exc, VLMJsonParseError):
        return "empty_or_non_json_response"
    return exc.__class__.__name__


def parse_json_object(raw_text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise VLMJsonParseError(raw_text)


def slugify_filename_part(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return text[:80] or "unknown"


def normalize_state(value: Any) -> float | int:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            value = float(text[:-1]) / 100
        else:
            value = float(text)
    number = float(value)
    if number > 1:
        number = number / 100
    nearest = min(STATE_VALUES, key=lambda allowed: abs(float(allowed) - number))
    return int(nearest) if nearest in {0, 1} else nearest


def normalize_ifjudge(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and int(value) in {0, 1}:
        return int(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"0", "false", "no"}:
            return 0
        if text in {"1", "true", "yes"}:
            return 1
    return 1


def normalize_output(
    parsed: dict[str, Any],
    *,
    time_text: str,
    frame_index: int,
    ref_step: int,
    total_steps: int,
    completed_steps: set[int],
) -> dict[str, Any]:
    try:
        raw_now_step = int(parsed.get("now_step", ref_step))
    except (TypeError, ValueError):
        raw_now_step = ref_step
    now_step = ref_step
    ifjudge = normalize_ifjudge(parsed.get("ifjudge"))
    reason = str(parsed.get("reason") or "").strip()
    if not reason:
        reason = "模型没有给出理由。"

    if ifjudge == 0:
        return {
            "time": time_text,
            "frame_index": frame_index,
            "ifjudge": 0,
            "now_step": ref_step,
            "state": 0,
            "reason": reason,
            "changepage": 0,
        }

    state = normalize_state(parsed.get("state", 0))
    if raw_now_step != ref_step:
        reason = (
            f"{reason}（模型原始 now_step={raw_now_step}，"
            f"已按当前 ref_step={ref_step} 校正；VLM 只判断当前参考页，不预测未来页。）"
        )

    changepage = 0
    if state == 1 and ref_step < total_steps and ref_step not in completed_steps:
        changepage = 1

    return {
        "time": time_text,
        "frame_index": frame_index,
        "ifjudge": ifjudge,
        "now_step": now_step,
        "state": state,
        "reason": reason,
        "changepage": changepage,
    }


def attach_frame_metadata(
    output: dict[str, Any],
    *,
    sample_time_text: str,
    sample_time_seconds: float,
    retry_offset_seconds: int,
    current_frame: Path,
    ref_step: int,
) -> dict[str, Any]:
    enriched = dict(output)
    enriched["sample_time"] = sample_time_text
    enriched["sample_time_seconds"] = round(sample_time_seconds, 3)
    enriched["retry_offset_seconds"] = retry_offset_seconds
    enriched["current_frame"] = str(current_frame)
    enriched["ref_step"] = ref_step
    return enriched


def mock_output(
    *,
    time_text: str,
    frame_index: int,
    ref_step: int,
    total_steps: int,
    completed_steps: set[int],
) -> tuple[dict[str, Any], float, str]:
    if ref_step == 1 and ref_step not in completed_steps:
        state = 1
        reason = "mock：步骤1只要看到白色底板就算完成，直接切换下一页。"
    else:
        cycle = (0.2, 0.6, 1)
        state = cycle[frame_index % len(cycle)]
        reason = f"mock：按测试节奏判断步骤{ref_step}完成度为{state}。"
    parsed = {
        "time": time_text,
        "frame_index": frame_index,
        "ifjudge": 1,
        "now_step": ref_step,
        "state": state,
        "reason": reason,
        "changepage": 0,
    }
    normalized = normalize_output(
        parsed,
        time_text=time_text,
        frame_index=frame_index,
        ref_step=ref_step,
        total_steps=total_steps,
        completed_steps=completed_steps,
    )
    return normalized, 0.0, json.dumps(normalized, ensure_ascii=False)


def initial_step_memory(total_steps: int) -> dict[str, Any]:
    return {
        "steps": [
            {
                "step": step,
                "startframe": None,
                "endframe": None,
                "nextpagetime": None,
            }
            for step in range(1, total_steps + 1)
        ]
    }


def ensure_step_started(step_memory: dict[str, Any], step: int, frame_index: int) -> None:
    item = step_memory["steps"][step - 1]
    if item["startframe"] is None:
        item["startframe"] = frame_index


def mark_step_completed(
    step_memory: dict[str, Any],
    *,
    step: int,
    frame_index: int,
    next_page_time: str | None,
) -> None:
    item = step_memory["steps"][step - 1]
    item["endframe"] = frame_index
    item["nextpagetime"] = next_page_time


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def current_step_from_outputs(outputs: list[dict[str, Any]], total_steps: int) -> int:
    completed_turns = sum(1 for output in outputs if int(output.get("changepage") or 0) == 1)
    return min(total_steps, completed_turns + 1)


def completed_steps_from_outputs(outputs: list[dict[str, Any]], total_steps: int) -> set[int]:
    completed_steps: set[int] = set()
    step = 1
    for output in outputs:
        if int(output.get("changepage") or 0) == 1:
            completed_steps.add(step)
            step = min(total_steps, step + 1)
    return completed_steps


def post_vlm_event(event_url: str, output: dict[str, Any]) -> None:
    if not event_url or event_url.lower() in {"0", "none", "off", "false"}:
        return
    payload = json.dumps(output, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        event_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=0.5).close()
    except (OSError, urllib.error.URLError, TimeoutError):
        return


def write_raw_output(
    raw_output_dir: Path,
    *,
    frame_index: int,
    time_text: str,
    sample_time_text: str | None = None,
    retry_offset_seconds: int = 0,
    ref_step: dict[str, Any],
    frame_path: Path,
    current_frame_payload: dict[str, Any] | None = None,
    ref_image_payload: dict[str, Any] | None = None,
    prompt: str,
    raw_text: str,
    parsed: dict[str, Any],
    output: dict[str, Any],
    vlm_time: float,
    mock: bool,
    model_name: str | None = None,
    model_role: str | None = None,
    request_variant: str | None = None,
    include_ref_image: bool | None = None,
    vlm_debug: dict[str, Any] | None = None,
) -> None:
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    retry_part = (
        f"_retry{retry_offset_seconds:02d}" if retry_offset_seconds else ""
    )
    model_part = ""
    if model_name:
        role_part = f"{slugify_filename_part(model_role)}_" if model_role else ""
        model_part = f"_{role_part}{slugify_filename_part(model_name)}"
    variant_part = ""
    if request_variant:
        variant_part = f"_{slugify_filename_part(request_variant)}"
    filename = (
        f"frame_{frame_index:04d}_{time_text.replace(':', '')}"
        f"{retry_part}{variant_part}{model_part}_rawoutput.json"
    )
    write_json(
        raw_output_dir / filename,
        {
            "time": time_text,
            "sample_time": sample_time_text or time_text,
            "retry_offset_seconds": retry_offset_seconds,
            "frame_index": frame_index,
            "mock": mock,
            "model_name": model_name,
            "model_role": model_role,
            "request_variant": request_variant,
            "include_ref_image": include_ref_image,
            "ref_step": ref_step["stepid"],
            "ref_image": ref_step["stepimage"],
            "current_frame": str(frame_path),
            "current_frame_payload": current_frame_payload,
            "ref_image_payload": ref_image_payload,
            "vlm_time": round(vlm_time, 3),
            "prompt": prompt,
            "raw_text": raw_text,
            "parsed_output": parsed,
            "normalized_output": output,
            "vlm_debug": vlm_debug,
        },
    )


def main() -> int:
    args = parse_args()
    load_env()

    video_path = resolve_path(args.video)
    step_info_path = resolve_path(args.step_info)
    model_config_path = resolve_path(args.model_config)
    config = load_model_config(model_config_path, args.model_key)
    prompt_file = resolve_path(args.prompt_file)
    primary_model_name = str(config.get("model_name") or args.model_key)
    run_dir = (
        resolve_path(args.run_dir)
        if args.run_dir
        else default_run_dir(primary_model_name)
    )
    frames_dir = resolve_path(args.frames_dir) if args.frames_dir else run_dir / "frames"
    raw_output_dir = (
        resolve_path(args.raw_output_dir)
        if args.raw_output_dir
        else run_dir / DEFAULT_RAW_OUTPUT_DIRNAME
    )
    output_json = (
        resolve_path(args.output_json) if args.output_json else run_dir / "vlmoutput.json"
    )
    step_memory_json = (
        resolve_path(args.step_memory_json)
        if args.step_memory_json
        else run_dir / "vlmstepmemory.json"
    )
    csv_log = resolve_path(args.csv_log) if args.csv_log else run_dir / "vlmlog.csv"
    run_dir.mkdir(parents=True, exist_ok=True)

    frame_mode = args.frame_path is not None
    frame_path_arg = resolve_path(args.frame_path) if args.frame_path else None
    if frame_mode and not frame_path_arg.exists():
        raise FileNotFoundError(frame_path_arg)
    if not frame_mode and not video_path.exists():
        raise FileNotFoundError(video_path)
    step_info = load_step_info(step_info_path)
    prompt_template = load_prompt_template(prompt_file)
    total_steps = len(step_info)
    model_attempts = model_attempt_configs(config)
    client = None if args.mock else build_client(config)
    if args.mock:
        print("[vlm] mock mode; model=mock", flush=True)
    else:
        model_labels = ", ".join(
            f"{attempt['model_role']}={attempt['model_name']}"
            for attempt in model_attempts
        )
        print(f"[vlm] model_attempts: {model_labels}", flush=True)

    if frame_mode:
        frame_time_seconds = (
            float(args.frame_time_seconds)
            if args.frame_time_seconds is not None
            else float(args.start_seconds)
        )
        existing_outputs = read_json(output_json, [])
        vlm_outputs = existing_outputs if isinstance(existing_outputs, list) else []
        frame_index_value = (
            int(args.frame_index)
            if args.frame_index is not None
            else len(vlm_outputs)
        )
        frame_items = [(frame_index_value, frame_time_seconds)]
        duration = frame_time_seconds
    else:
        duration = video_duration(video_path)
        times = sample_times(
            duration=duration,
            interval=args.interval_seconds,
            start=args.start_seconds,
            end=args.end_seconds,
            max_frames=args.max_frames,
        )
        frame_items = list(enumerate(times))
        vlm_outputs = []

    current_ref_step = current_step_from_outputs(vlm_outputs, total_steps)
    completed_steps = completed_steps_from_outputs(vlm_outputs, total_steps)
    loaded_step_memory = read_json(step_memory_json, None) if frame_mode else None
    step_memory = (
        loaded_step_memory
        if isinstance(loaded_step_memory, dict) and isinstance(loaded_step_memory.get("steps"), list)
        else initial_step_memory(total_steps)
    )
    csv_rows = read_csv(csv_log) if frame_mode else []
    sensitive_retry_count = max(0, int(args.sensitive_retry_count))
    if frame_mode:
        sensitive_retry_count = 0
    if not csv_log.exists():
        write_csv(csv_log, csv_rows)
    if not step_memory_json.exists():
        write_json(step_memory_json, step_memory)
    print(f"[vlm] run_dir={run_dir}", flush=True)
    print(f"[vlm] output_json={output_json}", flush=True)
    print(f"[vlm] raw_output={raw_output_dir}", flush=True)
    print(f"[vlm] csv_log={csv_log}", flush=True)
    print(f"[vlm] step_memory={step_memory_json}", flush=True)
    print(
        "[vlm]"
        f" frame_mode={'on' if frame_mode else 'off'}"
        f" interval_seconds={args.interval_seconds}",
        f" realtime_stream={'on' if args.realtime_stream else 'off'}",
        flush=True,
    )

    for frame_index, timestamp in frame_items:
        time_text = format_video_time(timestamp)
        if frame_mode:
            frame_path = frame_path_arg
        else:
            frame_path = frames_dir / f"frame_{frame_index:04d}_{time_text.replace(':', '')}.png"
            extract_frame(video_path, frame_path, timestamp)

        ref_step = find_step(step_info, current_ref_step)
        ensure_step_started(step_memory, current_ref_step, frame_index)
        prompt = build_prompt(
            step_info=step_info,
            ref_step=ref_step,
            time_text=time_text,
            frame_index=frame_index,
            prompt_template=prompt_template,
        )
        prompt = sanitize_prompt_for_gateway(prompt)
        final_prompt = prompt
        final_frame_path = frame_path
        final_sample_time_text = time_text
        final_sample_time_seconds = timestamp
        final_retry_offset_seconds = 0
        final_ref_image_payload = None
        used_model_name = "mock"
        used_model_role = "mock"
        used_request_variant = "mock"
        used_include_ref_image = False

        if args.mock:
            current_frame_payload = None
            ref_image_payload = None
            vlm_debug = None
            output, vlm_time, raw_text = mock_output(
                time_text=time_text,
                frame_index=frame_index,
                ref_step=current_ref_step,
                total_steps=total_steps,
                completed_steps=completed_steps,
            )
            parsed = dict(output)
        else:
            assert client is not None
            total_vlm_time = 0.0
            used_model_name = str(model_attempts[0]["model_name"])
            used_model_role = str(model_attempts[0].get("model_role") or "primary")
            ref_image_url, ref_image_payload = image_data_url(Path(ref_step["image_path"]))
            final_ref_image_payload = ref_image_payload

            frame_done = False
            frame_cache: dict[int, dict[str, Any]] = {}
            request_attempts = request_attempt_sequence(sensitive_retry_count)

            for request_index, request_attempt in enumerate(request_attempts):
                retry_offset_seconds = int(request_attempt["retry_offset_seconds"])
                request_variant = str(request_attempt["request_variant"])
                include_ref_image = include_ref_image_for_variant(request_variant)
                attempt_prompt = prompt_for_request_variant(
                    base_prompt=prompt,
                    request_variant=request_variant,
                    time_text=time_text,
                    frame_index=frame_index,
                    ref_step=current_ref_step,
                    total_steps=total_steps,
                    realtime_stream=args.realtime_stream,
                )

                if retry_offset_seconds not in frame_cache:
                    sample_timestamp = min(duration, timestamp + retry_offset_seconds)
                    sample_time_text = format_video_time(sample_timestamp)
                    if retry_offset_seconds == 0:
                        attempt_frame_path = frame_path
                    else:
                        attempt_frame_path = (
                            frames_dir
                            / (
                                f"frame_{frame_index:04d}_{time_text.replace(':', '')}"
                                f"_retry{retry_offset_seconds:02d}.png"
                            )
                        )
                        extract_frame(video_path, attempt_frame_path, sample_timestamp)
                    current_frame_url, current_frame_payload = image_data_url(
                        attempt_frame_path
                    )
                    frame_cache[retry_offset_seconds] = {
                        "sample_time_text": sample_time_text,
                        "sample_time_seconds": sample_timestamp,
                        "attempt_frame_path": attempt_frame_path,
                        "current_frame_url": current_frame_url,
                        "current_frame_payload": current_frame_payload,
                    }

                frame_attempt = frame_cache[retry_offset_seconds]
                sample_time_text = str(frame_attempt["sample_time_text"])
                sample_time_seconds = float(frame_attempt["sample_time_seconds"])
                attempt_frame_path = frame_attempt["attempt_frame_path"]
                current_frame_url = str(frame_attempt["current_frame_url"])
                current_frame_payload = frame_attempt["current_frame_payload"]
                attempt_ref_image_url = ref_image_url if include_ref_image else None
                attempt_ref_image_payload = (
                    ref_image_payload if include_ref_image else None
                )

                for model_index, attempt_config in enumerate(model_attempts):
                    attempt_model_name = str(attempt_config["model_name"])
                    attempt_model_role = str(
                        attempt_config.get("model_role") or "primary"
                    )
                    attempt_started_at = time.perf_counter()
                    try:
                        parsed, attempt_vlm_time, raw_text, vlm_debug = call_vlm(
                            client=client,
                            config=attempt_config,
                            current_frame_url=current_frame_url,
                            ref_image_url=attempt_ref_image_url,
                            prompt=attempt_prompt,
                        )
                        total_vlm_time += attempt_vlm_time
                        vlm_time = round(total_vlm_time, 3)
                        used_model_name = attempt_model_name
                        used_model_role = attempt_model_role
                        used_request_variant = request_variant
                        used_include_ref_image = include_ref_image
                        final_prompt = attempt_prompt
                        final_ref_image_payload = attempt_ref_image_payload
                        final_frame_path = attempt_frame_path
                        final_sample_time_text = sample_time_text
                        final_sample_time_seconds = sample_time_seconds
                        final_retry_offset_seconds = retry_offset_seconds
                        output = normalize_output(
                            parsed,
                            time_text=time_text,
                            frame_index=frame_index,
                            ref_step=current_ref_step,
                            total_steps=total_steps,
                            completed_steps=completed_steps,
                        )
                        frame_done = True
                        break
                    except Exception as exc:
                        attempt_vlm_time = round(
                            time.perf_counter() - attempt_started_at, 3
                        )
                        total_vlm_time += attempt_vlm_time
                        raw_text = (
                            exc.raw_text
                            if isinstance(exc, VLMJsonParseError)
                            else str(exc)
                        )
                        parsed = {
                            "error_type": exc.__class__.__name__,
                            "error": str(exc),
                            "traceback": traceback.format_exc(),
                        }
                        vlm_debug = getattr(exc, "vlm_debug", None)
                        used_model_name = attempt_model_name
                        used_model_role = attempt_model_role
                        used_request_variant = request_variant
                        used_include_ref_image = include_ref_image
                        final_prompt = attempt_prompt
                        final_ref_image_payload = attempt_ref_image_payload
                        final_frame_path = attempt_frame_path
                        final_sample_time_text = sample_time_text
                        final_sample_time_seconds = sample_time_seconds
                        final_retry_offset_seconds = retry_offset_seconds
                        output = {
                            "time": time_text,
                            "frame_index": frame_index,
                            "ifjudge": 0,
                            "now_step": current_ref_step,
                            "state": 0,
                            "reason": f"模型调用失败：{exc}",
                            "changepage": 0,
                        }
                        error_label = vlm_error_label(exc)
                        has_next_model = model_index < len(model_attempts) - 1
                        has_next_request = request_index < len(request_attempts) - 1

                        if is_sensitive_words_error(exc) and has_next_request:
                            next_request = request_attempts[request_index + 1]
                            next_offset = int(next_request["retry_offset_seconds"])
                            next_variant = str(next_request["request_variant"])
                            write_raw_output(
                                raw_output_dir,
                                frame_index=frame_index,
                                time_text=time_text,
                                sample_time_text=sample_time_text,
                                retry_offset_seconds=retry_offset_seconds,
                                ref_step=ref_step,
                                frame_path=attempt_frame_path,
                                current_frame_payload=current_frame_payload,
                                ref_image_payload=attempt_ref_image_payload,
                                prompt=attempt_prompt,
                                raw_text=raw_text,
                                parsed=parsed,
                                output=output,
                                vlm_time=attempt_vlm_time,
                                mock=False,
                                model_name=attempt_model_name,
                                model_role=attempt_model_role,
                                request_variant=request_variant,
                                include_ref_image=include_ref_image,
                                vlm_debug=vlm_debug,
                            )
                            if next_offset == retry_offset_seconds:
                                retry_label = f"retry variant={next_variant}"
                            else:
                                retry_label = (
                                    f"retry +{next_offset}s"
                                    f" variant={next_variant}"
                                )
                            print(
                                "[vlm]"
                                f" t={time_text}"
                                f" frame={frame_index}"
                                f" model={attempt_model_name}"
                                f" variant={request_variant}"
                                f" {error_label};"
                                f" {retry_label}",
                                flush=True,
                            )
                            break

                        if has_next_model:
                            next_config = model_attempts[model_index + 1]
                            write_raw_output(
                                raw_output_dir,
                                frame_index=frame_index,
                                time_text=time_text,
                                sample_time_text=sample_time_text,
                                retry_offset_seconds=retry_offset_seconds,
                                ref_step=ref_step,
                                frame_path=attempt_frame_path,
                                current_frame_payload=current_frame_payload,
                                ref_image_payload=attempt_ref_image_payload,
                                prompt=attempt_prompt,
                                raw_text=raw_text,
                                parsed=parsed,
                                output=output,
                                vlm_time=attempt_vlm_time,
                                mock=False,
                                model_name=attempt_model_name,
                                model_role=attempt_model_role,
                                request_variant=request_variant,
                                include_ref_image=include_ref_image,
                                vlm_debug=vlm_debug,
                            )
                            print(
                                "[vlm]"
                                f" t={time_text}"
                                f" frame={frame_index}"
                                f" model={attempt_model_name}"
                                f" variant={request_variant}"
                                f" {error_label};"
                                f" fallback_model={next_config['model_name']}",
                                flush=True,
                            )
                            continue

                        if is_retryable_vlm_error(exc):
                            if has_next_request:
                                next_request = request_attempts[request_index + 1]
                                next_offset = int(
                                    next_request["retry_offset_seconds"]
                                )
                                next_variant = str(next_request["request_variant"])
                                write_raw_output(
                                    raw_output_dir,
                                    frame_index=frame_index,
                                    time_text=time_text,
                                    sample_time_text=sample_time_text,
                                    retry_offset_seconds=retry_offset_seconds,
                                    ref_step=ref_step,
                                    frame_path=attempt_frame_path,
                                    current_frame_payload=current_frame_payload,
                                    ref_image_payload=attempt_ref_image_payload,
                                    prompt=attempt_prompt,
                                    raw_text=raw_text,
                                    parsed=parsed,
                                    output=output,
                                    vlm_time=attempt_vlm_time,
                                    mock=False,
                                    model_name=attempt_model_name,
                                    model_role=attempt_model_role,
                                    request_variant=request_variant,
                                    include_ref_image=include_ref_image,
                                    vlm_debug=vlm_debug,
                                )
                                if next_offset == retry_offset_seconds:
                                    retry_label = f"retry variant={next_variant}"
                                else:
                                    retry_label = (
                                        f"retry +{next_offset}s"
                                        f" variant={next_variant}"
                                    )
                                print(
                                    "[vlm]"
                                    f" t={time_text}"
                                    f" frame={frame_index}"
                                    f" model={attempt_model_name}"
                                    f" variant={request_variant}"
                                    f" {error_label};"
                                    f" {retry_label}",
                                    flush=True,
                                )
                                break
                            vlm_time = round(total_vlm_time, 3)
                            output["reason"] = (
                                f"模型调用失败：连续遇到 {error_label}，"
                                f"已尝试请求降级与原帧到 +{sensitive_retry_count}s，"
                                f"最后模型 {attempt_model_name} 仍失败，跳过这一帧。"
                            )
                            frame_done = True
                            break

                        write_raw_output(
                            raw_output_dir,
                            frame_index=frame_index,
                            time_text=time_text,
                            sample_time_text=sample_time_text,
                            retry_offset_seconds=retry_offset_seconds,
                            ref_step=ref_step,
                            frame_path=attempt_frame_path,
                            current_frame_payload=current_frame_payload,
                            ref_image_payload=attempt_ref_image_payload,
                            prompt=attempt_prompt,
                            raw_text=raw_text,
                            parsed=parsed,
                            output=output,
                            vlm_time=attempt_vlm_time,
                            mock=False,
                            model_name=attempt_model_name,
                            model_role=attempt_model_role,
                            request_variant=request_variant,
                            include_ref_image=include_ref_image,
                            vlm_debug=vlm_debug,
                        )
                        raise

                if frame_done:
                    break

        output = attach_frame_metadata(
            output,
            sample_time_text=final_sample_time_text,
            sample_time_seconds=final_sample_time_seconds,
            retry_offset_seconds=final_retry_offset_seconds,
            current_frame=final_frame_path,
            ref_step=current_ref_step,
        )
        vlm_outputs.append(output)
        write_json(output_json, vlm_outputs)
        post_vlm_event(args.event_url, output)
        write_raw_output(
            raw_output_dir,
            frame_index=frame_index,
            time_text=time_text,
            sample_time_text=final_sample_time_text,
            retry_offset_seconds=final_retry_offset_seconds,
            ref_step=ref_step,
            frame_path=final_frame_path,
            current_frame_payload=current_frame_payload,
            ref_image_payload=final_ref_image_payload,
            prompt=final_prompt,
            raw_text=raw_text,
            parsed=parsed,
            output=output,
            vlm_time=vlm_time,
            mock=args.mock,
            model_name=used_model_name,
            model_role=used_model_role,
            request_variant=used_request_variant,
            include_ref_image=used_include_ref_image,
            vlm_debug=vlm_debug,
        )
        csv_rows.append(
            {
                "frame_index": frame_index,
                "time": output["time"],
                "sample_time": output["sample_time"],
                "model": used_model_name,
                "variant": used_request_variant,
                "ref_step": current_ref_step,
                "ifjudge": output["ifjudge"],
                "now_step": output["now_step"],
                "state": output["state"],
                "changepage": output["changepage"],
                "current_frame": output["current_frame"],
                "vlm_time": f"{vlm_time:.3f}",
            }
        )
        write_csv(csv_log, csv_rows)

        print(
            "[vlm]"
            f" t={time_text}"
            f" frame={frame_index}"
            f" model={used_model_name}"
            f" variant={used_request_variant}"
            f" ref_step={current_ref_step}"
            f" ifjudge={output['ifjudge']}"
            f" now_step={output['now_step']}"
            f" state={output['state']}"
            f" changepage={output['changepage']}"
            f" vlm_time={vlm_time:.3f}s",
            flush=True,
        )

        if (
            output["state"] == 1
            and current_ref_step not in completed_steps
            and (output["changepage"] == 1 or current_ref_step == total_steps)
        ):
            completed_steps.add(current_ref_step)
            mark_step_completed(
                step_memory,
                step=current_ref_step,
                frame_index=frame_index,
                next_page_time=time_text if output["changepage"] == 1 else None,
            )
        if output["changepage"] == 1:
            current_ref_step = min(total_steps, current_ref_step + 1)
        write_json(step_memory_json, step_memory)

    write_csv(csv_log, csv_rows)
    write_json(step_memory_json, step_memory)
    print(f"[vlm] completed run_dir={run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
