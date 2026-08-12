# Nautilus Studio

[![CI](https://github.com/yeahdongcn/nautilus-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/yeahdongcn/nautilus-studio/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Nautilus Studio is a creator-first, agentic AI film workshop. A creator gives
it an idea and optional reference media; Nautilus turns that into a story,
editable storyboard, continuous video clips, and a final long-form cut.

> Project status: experimental alpha. The local server has no built-in
> authentication and should be deployed only on localhost or behind an
> authenticated reverse proxy.

## Why Nautilus

Most diffusion and omni models generate short clips. Nautilus makes long-form
creation practical by keeping the technical graph behind a creator-oriented
workflow:

- story and storyboard planning from one prompt;
- a persistent material library with character, location, prop, style,
  start-frame, and audio roles;
- FL2VA and Ref2VA video adapters;
- previous-boundary-to-next-anchor continuity;
- optional story-aware anchor frames built by an image-edit provider;
- provider switching between self-hosted vLLM-Omni and hosted APIs;
- resumable projects, progress tracking, inline preview, and sidecar subtitles.

## Architecture

```text
Creator brief + material library
              |
          Film planner
              |
     World bible + storyboard
              |
       optional Image Edit
   (vLLM-Omni or vendor API)
              |
       MiniMax-H3 / provider
              |
      clips + boundary frames
              |
         final assembly
```

The project uses a provider-neutral Film IR. Planner, image-edit, video, and
media-assembly implementations are adapters rather than assumptions embedded in
the UI.

## Quick start

Requirements:

- Python 3.10 or newer;
- `ffmpeg` and `ffprobe`;
- optional model endpoints for planning, image editing, or video generation.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
set -a && . ./.env && set +a
nautilus-studio --host 127.0.0.1 --port 7860
```

Open `http://127.0.0.1:7860`. Without external endpoints, the deterministic
planner, project editor, material library, and compiler remain available.

## Docker

```bash
docker compose up --build
```

The compose file exposes port `7860`, keeps state in a named volume, and mounts
`./media` read-only. Model servers remain independent services and are
configured with environment variables. Networks that mirror Docker Hub can
override the build bases with `NODE_IMAGE` and `PYTHON_IMAGE` build arguments;
the public defaults remain the official Node and Python slim images.

## Creator UI

The creator workspace lives in `web/` and is built into the Docker image. The
small shell under `src/.../static` remains a source-install fallback when a web
bundle has not been built:

```bash
cd web
npm ci
npm run dev -- --host 0.0.0.0 --port 5173
```

The development server proxies `/api` to a local Studio on port `7860` without
changing the backend API contract.

The direction, project bible, shot cards, and material metadata are edited in
focused dialogs. The storyboard stays compact while full prompts and ordered
references remain available when a creator opens a shot.

Explicit creator start frames always win. If no start frame is selected and an
image-edit provider is configured, Nautilus composes the opening anchor from
the project context and ordered scene/character/prop references instead.

Project aspect ratio is sent explicitly to H3. The current model-native
canvases are `1280x704` (16:9 landscape), `704x1280` (9:16 portrait), and
`960x960` (square); input images are center-cropped to the selected canvas with
no geometric stretching.

## Model providers

### Planner

Set an OpenAI-compatible chat endpoint:

```bash
export STUDIO_PLANNER_BASE_URL=http://127.0.0.1:8000/v1
export STUDIO_PLANNER_MODEL=your-model
export STUDIO_PLANNER_API_KEY=...
```

If no endpoint is configured, Nautilus uses its deterministic planner.

### MiniMax-H3 video

FL2VA and Ref2VA are separate model partitions and may be served independently:

```bash
export STUDIO_H3_FL2VA_URL=http://127.0.0.1:8091
export STUDIO_H3_REF2VA_URL=http://127.0.0.1:8092
```

Nautilus submits durable jobs to the vLLM-Omni `/v1/videos` endpoint and polls
their status, so long 50-step clips are not held behind a short HTTP request
timeout. The adapter still supports `/v1/videos/sync` for focused smoke tests.
For vLLM-Omni servers, set `VLLM_OMNI_VIDEO_SYNC_TIMEOUT` high enough when using
the synchronous endpoint.

### Image Edit

Image editing constructs a scene-complete anchor from ordered location,
character, and prop references. It is optional and disabled by default.

```bash
export STUDIO_IMAGE_EDIT_PROVIDER=vllm-omni
export STUDIO_IMAGE_EDIT_BASE_URL=http://127.0.0.1:8093
export STUDIO_IMAGE_EDIT_MODEL=Qwen/Qwen-Image-Edit-2511
export STUDIO_IMAGE_EDIT_MAX_REFERENCES=4
export STUDIO_IMAGE_EDIT_ANCHOR_MODE=scene-cuts
```

Use `openai-compatible` instead of `vllm-omni` for a hosted provider. See
[Image-edit providers](docs/image-edit-providers.md) for the request contract,
reference manifest, and acceptance matrix.

## Configuration

All configuration uses `STUDIO_` environment variables. Start from
[`.env.example`](.env.example). Important paths:

- `STUDIO_DATA_DIR`: SQLite, imported assets, and render outputs;
- `STUDIO_IMPORT_ROOTS`: colon-separated server paths allowed for asset import;
- `STUDIO_COPY_IMPORTED_ASSETS`: copy imported files into studio storage;
- `STUDIO_FFMPEG` and `STUDIO_FFPROBE`: media binaries or wrappers.

API keys are never required in project data and should only come from the
environment or a deployment secret store.

## Development

```bash
make check
```

Or run the checks individually:

```bash
ruff format --check src tests scripts/probe-image-edit.py scripts/probe-h3-continuation.py scripts/verify-qwen-image-edit-checkpoint.py
ruff check src tests scripts/probe-image-edit.py scripts/probe-h3-continuation.py scripts/verify-qwen-image-edit-checkpoint.py
pytest -q
cd web && npm ci && npm run build && npm audit --audit-level=high
```

The test suite uses mock provider transports. Hardware claims require a real
model endpoint and must include the exact model revision and artifacts.

### A/B/C continuation comparison

Creator projects expose two continuation modes without surfacing provider
internals:

- `fast` (default): send the previous clip's final five seconds to Ref2VA;
- `quality`: send the complete previous clip to Ref2VA.

Both modes append an ephemeral "continue after the final moment; do not
replay" constraint to clip 1 and later. The constraint is never persisted into
the editable storyboard prompt. An explicit start frame still wins and uses
the FL2VA path.

To compare a boundary-frame FL2VA candidate with full-reference and tail-
reference Ref2VA candidates, preserve the three source files and build one
sequential, labeled review video:

```bash
python scripts/build-continuation-comparison.py \
  /absolute/path/A.mp4 \
  /absolute/path/B.mp4 \
  /absolute/path/C.mp4 \
  /absolute/path/comparison-output
```

The command keeps byte-preserving `candidate-{A,B,C}-original.mp4` artifacts,
creates padded (never geometrically stretched) labeled segments and
`continuation-ABC-comparison.mp4`, and writes
`continuation-ABC-comparison.manifest.json` with the ffprobe metadata for every
input and output. Local `ffmpeg`/`ffprobe` are preferred; set
`STUDIO_FFMPEG`/`STUDIO_FFPROBE` to the repository wrappers when the host does
not provide the binaries. The default canvas follows candidate A; use
`--width` and `--height` together for an explicit review canvas.

Before launching a downloaded Plus checkpoint, fail closed on missing shards
or resumable ModelScope temporary files:

```bash
python scripts/verify-qwen-image-edit-checkpoint.py /models/Qwen-Image-Edit-2511
```

For Qwen-Image-Edit-2511, start the helper only after choosing a tensor
parallel size that is known to fit the target accelerator:

```bash
export TP_SIZE=... # replace with a size validated for this host
export MAX_REFERENCE_IMAGES=4
MODEL_PATH=/models/Qwen-Image-Edit-2511 scripts/serve-qwen-image-edit.sh
```

`TP_SIZE` is required deliberately; optional `ULYSSES_DEGREE`, `RING_DEGREE`,
`CFG_PARALLEL_SIZE`, `VAE_PATCH_PARALLEL_SIZE`, `VAE_USE_TILING`, and
`VAE_USE_SLICING` expose vLLM-Omni's diffusion parallel layout;
`EXPECTED_WORLD_SIZE` verifies their product. Nautilus does not assume that one
GPU topology works across CUDA and MUSA.

## Project layout

```text
src/long_video_studio/
  adapters/       provider and media integrations
  static/         current creator UI
  api.py          FastAPI routes
  compiler.py     storyboard-to-execution compiler
  domain.py       provider-neutral Film IR
  planner.py      deterministic and agent planners
  runner.py       render orchestration
docs/             architecture and provider contracts
scripts/          serving and probe helpers
tests/            unit and integration tests
```

## Roadmap

- complete Qwen-Image-Edit-2511 multi-reference validation;
- add more hosted image/video provider adapters;
- add mobile navigation, accessibility checks, and editable timeline transitions;
- authentication, tenancy, quotas, and moderation hooks;
- editable timeline transitions, music, and sidecar subtitle workflows.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Do not
publish security reports or provider credentials in issues; follow
[SECURITY.md](SECURITY.md).

The control-plane and provider boundaries are described in
[docs/architecture.md](docs/architecture.md).

Direct dependency and external-model licensing boundaries are summarized in
[THIRD_PARTY.md](THIRD_PARTY.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
