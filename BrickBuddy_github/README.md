# BrickBuddy

BrickBuddy is a multimodal LEGO assembly assistant for classroom and maker-space activities. It combines a web activity dashboard, a lightweight Python backend, VLM-based assembly progress checking, and Qwen Realtime voice coaching to help students build LEGO structures step by step.

The current demo focuses on a Temple of Heaven LEGO activity: the system shows assembly guide images, receives live or simulated first-person video, detects page/step changes with a vision-language model, and triggers realtime spoken guidance for the next step.

Demo video: https://www.youtube.com/watch?v=3-L7GyE6FCs

## Features

- Live activity cockpit built with Expo, React Native, and React Native Web
- Assembly guide timeline with manual and VLM-driven step switching
- Video source modes for simulation video, glasses HTTP stream, and public RTMP/HTTP-FLV playback
- Server-Sent Events bridge for VLM outputs such as `now_step`, `state`, and `changepage`
- Qwen Realtime assistant for microphone input, audio playback, and step-specific coaching prompts
- VLM progress checker with OpenAI-compatible model configuration
- Chinese/English UI toggle
- Run logs for VLM frames, model outputs, realtime audio, and interaction history

## Project Structure

```text
.
├── frontend/                       # Expo / React Native Web dashboard
│   ├── App.tsx                     # Main app entry
│   └── src/pages/
│       ├── LegoActivityDashboard.tsx
│       └── TeachingGuideDashboard.tsx
├── legoagentbackend/
│   ├── server.py                   # Local backend, video server, SSE bridge, VLM scheduler
│   ├── rawdata/                    # Step metadata, instruction images, component images
│   └── testvideo/                  # Simulation video used by the demo
└── qwen_legoagent/
    ├── vlm/                        # VLM frame checker and model config
    └── realtime/                   # Qwen Realtime audio/video loop and turn prompts
```

## Requirements

- Node.js 20 or newer
- Python 3.10 or newer
- `ffmpeg` and `ffprobe`
- A microphone and speaker for realtime voice tests
- API keys for the model services you want to use

On macOS, install system tools first:

```bash
brew install ffmpeg portaudio
```

## Setup

Clone the repository and install frontend dependencies:

```bash
cd BrickBuddy_github/frontend
npm install
```

Create a Python environment from the repository root:

```bash
cd BrickBuddy_github
python3 -m venv .venv
source .venv/bin/activate
pip install python-dotenv pillow openai dashscope pyaudio
```

If `pyaudio` fails to install, make sure `portaudio` is installed and retry inside the virtual environment.

## Environment Variables

Create a local `.env` file in the repository root. Do not commit real API keys.

```bash
# Required for Qwen Realtime
DASHSCOPE_API_KEY=your_dashscope_key

# Required for GPT/Gemini-style VLM configs
VLM_API_KEY=your_vlm_gateway_key
VLM_BASE_URL=https://your-openai-compatible-endpoint/v1

# Optional model overrides
LEGOGLASS_PROGRESS_MODEL=gpt-5.5
VLM_GEMINI_MODEL=gemini-3.5-flash
VLM_QWEN_MODEL=qwen3.5-omni-flash
QWEN_REALTIME_MODEL=qwen3.5-omni-flash-realtime
```

For the frontend, you can create `frontend/.env`:

```bash
EXPO_PUBLIC_LEGO_BACKEND_URL=http://127.0.0.1:8765
EXPO_PUBLIC_VLM_EVENT_URL=http://127.0.0.1:8765/events
EXPO_PUBLIC_SIMULATION_VIDEO_URL=http://127.0.0.1:8765/video/step8test.mp4
```

## Quick Start

Start the local backend:

```bash
cd BrickBuddy_github
source .venv/bin/activate
python legoagentbackend/server.py --source-mode simulation
```

Start the web dashboard in another terminal:

```bash
cd BrickBuddy_github/frontend
npm run web
```

Open the Expo web URL shown in the terminal. In the dashboard, choose the simulation source and start teaching. The frontend calls `/vlm/start`; the backend samples video frames, runs the VLM checker, publishes events to `/events`, and starts Qwen Realtime unless `--no-realtime` is passed.

Large local demo videos are not committed to this repository. To run the default simulation path, place `step8test.mp4` in `legoagentbackend/testvideo/`, or pass a different video path/stream URL to the backend and VLM commands.

For a UI-only check without model calls, keep the backend and frontend running, then post a fake VLM event:

```bash
curl -X POST http://127.0.0.1:8765/events \
  -H "Content-Type: application/json" \
  -d '{"time":"00:00:15","frame_index":1,"ifjudge":1,"now_step":1,"state":1,"changepage":1,"reason":"mock step complete"}'
```

## Backend Commands

Run with the default glasses stream mode:

```bash
python legoagentbackend/server.py \
  --source-mode glasses \
  --glasses-stream-url http://172.20.10.9:8080/
```

Run simulation mode without auto-starting Qwen Realtime:

```bash
python legoagentbackend/server.py \
  --source-mode simulation \
  --no-realtime
```

Useful backend endpoints:

- `GET /health` checks whether the backend is alive
- `GET /events` opens the SSE stream consumed by the frontend
- `POST /events` pushes a VLM event manually
- `POST /vlm/start` starts VLM sampling and optional realtime coaching
- `POST /vlm/stop` stops VLM and realtime subprocesses
- `GET /vlm/status` returns current process and run status
- `GET /video/step8test.mp4` serves the simulation video
- `GET /rawdata/...` serves guide and component assets

Example `/vlm/start` payload:

```json
{
  "source_mode": "simulation",
  "interval_seconds": 15,
  "model_key": "gpt-5.5",
  "start_realtime": true,
  "include_final_frame": true
}
```

For glasses streaming:

```json
{
  "source_mode": "glasses",
  "stream_url": "http://172.20.10.9:8080/",
  "interval_seconds": 5,
  "model_key": "qwen"
}
```

## VLM Runner

The VLM module samples frames, compares the current build with step metadata, writes JSON/CSV logs, and posts each event back to the backend.

Run a mock VLM pass without calling a model:

```bash
python qwen_legoagent/vlm/vlm.py \
  --video legoagentbackend/testvideo/step8test.mp4 \
  --mock \
  --max-frames 3
```

Run a real single-video pass:

```bash
python qwen_legoagent/vlm/vlm.py \
  --video legoagentbackend/testvideo/step8test.mp4 \
  --model-key gpt \
  --interval-seconds 15
```

Model configuration lives in `qwen_legoagent/vlm/testmodel_list.json`. Supported aliases include `gpt`, `gemini`, `qwen`, and `qwen3`.

## Qwen Realtime

The realtime module streams microphone audio to Qwen Realtime, optionally appends sampled video frames, and sends step-specific prompts when VLM output says the page changed.

Audio plus simulation video:

```bash
python qwen_legoagent/realtime/basic_vad_video_file.py \
  --video legoagentbackend/testvideo/step8test.mp4
```

Audio-only sanity check:

```bash
python qwen_legoagent/realtime/basic_vad_video_file.py
```

List local audio devices:

```bash
python qwen_legoagent/realtime/basic_vad_video_file.py --list-devices
```

Run the builtin realtime loop against an existing VLM output file:

```bash
python qwen_legoagent/realtime/run_builtin_realtime_turns.py \
  --vlm-output qwen_legoagent/vlm/vlmhistory/<run-dir>/vlmoutput.json \
  --video legoagentbackend/testvideo/step8test.mp4
```

## Logs and Outputs

Generated outputs are useful for experiments but can become very large:

- `qwen_legoagent/vlm/vlmhistory/` stores sampled frames, VLM JSON, raw outputs, and CSV logs
- `qwen_legoagent/realtime/realtimehistory/` stores realtime manifests, audio, video, events, and metrics summaries
- `qwen_legoagent/vlm/captured_frames/` may contain temporary extracted frames

Keep a small `example/` folder if needed, but avoid committing full experiment histories.

## Development Checks

Run frontend type checking:

```bash
cd frontend
npm run typecheck
```

Check backend health:

```bash
curl http://127.0.0.1:8765/health
```

## Before Publishing to GitHub

Make sure these files and folders are ignored or removed before committing:

```text
.DS_Store
.env
*.pyc
__pycache__/
frontend/node_modules/
frontend/.expo/
frontend/.env
qwen_legoagent/vlm/vlmhistory/
qwen_legoagent/realtime/realtimehistory/
qwen_legoagent/vlm/captured_frames/
```

Also check that no API keys, private video/audio recordings, or student data are included in committed logs.

## License

Add a license before public release, for example MIT for open-source code or a custom research-only license if the project contains unpublished experiment assets.
