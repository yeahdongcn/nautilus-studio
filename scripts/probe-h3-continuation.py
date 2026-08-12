#!/usr/bin/env python3
"""Generate one Ref2VA continuation candidate for an A/B/C experiment."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from long_video_studio.adapters.h3 import H3Client
from long_video_studio.config import Settings
from long_video_studio.repository import StudioRepository

CONTINUATION_RULE = (
    "CONTINUATION RULE: Begin strictly after the final moment of the reference video. "
    "Do not replay, reenact, reset, summarize, or repeat any action, dialogue, pose, "
    "camera path, or timing already shown. Advance immediately into the new action while "
    "preserving the same characters, wardrobe, room, voice identity, room tone, and camera language."
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-id", required=True)
    result.add_argument("--shot-index", type=int, required=True)
    result.add_argument("--reference", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--endpoint", default="http://127.0.0.1:18092")
    result.add_argument("--timeout", type=float, default=1800)
    result.add_argument("--width", type=int, help="explicit output width (keeps A/B/C comparable)")
    result.add_argument("--height", type=int, help="explicit output height (keeps A/B/C comparable)")
    return result


async def run(args: argparse.Namespace) -> Path:
    settings = Settings.from_env()
    repository = StudioRepository(settings.database_path)
    project = repository.get_project(args.project_id)
    if project is None:
        raise RuntimeError(f"project not found: {args.project_id}")
    try:
        source_shot = project.shots[args.shot_index]
    except IndexError as error:
        raise RuntimeError(f"shot index out of range: {args.shot_index}") from error
    reference = args.reference.expanduser().resolve()
    if not reference.is_file():
        raise RuntimeError(f"reference video not found: {reference}")
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if (args.width is None) != (args.height is None):
        raise RuntimeError("--width and --height must be supplied together")
    if args.width is not None and (args.width < 2 or args.height < 2):
        raise RuntimeError("output dimensions must be at least 2x2")
    shot = source_shot.model_copy(update={"prompt": f"{CONTINUATION_RULE}\n\n{source_shot.prompt}"})
    client = H3Client(args.endpoint, timeout_seconds=args.timeout, flow_shift=settings.h3_flow_shift)
    print(f"project={project.id}", flush=True)
    print(f"shot={shot.index} duration={shot.duration_seconds} steps={shot.inference_steps}", flush=True)
    print(f"reference={reference}", flush=True)
    print(f"output={output}", flush=True)
    return await client.generate_ref2va(
        shot,
        reference,
        reference,
        output,
        width=args.width,
        height=args.height,
        async_job=True,
    )


def main() -> int:
    args = parser().parse_args()
    output = asyncio.run(run(args))
    print(f"completed={output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
