#!/usr/bin/env python3
"""Generate per-step turn prompts for Qwen Realtime narration.

This is an offline helper. It reads the long lesson material, asks an LLM to
compress each step into a short realtime-friendly narration, then writes one
Markdown file per step. Each file keeps both the short narration and the source
step material, so realtime can answer immediate follow-up questions more
accurately.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REALTIME_LESSON = ROOT / "legoagentbackend/rawdata/steplesson_forrealtime.json"
DEFAULT_VLM_STEPINFO = ROOT / "legoagentbackend/rawdata/stepinfor_forvlm.json"
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("trunprompt_allsteps")
DEFAULT_MODEL = os.getenv("TURN_PROMPT_MODEL", "gpt-5.5")


SYSTEM_PROMPT = """你是 LegoGlass 的离线内容编辑器。
任务：把乐高拼搭步骤的长说明压缩成适合 realtime 自动播报的一小段中文。

要求：
- 输出自然口语，不要像说明书。
- 每步 2 到 3 句，最长不超过 15 秒朗读。
- 必须包含：当前步骤要做什么 + 一句天坛/祈年殿/古建筑/结构/颜色知识。
- 结尾自然提醒：想听更多可以直接问我。
- 不要提到 JSON、字段名、VLM、系统、模型、prompt。
- 不要虚构原材料没有的历史事实。
"""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_steps(data: Any, *, index_keys: tuple[str, ...]) -> dict[int, dict[str, Any]]:
    if isinstance(data, dict):
        raw_steps = data.get("steps") or []
    elif isinstance(data, list):
        raw_steps = data
    else:
        raw_steps = []

    steps: dict[int, dict[str, Any]] = {}
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        step_id = None
        for key in index_keys:
            if key in item:
                try:
                    step_id = int(item[key])
                    break
                except (TypeError, ValueError):
                    pass
        if step_id is not None:
            steps[step_id] = item
    return steps


def make_user_prompt(step_id: int, realtime_step: dict[str, Any], vlm_step: dict[str, Any]) -> str:
    cultural = str(realtime_step.get("cultural_knowledge") or "").strip()
    step_description = str(vlm_step.get("stepdescription") or "").strip()
    components = [
        str(component.get("description", "")).strip()
        for component in (vlm_step.get("stepcomponents") or [])
        if isinstance(component, dict) and component.get("description")
    ]
    components_text = "、".join(components)
    return f"""请为第 {step_id} 步生成 auto_narration。

步骤描述：
{step_description}

组件：
{components_text}

文化知识：
{cultural}
"""


def step_material(realtime_step: dict[str, Any], vlm_step: dict[str, Any]) -> dict[str, str]:
    components = [
        str(component.get("description", "")).strip()
        for component in (vlm_step.get("stepcomponents") or [])
        if isinstance(component, dict) and component.get("description")
    ]
    return {
        "step_description": str(vlm_step.get("stepdescription") or "").strip(),
        "components": "、".join(components),
        "cultural_knowledge": str(realtime_step.get("cultural_knowledge") or "").strip(),
    }


def render_markdown(
    step_id: int,
    auto_narration: str,
    *,
    material: dict[str, str],
) -> str:
    return f"""# Step {step_id} Realtime Turn Prompt

```text
系统确认进入第 {step_id} 步。

请只进行一次简短自动播报：
- 优先播报「建议播报」里的内容，可以自然润色，但不要展开。
- 控制在 15 秒以内，1 到 3 句。
- 不要完整朗读「当前步骤资料」。
- 如果用户随后追问，再基于「当前步骤资料」回答更多细节。
- 不要提到系统、字段名、JSON、VLM 或 prompt。

建议播报：
{auto_narration.strip()}

当前步骤资料：
step_index: {step_id}

step_description:
{material.get("step_description") or "无"}

components:
{material.get("components") or "无"}

cultural_knowledge:
{material.get("cultural_knowledge") or "无"}
```
"""


def generate_with_llm(client: OpenAI, model: str, user_prompt: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
    )
    return (response.choices[0].message.content or "").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate realtime turn prompt markdown files.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--realtime-lesson", type=Path, default=DEFAULT_REALTIME_LESSON)
    parser.add_argument("--vlm-stepinfo", type=Path, default=DEFAULT_VLM_STEPINFO)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-step", type=int, default=1)
    parser.add_argument("--end-step", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    realtime_steps = normalize_steps(
        load_json(args.realtime_lesson),
        index_keys=("step_index", "stepid", "step"),
    )
    vlm_steps = normalize_steps(
        load_json(args.vlm_stepinfo),
        index_keys=("stepid", "step_index", "step"),
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAI()
    for step_id in range(args.start_step, args.end_step + 1):
        user_prompt = make_user_prompt(
            step_id,
            realtime_steps.get(step_id, {}),
            vlm_steps.get(step_id, {}),
        )
        auto_narration = generate_with_llm(client, args.model, user_prompt)
        material = step_material(
            realtime_steps.get(step_id, {}),
            vlm_steps.get(step_id, {}),
        )
        output_path = args.output_dir / f"step{step_id}.md"
        output_path.write_text(
            render_markdown(step_id, auto_narration, material=material),
            encoding="utf-8",
        )
        print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
