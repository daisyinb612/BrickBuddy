# Qwen Realtime Minimal VAD Demo

This folder contains the smallest useful Qwen Omni Realtime loop for the
LegoGlass experiments:

- microphone audio is streamed as 16 kHz mono PCM
- Qwen server VAD (`server_vad`) decides when a user turn starts and stops
- a test video file is sampled into JPEG frames
- every video frame is appended only after a successful audio append

That last point is important for WebSocket multimodal input. Qwen expects image
input to belong to the current audio buffer, so video should not be sent from an
independent loop while no audio chunks are being appended.

## Run

Set the API key first:

```bash
export DASHSCOPE_API_KEY=your_key_here
```

Run audio plus test video:

```bash
uv run python qwen_legoagent/realtime/basic_vad_video_file.py --video legoagentbackend/testvideo/step8test.mp4
```

Audio-only sanity check:

```bash
uv run python qwen_legoagent/realtime/basic_vad_video_file.py
```

List local audio devices:

```bash
uv run python qwen_legoagent/realtime/basic_vad_video_file.py --list-devices
```

Then pass `--input-device-index` or `--output-device-index` if needed.

## Prompt

The default realtime system prompt lives in `system_prompt.md`. The demo loads it
as the session instructions. Use `--instructions "..."` only when you want to
temporarily override the whole prompt from the command line.

## Step context

`context_builder.py` turns a page-change VLM output plus a lesson plan into text
that can be sent to Qwen Realtime with `create_response`.

```bash
uv run python qwen_legoagent/realtime/context_builder.py \
  --vlm-output qwen_legoagent/vlm/example_vlmoutput.json \
  --lesson-plan backend/generated/10steplesson_plan/10steplesson_plan.json
```

The builder accepts evolving field names through aliases. For the current VLM
shape, `changepage=1` and `now_step=N` means the completed step is `N` and the
target narration step is `N + 1`, unless an explicit `next_step` field exists.
When `changepage=0`, it returns no narration prompt.

## Builtin Realtime Runner

`run_builtin_realtime_turns.py` runs the current minimal realtime flow with the
Mac's builtin microphone and speakers:

```bash
qwen_legoagent/realtime/run_builtin_realtime_turns.sh \
  --vlm-output qwen_legoagent/vlm/example_vlmoutput.json
```

Runtime behavior:

- system prompt is sent once during `session.update`
- microphone audio streams continuously for server VAD
- test video frames are appended after audio chunks
- `--vlm-output` is polled once per second
- only `changepage=1` sends a turn prompt
- the turn prompt is loaded from `turnstep_prompt/trunprompt_allsteps/step{N}.md`

Use `--no-video` for an audio-only check, or `--duration 30` for a short timed
run.

## Notes

- Video file input requires `ffmpeg`. Passing a directory of `.jpg`, `.jpeg`,
  `.png`, or `.webp` frames also works.
- Default video sampling is `--video-fps 1.0`.
- Use headphones during tests if playback leaks into the microphone.
- The default realtime URL/model/ASR model match the current LegoGlass realtime
  config, and can be overridden with command-line flags.
