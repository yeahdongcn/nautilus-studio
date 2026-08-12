#!/usr/bin/env python3
"""Probe a local or hosted multi-image provider with ordered references."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from PIL import Image

from long_video_studio.adapters.image_edit import (
    ImageEditReference,
    ImageEditRequest,
    OpenAICompatibleImageEditProvider,
)


def parse_reference(value: str) -> ImageEditReference:
    role, separator, path_value = value.partition("=")
    if not separator or not role or not path_value:
        raise argparse.ArgumentTypeError("reference must be ROLE=/path/to/image")
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"reference does not exist: {path}")
    return ImageEditReference(path=path, label=path.stem, role=role)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="Qwen/Qwen-Image-Edit-2511")
    parser.add_argument("--reference", action="append", type=parse_reference, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--guidance-scale", type=float, default=1.0)
    parser.add_argument("--true-cfg-scale", type=float, default=4.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--negative-prompt")
    parser.add_argument("--max-references", type=int, default=4)
    parser.add_argument("--receipt", type=Path, help="write a key-free JSON acceptance receipt")
    return parser


async def run(args: argparse.Namespace) -> None:
    provider = OpenAICompatibleImageEditProvider(
        args.base_url,
        args.model,
        api_key=os.getenv("STUDIO_IMAGE_EDIT_API_KEY"),
        max_references=args.max_references,
    )
    path = await provider.edit(
        ImageEditRequest(
            prompt=args.prompt,
            references=tuple(args.reference),
            output_path=args.output.resolve(),
            width=args.width,
            height=args.height,
            negative_prompt=args.negative_prompt,
            extra_body={
                "num_inference_steps": args.steps,
                "guidance_scale": args.guidance_scale,
                "seed": args.seed,
                "true_cfg_scale": args.true_cfg_scale,
            },
        )
    )
    with Image.open(path) as image:
        actual_size = list(image.size)
        image_format = image.format
    receipt = {
        "model": args.model,
        "provider_protocol": provider.capabilities.protocol,
        "reference_count": len(args.reference),
        "references": [
            {
                "role": reference.role,
                "name": reference.path.name,
                "sha256": hashlib.sha256(reference.path.read_bytes()).hexdigest(),
            }
            for reference in args.reference
        ],
        "prompt": args.prompt,
        "negative_prompt": args.negative_prompt,
        "requested_size": [args.width, args.height],
        "actual_size": actual_size,
        "steps": args.steps,
        "guidance_scale": args.guidance_scale,
        "true_cfg_scale": args.true_cfg_scale,
        "seed": args.seed,
        "output": str(path),
        "output_format": image_format,
        "output_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    if args.receipt:
        destination = args.receipt.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
