# Changelog

All notable changes to Nautilus Studio will be documented here.

## [Unreleased]

### Added

- Creator-first React/Vite workspace with fixed Workspace navigation, inline
  progress/preview, and dialog-based editing for direction, project bible,
  shots, and material metadata.
- Provider-neutral image-edit adapter with ordered multi-reference manifests,
  Qwen-Image-Edit-2509/2511 capability gates, and acceptance receipts.
- H3 FL2VA/Ref2VA orchestration with resumable project state, visual boundary
  chaining, and sidecar subtitle settings.
- Explicit H3 canvas dimensions derived from project aspect ratio, with
  no-stretch center-cropping for input frames.
- CI, Docker/Compose packaging, security policy, contribution guide, issue
  templates, and third-party license inventory.

### Known limitations

- The built-in server intentionally has no authentication, tenant isolation,
  rate limiting, or content moderation; deploy behind an authenticated proxy.
- Real MUSA Qwen-Image-Edit-2511 multi-reference acceptance is pending complete
  checkpoint download and an independent hardware run.
- Hosted provider adapters share the OpenAI-compatible multimodal contract;
  vendor-specific request/response quirks may require a small adapter.
