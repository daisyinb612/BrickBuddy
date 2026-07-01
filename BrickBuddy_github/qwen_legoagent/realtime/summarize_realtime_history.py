#!/usr/bin/env python3
"""Summarize Qwen realtime experiment history directories into a CSV."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_HISTORY_ROOT = SCRIPT_DIR / "realtimehistory"
DEFAULT_OUTPUT = DEFAULT_HISTORY_ROOT / "realtime_history_summary.csv"


FIELDNAMES = [
    "run_id",
    "started_at",
    "model",
    "voice",
    "trigger_count",
    "turn_count",
    "agent_response_count",
    "step_trigger_count",
    "user_trigger_count",
    "useful_user_input_count",
    "invalid_user_input_count",
    "false_user_trigger_count",
    "avg_agent_response_latency",
    "max_agent_response_latency",
    "user_interrupt_count",
    "step_right",
    "step_right_count",
    "step_total_count",
    "vlm_step_payload_avg_latency",
    "vlm_step_payload_max_latency",
    "vlm_step_payload_min_latency",
    "server_error_count",
]


USEFUL_REQUEST_MARKERS = (
    "给我讲",
    "讲一讲",
    "讲讲",
    "介绍",
    "解释",
    "为什么",
    "怎么",
    "怎样",
    "如何",
    "什么",
    "帮我",
    "不会",
    "不懂",
    "听更多",
)
USEFUL_SESSION_END_MARKERS = ("不用", "拜拜", "结束", "暂停", "停下")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            clean = line.strip()
            if not clean:
                continue
            try:
                item = json.loads(clean)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def log_rows(run_dir: Path, preferred_name: str) -> list[dict[str, Any]]:
    rows = read_jsonl(run_dir / preferred_name)
    if rows:
        return rows
    if preferred_name != "realtime_rawchathistory.jsonl":
        return read_jsonl(run_dir / "realtime_rawchathistory.jsonl")
    return []


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def is_useful_user_input(text: str) -> bool:
    clean = compact_text(text)
    if not clean:
        return False
    return any(marker in clean for marker in USEFUL_REQUEST_MARKERS) or any(
        marker in clean for marker in USEFUL_SESSION_END_MARKERS
    )


def is_step_like_response(text: str) -> bool:
    clean = compact_text(text)
    if "第" in clean and "步" in clean:
        return True
    return any(marker in clean for marker in ("进入", "搭", "屋檐", "屋顶", "底板"))


def parse_wrong_steps(values: list[str]) -> dict[str, set[int]]:
    output: dict[str, set[int]] = {}
    for value in values:
        if ":" not in value:
            continue
        run_id, step_text = value.split(":", 1)
        step = safe_int(step_text)
        if not run_id or step is None:
            continue
        output.setdefault(run_id, set()).add(step)
    return output


def parse_step_totals(values: list[str]) -> dict[str, int]:
    output: dict[str, int] = {}
    for value in values:
        if ":" not in value:
            continue
        run_id, total_text = value.split(":", 1)
        total = safe_int(total_text)
        if run_id and total is not None:
            output[run_id] = total
    return output


def load_manifest(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(run_dir / "run_manifest.json")
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "started_at": manifest.get("started_at"),
        "model": manifest.get("model"),
        "voice": metadata.get("voice"),
    }


def collect_triggers(conversation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    triggers: list[dict[str, Any]] = []
    for row in conversation_rows:
        row_type = row.get("type")
        if row_type not in {"user_transcript", "step_prompt_sent"}:
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        text = payload.get("user_transcript") or payload.get("prompt") or ""
        trigger = {
            "sequence": row.get("sequence"),
            "type": row_type,
            "trigger": payload.get("trigger"),
            "turn_id": payload.get("turn_id"),
            "step": payload.get("step"),
            "elapsed_seconds": safe_float(row.get("elapsed_seconds")),
            "text": compact_text(text),
            "matched": False,
        }
        trigger["useful_user_input"] = (
            row_type == "user_transcript" and is_useful_user_input(trigger["text"])
        )
        triggers.append(trigger)
    return triggers


def collect_responses(conversation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    for row in conversation_rows:
        if row.get("type") != "assistant_response":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        trigger_context = payload.get("trigger_context")
        if not isinstance(trigger_context, dict):
            trigger_context = {}
        responses.append(
            {
                "sequence": row.get("sequence"),
                "elapsed_seconds": safe_float(row.get("elapsed_seconds")),
                "text": compact_text(payload.get("assistant_transcript")),
                "trigger_context": trigger_context,
            }
        )
    return responses


def trigger_key(trigger: dict[str, Any]) -> tuple[Any, Any]:
    return trigger.get("trigger"), trigger.get("turn_id")


def choose_latency_trigger(
    response: dict[str, Any],
    triggers: list[dict[str, Any]],
    triggers_by_key: dict[tuple[Any, Any], dict[str, Any]],
) -> dict[str, Any] | None:
    response_time = response.get("elapsed_seconds")
    if response_time is None:
        return None

    context = response.get("trigger_context") or {}
    context_key = (context.get("trigger"), context.get("turn_id"))
    context_trigger = triggers_by_key.get(context_key)
    response_step_like = is_step_like_response(str(response.get("text") or ""))

    if (
        context_trigger is not None
        and not context_trigger.get("matched")
        and context_trigger.get("elapsed_seconds") is not None
        and float(context_trigger["elapsed_seconds"]) <= float(response_time)
    ):
        if context_trigger.get("type") == "step_prompt_sent":
            return context_trigger
        if context_trigger.get("text") and not response_step_like:
            return context_trigger

    candidates = [
        trigger
        for trigger in triggers
        if not trigger.get("matched")
        and trigger.get("elapsed_seconds") is not None
        and float(trigger["elapsed_seconds"]) <= float(response_time)
    ]
    if not candidates:
        return None

    if response_step_like:
        step_candidates = [
            trigger for trigger in candidates if trigger.get("type") == "step_prompt_sent"
        ]
        if step_candidates:
            return max(step_candidates, key=lambda item: float(item["elapsed_seconds"]))

    useful_user_candidates = [
        trigger for trigger in candidates if trigger.get("useful_user_input")
    ]
    if useful_user_candidates:
        return max(useful_user_candidates, key=lambda item: float(item["elapsed_seconds"]))

    nonempty_user_candidates = [
        trigger
        for trigger in candidates
        if trigger.get("type") == "user_transcript" and trigger.get("text")
    ]
    if nonempty_user_candidates:
        return max(nonempty_user_candidates, key=lambda item: float(item["elapsed_seconds"]))

    return max(candidates, key=lambda item: float(item["elapsed_seconds"]))


def agent_response_latencies(
    triggers: list[dict[str, Any]],
    responses: list[dict[str, Any]],
) -> list[float]:
    for trigger in triggers:
        trigger["matched"] = False

    triggers_by_key = {trigger_key(trigger): trigger for trigger in triggers}
    latencies: list[float] = []
    for response in responses:
        response_time = response.get("elapsed_seconds")
        if response_time is None:
            continue
        trigger = choose_latency_trigger(response, triggers, triggers_by_key)
        if trigger is None or trigger.get("elapsed_seconds") is None:
            continue
        trigger["matched"] = True
        latencies.append(float(response_time) - float(trigger["elapsed_seconds"]))
    return latencies


def vlm_step_payload_latencies(step_rows: list[dict[str, Any]]) -> list[float]:
    delays: list[float] = []
    for row in step_rows:
        if row.get("type") != "step_prompt_sent":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        vlm_event = payload.get("vlm_event")
        if not isinstance(vlm_event, dict):
            continue
        prompt_time = safe_float(row.get("elapsed_seconds"))
        sample_time = safe_float(vlm_event.get("sample_time_seconds"))
        if prompt_time is None or sample_time is None:
            continue
        delays.append(prompt_time - sample_time)
    return delays


def user_interrupt_count(event_rows: list[dict[str, Any]]) -> int:
    active = False
    count = 0
    for row in event_rows:
        row_type = str(row.get("type") or "")
        if row_type in {
            "response_created",
            "response_audio_delta",
            "response_text_delta",
            "response.audio.delta",
            "response.audio_transcript.delta",
        }:
            active = True
        elif row_type == "speech_started" and active:
            count += 1
        elif row_type in {"response_done", "response.done", "response_audio_done"}:
            active = False
    return count


def round_or_blank(value: float | None, digits: int = 2) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def summarize_run(
    run_dir: Path,
    *,
    wrong_steps: set[int],
    step_total_override: int | None,
) -> dict[str, Any]:
    manifest = load_manifest(run_dir)
    conversation_rows = log_rows(run_dir, "conversation_log.jsonl")
    step_rows = log_rows(run_dir, "step_triggered_conversations.jsonl")
    event_rows = read_jsonl(run_dir / "events.jsonl")

    triggers = collect_triggers(conversation_rows)
    responses = collect_responses(conversation_rows)

    user_triggers = [trigger for trigger in triggers if trigger["type"] == "user_transcript"]
    step_triggers = [
        trigger for trigger in triggers if trigger["type"] == "step_prompt_sent"
    ]
    false_user_count = sum(1 for trigger in user_triggers if not trigger["text"])
    useful_user_count = sum(
        1 for trigger in user_triggers if trigger.get("useful_user_input")
    )
    invalid_user_count = sum(
        1
        for trigger in user_triggers
        if trigger["text"] and not trigger.get("useful_user_input")
    )

    response_latencies = agent_response_latencies(triggers, responses)
    vlm_latencies = vlm_step_payload_latencies(step_rows)

    detected_step_total = max(
        [safe_int(trigger.get("step")) or 0 for trigger in step_triggers] or [0]
    )
    step_total = step_total_override or detected_step_total
    if step_total <= 0:
        step_total = len(step_triggers)
    step_right_count = max(0, step_total - len(wrong_steps))

    summary = {
        "run_id": run_dir.name,
        "started_at": manifest.get("started_at") or "",
        "model": manifest.get("model") or "",
        "voice": manifest.get("voice") or "",
        "trigger_count": len(triggers),
        "turn_count": len(responses),
        "agent_response_count": len(responses),
        "step_trigger_count": len(step_triggers),
        "user_trigger_count": len(user_triggers),
        "useful_user_input_count": useful_user_count,
        "invalid_user_input_count": invalid_user_count,
        "false_user_trigger_count": false_user_count,
        "avg_agent_response_latency": round_or_blank(
            statistics.mean(response_latencies) if response_latencies else None
        ),
        "max_agent_response_latency": round_or_blank(
            max(response_latencies) if response_latencies else None
        ),
        "user_interrupt_count": user_interrupt_count(event_rows),
        "step_right": f"{step_right_count}/{step_total}" if step_total else "",
        "step_right_count": step_right_count if step_total else "",
        "step_total_count": step_total if step_total else "",
        "vlm_step_payload_avg_latency": round_or_blank(
            statistics.mean(vlm_latencies) if vlm_latencies else None
        ),
        "vlm_step_payload_max_latency": round_or_blank(
            max(vlm_latencies) if vlm_latencies else None
        ),
        "vlm_step_payload_min_latency": round_or_blank(
            min(vlm_latencies) if vlm_latencies else None
        ),
        "server_error_count": sum(1 for row in event_rows if row.get("type") == "server_error"),
    }
    return summary


def history_dirs(path: Path) -> list[Path]:
    if path.is_dir() and (
        (path / "conversation_log.jsonl").exists()
        or (path / "realtime_rawchathistory.jsonl").exists()
    ):
        return [path]
    if not path.exists():
        return []
    return sorted(
        item
        for item in path.iterdir()
        if item.is_dir()
        and (
            (item / "conversation_log.jsonl").exists()
            or (item / "realtime_rawchathistory.jsonl").exists()
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize realtimehistory logs into a CSV."
    )
    parser.add_argument(
        "history",
        nargs="?",
        default=str(DEFAULT_HISTORY_ROOT),
        help="A realtimehistory root, or one run directory.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="CSV output path.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help=(
            "Also write realtime_metrics_summary.csv and "
            "realtime_metrics_summary.json into each run directory."
        ),
    )
    parser.add_argument(
        "--wrong-step",
        action="append",
        default=[],
        metavar="RUN_ID:STEP",
        help="Mark one step as wrong for a run, e.g. realtime-qwen-...:5.",
    )
    parser.add_argument(
        "--step-total",
        action="append",
        default=[],
        metavar="RUN_ID:TOTAL",
        help="Override total step count for a run.",
    )
    args = parser.parse_args()

    wrong_steps_by_run = parse_wrong_steps(args.wrong_step)
    step_totals_by_run = parse_step_totals(args.step_total)

    runs = history_dirs(Path(args.history))
    summaries = [
        summarize_run(
            run,
            wrong_steps=wrong_steps_by_run.get(run.name, set()),
            step_total_override=step_totals_by_run.get(run.name),
        )
        for run in runs
    ]

    if args.in_place:
        for run, summary in zip(runs, summaries, strict=True):
            run_csv = run / "realtime_metrics_summary.csv"
            with run_csv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
                writer.writeheader()
                writer.writerow(summary)
            (run / "realtime_metrics_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(summaries)

    print(f"Wrote {len(summaries)} row(s) to {output_path}")


if __name__ == "__main__":
    main()
