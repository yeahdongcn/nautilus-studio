

# Nautilus Studio

[![CI](https://github.com/yeahdongcn/nautilus-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/yeahdongcn/nautilus-studio/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Nautilus Studio is a creator-first, agentic AI film workshop. A creator gives
it an idea and optional reference media; Nautilus turns that into a story,
editable storyboard, continuous video clips, and a final long-form cut.

> Project status: experimental alpha. The local server has no built-in
> authentication and should be deployed only on localhost or behind an
> authenticated reverse proxy.

![Nautilus Studio creator workspace](assets/screenshot.webp)

## Why Nautilus

Most diffusion and omni models generate short clips. Nautilus makes long-form
creation practical by keeping the technical graph behind a creator-oriented
workflow:

- story and storyboard planning from one prompt;
- a persistent material library with character, location, prop, style,
  start-frame, and audio roles;
- FL2VA and Ref2VA video adapters;
- previous-boundary-to-next-anchor continuity;
- optional story-aware anchor frames built by Image Edit or zero-material T2I;
- provider switching between self-hosted vLLM-Omni and hosted APIs;
- live per-service readiness, request activity, and optional per-GPU telemetry;
- resumable and deletable projects, concurrent background planning/rendering,
  history-calibrated ETAs, inline preview, and sidecar subtitles.

## Architecture

```text
Creator brief + material library
              |
          Film planner
              |
     World bible + storyboard
              |
 Image Edit (with references)
     or T2I (zero material)
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
npm --prefix web ci
npm --prefix web run build
cp .env.example .env
set -a && . ./.env && set +a
nautilus-studio --host 127.0.0.1 --port 7860
```

Open `http://127.0.0.1:7860`. Without external endpoints, the deterministic
planner, project editor, material library, and compiler remain available.

### MCP endpoint

The same Studio process exposes a Streamable HTTP MCP endpoint at
`http://127.0.0.1:7860/mcp/`. It provides creator-facing tools for listing
assets/projects, planning a storyboard, checking render state, and starting a
render; it does not expose shell or arbitrary filesystem operations. Set
`STUDIO_MCP_TOKEN` when the service is reachable by other users, then configure
Codex with the endpoint and a Bearer token. Set `STUDIO_MCP_ENABLED=false` to
disable MCP without changing the Studio API.

Every running Studio also publishes an agent-facing discovery document at
`/llms.txt`. It describes the instance's MCP tools and resources, Codex and
Claude Code setup, recommended project workflow, model-service topology,
operational safeguards, and REST/OpenAPI fallbacks using the host that served
the request:

```bash
curl http://127.0.0.1:7860/llms.txt
```

```bash
# Local Studio without a token
codex mcp add nautilus-studio --url http://127.0.0.1:7860/mcp/

# Token-protected Studio
export NAUTILUS_STUDIO_MCP_TOKEN='...'
codex mcp add nautilus-studio \
  --url http://127.0.0.1:7860/mcp/ \
  --bearer-token-env-var NAUTILUS_STUDIO_MCP_TOKEN

codex mcp list
```

Claude Code can connect to the same endpoint:

```bash
claude mcp add --transport http nautilus-studio http://127.0.0.1:7860/mcp/
claude mcp get nautilus-studio
```

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

The creator workspace lives in `web/` and is the only supported UI. Build it
before starting a source checkout and point `STUDIO_WEB_ROOT` at `web/dist`.
The server fails fast when the React bundle is missing instead of silently
falling back to a stale interface:

```bash
npm --prefix web ci
npm --prefix web run build
export STUDIO_WEB_ROOT="$PWD/web/dist"
nautilus-studio --host 0.0.0.0 --port 7860
```

For frontend-only development, `npm --prefix web run dev -- --host 0.0.0.0
--port 5173` proxies `/api` to a local Studio on port `7860` without changing
the backend API contract.

The direction, project bible, shot cards, and material metadata are edited in
focused dialogs. The storyboard stays compact while full prompts and ordered
references remain available when a creator opens a shot.

The creator's material selection is an authorization boundary. Only asset IDs
explicitly selected for the project are sent to the planner or downstream
model providers. An empty selection means that no material-library asset is
retrieved, inserted into prompts, or attached to generated shots. A future
automatic-retrieval mode must remain an explicit creator choice.

Explicit creator start frames always win. If no start frame is selected and an
image-edit provider is configured, Nautilus composes the opening anchor from
the project context and ordered scene/character/prop references instead. The
planner still writes and exposes that opening-frame prompt when Image Edit is
offline, so creators can review or edit the storyboard before enabling render
services; ordinary reference images are never silently promoted to a start
frame.

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

By default, planning uses three focused calls: a creative director builds the
World Bible and shot spine, independent shot directors write detailed H3
timelines, and a continuity critic repairs adjacent-boundary drift. Configure
the global provider-call cap, transient retry policy, and optional local
MiniMax-H3 style packs with:

```bash
export STUDIO_PLANNER_PIPELINE=hierarchical
export STUDIO_PLANNER_RETRY_ATTEMPTS=3
export STUDIO_PLANNER_RETRY_BACKOFF_SECONDS=2
export STUDIO_PLANNER_SHOT_CONCURRENCY=3
export STUDIO_PLANNER_PROJECT_CONCURRENCY=3
export STUDIO_H3_SKILLS_DIR=/path/to/long-video-studio/skills
```

The selected skill pack is applied by style; all packs are not concatenated
into one prompt. `single_pass` remains available for a provider that cannot
handle multiple requests.

### MiniMax-H3 video

FL2VA and Ref2VA are separate model partitions and may be served independently:

```bash
export STUDIO_H3_FL2VA_URL=http://127.0.0.1:8091
export STUDIO_H3_REF2VA_URL=http://127.0.0.1:8092
export STUDIO_H3_QUALITY=lossless
```

`lossless` is the default and keeps the native H3 reference-conditioning path.
`high` is an explicit opt-in for Cache-DiT deployments; it can improve
throughput but may lower reference fidelity and should not be used as the
correctness baseline. The validated MUSA baseline is 50 steps with
`flow_shift=12` and `audio_flow_shift=3`. The internal H3 conditioning noise
strength is intentionally not exposed as a serving knob yet; changing it
would trade continuity for artifact suppression and needs a separate accuracy
matrix.

Nautilus submits durable jobs to the vLLM-Omni `/v1/videos` endpoint and polls
their status, so long 50-step clips are not held behind a short HTTP request
timeout. The adapter still supports `/v1/videos/sync` for focused smoke tests.
For vLLM-Omni servers, set `VLLM_OMNI_VIDEO_SYNC_TIMEOUT` high enough when using
the synchronous endpoint.

For the exact MUSA image, validated Ref2VA parameter matrix, failure modes, and
three-service Docker commands, see [MiniMax-H3 on MUSA](docs/minimax-h3-musa-deployment.md).

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

The tokenizer is normally inside the vLLM-Omni image-edit container; the
Studio host does not need a second checkpoint download. `STUDIO_IMAGE_EDIT_TOKENIZER_PATH`
is optional and only enables an exact local Qwen-token preflight. If it is set,
it must point to a host-visible directory containing `tokenizer.json` (not a
path that exists only inside the remote serving container). Nautilus always
keeps the 1000-character cap and lets the remote provider enforce its exact
token limit when no local tokenizer is available.

Use `openai-compatible` instead of `vllm-omni` for a hosted provider. See
[Image-edit providers](docs/image-edit-providers.md) for the request contract,
reference manifest, and acceptance matrix.

### Text to Image

T2I is a separate opening-frame route for projects with no selected image
material. It never reads from the material library implicitly and does not
silently fall back to Image Edit.

```bash
export STUDIO_T2I_PROVIDER=vllm-omni
export STUDIO_T2I_BASE_URL=http://127.0.0.1:8094
# Optional for single-model servers; omit to avoid a served-model-name mismatch.
# export STUDIO_T2I_MODEL=Qwen/Qwen-Image-2512
export STUDIO_T2I_STEPS=50
export STUDIO_T2I_TRUE_CFG_SCALE=4.0
export STUDIO_T2I_GUIDANCE_SCALE=1.0
# The vLLM-Omni image endpoint is synchronous; allow long MUSA generations.
# Connection setup remains bounded separately by the adapter.
export STUDIO_T2I_TIMEOUT_SECONDS=7200
```

Studio calls the OpenAI-compatible `/v1/images/generations` endpoint. The
model field is optional for a single-model vLLM-Omni server.

## Service and GPU status

The header status control reports Planner, FL2VA, Ref2VA, Image Edit, and T2I
independently. For vLLM-Omni deployments, Nautilus reads `/health` and the
stable request counters from `/metrics`; a failed metrics endpoint never turns
a successful health check into an outage.

GPU telemetry is optional and provider-neutral. The Studio process never runs
SSH, `mthreads-gmi`, `nvidia-smi`, or lease commands. Instead, an
operator-owned collector writes one local JSON snapshot atomically and Studio
only validates and displays it:

```bash
export STUDIO_GPU_SNAPSHOT_PATH=/var/run/nautilus/gpu-service-snapshot.json
export STUDIO_GPU_SNAPSHOT_MAX_AGE_SECONDS=20
```

The versioned snapshot contract maps physical or container-visible devices to
Studio service IDs:

```json
{
  "schema_version": 1,
  "kind": "gpu_service_snapshot",
  "captured_at": "2026-08-15T08:30:00Z",
  "devices": [
    {
      "service_id": "fl2va",
      "node": "video-node",
      "index": 0,
      "name": "accelerator",
      "utilization_percent": 87,
      "memory_used_mib": 64000,
      "memory_total_mib": 81920,
      "temperature_c": 61
    }
  ]
}
```

Supported service IDs are `fl2va`, `ref2va`, `image_edit`, and `t2i`.
Unknown fields are ignored for forward compatibility; invalid ranges,
timezone-less timestamps, oversized files, and unsupported schema versions
fail closed. A stale or unavailable snapshot is displayed as unknown rather
than as `0%`. Telemetry is observational only: scheduling, lease ownership,
and process cleanup remain external operator responsibilities.

## Configuration

All configuration uses `STUDIO_` environment variables. Start from
[`.env.example`](.env.example). Important paths:

- `STUDIO_DATA_DIR`: SQLite, imported assets, and render outputs;
- `STUDIO_IMPORT_ROOTS`: colon-separated server paths allowed for asset import;
- `STUDIO_COPY_IMPORTED_ASSETS`: copy imported files into studio storage;
- `STUDIO_FFMPEG` and `STUDIO_FFPROBE`: media binaries or wrappers.

Rendering is scheduled independently per project. Set
`STUDIO_RENDER_MAX_CONCURRENCY` to the number of project pipelines the deployed
video services can sustain. ETA observations are isolated by
`STUDIO_RENDER_PROFILE`; use a new profile name after changing the model,
precision, hardware, or parallel topology. Successful shot timings remain in
the calibration table even if their project is later deleted.

API keys are never required in project data and should only come from the
environment or a deployment secret store.

## Development

```bash
make check
```

Or run the checks individually:

```bash
ruff format --check src tests scripts/build-continuation-comparison.py scripts/probe-image-edit.py scripts/probe-h3-continuation.py scripts/verify-qwen-image-edit-checkpoint.py
ruff check src tests scripts/build-continuation-comparison.py scripts/probe-image-edit.py scripts/probe-h3-continuation.py scripts/verify-qwen-image-edit-checkpoint.py
pytest -q
cd web && npm ci && npm run format && npm run build && npm audit --audit-level=high
```

The test suite uses mock provider transports. Hardware claims require a real
model endpoint and must include the exact model revision and artifacts.

### A/B/C continuation comparison

Creator projects expose three continuation modes without surfacing provider
internals:

- `ultra_fast`: generate a storyboard-specific opening frame for every FL2VA
  shot. Shot 1 uses selected materials through Image Edit, or T2I with no
  materials; later shots use the previous final frame as Image Edit reference
  1, followed by selected creator images. The editor then joins shots with a
  fixed or deterministic-random transition. A legacy direct boundary-frame
  strategy remains selectable;
- `fast` (legacy API compatibility default; the UI defaults to `quality`):
  send the previous clip's final five seconds to Ref2VA;
- `quality`: send the complete previous clip to Ref2VA.

The two Ref2VA modes append an ephemeral "continue after the final moment; do
not replay" constraint to clip 2 and later. The constraint is never persisted
into the editable storyboard prompt. An explicit start frame still wins and
uses the FL2VA path.

Ultra-fast therefore needs only Qwen Image, Qwen Image Edit, and MiniMax-H3
FL2VA. Its default `fade_black` edit can be changed to `dissolve`, `hard_cut`,
or deterministic `random`; the default transition duration is 0.6 seconds.

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
  api.py          FastAPI routes
  compiler.py     storyboard-to-execution compiler
  domain.py       provider-neutral Film IR
  planner.py      deterministic and agent planners
  planning.py     concurrent background project planning
  estimator.py    profile-aware render ETA calibration
  runner.py       render orchestration
  service_status.py service health, activity, and GPU snapshot projection
web/              React creator UI and Vite build
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
