#!/usr/bin/env python3
"""Build minimal prompt context for Qwen Realtime step narration.

The VLM and lesson schemas are still evolving, so this module deliberately uses
field aliases instead of depending on one exact JSON shape.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


VLM_ALIASES = {
    "change_page": ("changepage", "change_page", "changePage", "should_advance"),
    "now_step": ("now_step", "nowStep", "current_step", "currentStep", "step"),
    "next_step": ("next_step", "nextStep", "target_step", "targetStep"),
    "state": ("state", "completion", "score", "progress"),
    "reason": ("reason", "rationale", "visual_reason", "analysis"),
    "time": ("time", "timestamp", "video_time"),
    "frame_index": ("frame_index", "frameIndex", "frame"),
    "ifjudge": ("ifjudge", "if_judge", "can_judge", "is_judgeable"),
}

STEP_ALIASES = {
    "step_index": ("step_index", "step", "step_id", "page", "page_index", "index", "id"),
    "title": ("title", "name", "step_title", "page_title"),
    "image_file": ("image_file", "image", "ref_image", "reference_image"),
    "parts_needed": ("parts_needed", "parts", "components", "bricks"),
    "placement_instructions": (
        "placement_instructions",
        "instructions",
        "step_description",
        "ref_stepdescription",
        "description",
    ),
    "cultural_knowledge": (
        "cultural_knowledge",
        "culture",
        "history",
        "knowledge",
        "story",
        "background",
    ),
    "teaching_notes": ("teaching_notes", "notes", "teacher_notes", "guidance"),
    "component_expectation": ("component_expectation", "expectation", "visual_expectation"),
    "assembly_mode": ("assembly_mode", "mode"),
}

STEP_LABELS = {
    "title": "标题",
    "image_file": "参考图",
    "parts_needed": "零件或组件",
    "placement_instructions": "拼装说明",
    "cultural_knowledge": "历史文化与设计知识",
    "teaching_notes": "教学备注",
    "component_expectation": "完成标准",
    "assembly_mode": "拼装模式",
}

AUTO_FIELD_LIMITS = {
    "placement_instructions": 260,
    "cultural_knowledge": 360,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def first_value(data: dict[str, Any], aliases: tuple[str, ...], default: Any = None) -> Any:
    for key in aliases:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def latest_vlm_event(vlm_output: Any, event_index: int | None = None) -> dict[str, Any]:
    if isinstance(vlm_output, list):
        if not vlm_output:
            return {}
        index = event_index if event_index is not None else len(vlm_output) - 1
        return dict(vlm_output[index])
    if isinstance(vlm_output, dict):
        return dict(vlm_output)
    raise TypeError("VLM output must be a JSON object or an array of objects.")


def lesson_steps(lesson_plan: Any) -> list[dict[str, Any]]:
    if isinstance(lesson_plan, list):
        return [dict(item) for item in lesson_plan if isinstance(item, dict)]
    if isinstance(lesson_plan, dict):
        steps = lesson_plan.get("steps")
        if isinstance(steps, list):
            return [dict(item) for item in steps if isinstance(item, dict)]
    return []


def step_index(step: dict[str, Any]) -> int | None:
    return as_int(first_value(step, STEP_ALIASES["step_index"]))


def find_step(steps: list[dict[str, Any]], target_step: int) -> dict[str, Any] | None:
    for step in steps:
        if step_index(step) == target_step:
            return step
    return None


def clamp_step(value: int, steps: list[dict[str, Any]]) -> int:
    if not steps:
        return value
    known = [index for item in steps if (index := step_index(item)) is not None]
    if not known:
        return value
    return min(max(value, min(known)), max(known))


def infer_target_step(vlm_event: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    change_page = as_bool(first_value(vlm_event, VLM_ALIASES["change_page"], False))
    now_step = as_int(first_value(vlm_event, VLM_ALIASES["now_step"]), 1) or 1
    explicit_next = as_int(first_value(vlm_event, VLM_ALIASES["next_step"]))

    if explicit_next is not None:
        target_step = explicit_next
    elif change_page:
        target_step = now_step + 1
    else:
        target_step = now_step

    return {
        "change_page": change_page,
        "completed_step": now_step if change_page else None,
        "target_step": clamp_step(target_step, steps),
        "source_now_step": now_step,
    }


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value).strip()


def truncate_text(text: str, limit: int) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def normalized_step_fields(step: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for canonical, aliases in STEP_ALIASES.items():
        if canonical == "step_index":
            continue
        value = compact_text(first_value(step, aliases))
        if value:
            fields[canonical] = value
    return fields


def global_teaching_materials(lesson_plan: Any) -> str:
    if not isinstance(lesson_plan, dict):
        return ""
    return compact_text(lesson_plan.get("teaching_materials"))


def build_step_context(
    *,
    vlm_event: dict[str, Any],
    lesson_plan: Any,
    target_step: int | None = None,
) -> dict[str, Any]:
    steps = lesson_steps(lesson_plan)
    inferred = infer_target_step(vlm_event, steps)
    if target_step is not None:
        inferred["target_step"] = clamp_step(target_step, steps)

    should_send = bool(inferred["change_page"] or target_step is not None)
    step = find_step(steps, int(inferred["target_step"]))
    if step is None:
        raise ValueError(f"Could not find lesson step {inferred['target_step']}.")

    fields = normalized_step_fields(step)
    background = global_teaching_materials(lesson_plan)

    return {
        "should_send": should_send,
        "change_page": inferred["change_page"],
        "completed_step": inferred["completed_step"],
        "next_step": inferred["target_step"],
        "source_now_step": inferred["source_now_step"],
        "step": step,
        "fields": fields,
        "global_teaching_materials": background,
    }


def render_step_context(
    context: dict[str, Any],
    *,
    include_global_materials: bool = True,
) -> str:
    lines = [f"next_step：第 {context['next_step']} 步"]

    fields = context.get("fields") or {}
    for canonical, label in STEP_LABELS.items():
        value = fields.get(canonical)
        if value:
            lines.append(f"{label}：\n{value}")

    background = context.get("global_teaching_materials") if include_global_materials else ""
    if background:
        lines.append(f"全局背景材料（当前步骤材料不足时才使用）：\n{background}")

    return "\n\n".join(lines)


def render_auto_step_context(context: dict[str, Any]) -> str:
    lines = [f"next_step：第 {context['next_step']} 步"]
    fields = context.get("fields") or {}
    for canonical in ("placement_instructions", "cultural_knowledge"):
        value = fields.get(canonical)
        if not value:
            continue
        label = STEP_LABELS[canonical]
        limit = AUTO_FIELD_LIMITS[canonical]
        lines.append(f"{label}：\n{truncate_text(value, limit)}")
    return "\n\n".join(lines)


def render_auto_narration_prompt(context: dict[str, Any]) -> str:
    if not context.get("should_send"):
        return ""
    return "\n\n".join(
        [
            f"系统确认进入第 {context['next_step']} 步。",
            render_auto_step_context(context),
            (
                "请基于以上 next_step 和 lesson 内容，进行一次自动阶段播报。\n"
                "- 只用中文。\n"
                "- 控制在 15 秒以内，1 到 3 句。\n"
                "- 先说当前这一步要做什么。\n"
                "- 再补充一句和天坛、祈年殿、古建筑、颜色或结构相关的知识。\n"
                "- 最后自然提醒：如果想听更多，可以直接问我。\n"
                "- 不要提到 VLM、JSON、字段名、系统事件、内部判断。"
            ),
        ]
    )


def build_realtime_prompt(
    *,
    vlm_output: Any,
    lesson_plan: Any,
    event_index: int | None = None,
    target_step: int | None = None,
) -> dict[str, Any]:
    event = latest_vlm_event(vlm_output, event_index)
    context = build_step_context(
        vlm_event=event,
        lesson_plan=lesson_plan,
        target_step=target_step,
    )
    step_context_text = (
        render_step_context(context) if context.get("should_send") else ""
    )
    return {
        **context,
        "step_context_text": step_context_text,
        "auto_narration_prompt": render_auto_narration_prompt(context),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Qwen Realtime step context.")
    parser.add_argument("--vlm-output", type=Path, required=True)
    parser.add_argument("--lesson-plan", type=Path, required=True)
    parser.add_argument("--event-index", type=int)
    parser.add_argument("--target-step", type=int)
    parser.add_argument(
        "--format",
        choices=("auto-prompt", "step-context", "json"),
        default="auto-prompt",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build_realtime_prompt(
        vlm_output=load_json(args.vlm_output),
        lesson_plan=load_json(args.lesson_plan),
        event_index=args.event_index,
        target_step=args.target_step,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.format == "step-context":
        print(result["step_context_text"])
    else:
        print(result["auto_narration_prompt"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
