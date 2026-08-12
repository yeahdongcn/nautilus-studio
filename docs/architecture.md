# Architecture

Nautilus Studio is a creator-facing control plane. Model topology remains
behind a small set of provider contracts; creators work with stories,
materials, shots, and finished videos rather than node graphs.

```text
brief + material library
          |
          v
 planner / storyboard agent
          |
          v
 project bible + editable shots
          |
          +--> explicit start frame --------------------+
          |                                             |
          +--> ordered references --> image edit anchor |
                                                        v
                                             FL2VA / Ref2VA provider
                                                        |
                                                        v
                                          boundary extraction + assembly
                                                        |
                                                        v
                                             preview + sidecar metadata
```

## State

SQLite stores assets, projects, shots, and render jobs. Original material and
generated media live under the configured data directory. Provider jobs are
submitted through durable vLLM-Omni video endpoints and polled by the runner;
the local job records creator-visible progress and output paths.

## Provider boundaries

- Planner: Responses-compatible structured generation with a deterministic
  fallback.
- Image edit: ordered multimodal chat request returning an image. Local
  vLLM-Omni and hosted OpenAI-compatible endpoints share this contract.
- Video: MiniMax-H3 FL2VA and Ref2VA adapters. The interface keeps room for
  other vLLM-Omni, SGLang, or hosted video backends.
- Media: local ffmpeg/ffprobe assembly and Pillow-based no-stretch image fit.

## Continuity policy

An explicit creator start frame always wins. Without one, the runtime may
compose an anchor from project context and ordered references. Continuous
shots use Ref2VA when configured: `fast` passes the previous clip's final five
seconds, while `quality` passes the complete previous clip. The runtime adds a
non-persistent anti-replay instruction only to these continuation requests, so
storyboard prompts remain editable and retries cannot accumulate duplicate
constraints. When Ref2VA is unavailable, the previous boundary frame and
FL2VA remain an internal compatibility fallback. Real scene cuts can request
a new anchor.

## Security boundary

The built-in API is a single-user development service. Authentication,
authorization, tenant isolation, rate limiting, TLS, and content policy belong
in a trusted reverse proxy or hosting layer. Provider credentials are read from
environment variables or local operator configuration and must never be stored
with project media.
