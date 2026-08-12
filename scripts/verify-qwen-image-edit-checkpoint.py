#!/usr/bin/env python3
"""Fail-closed structural verification for Qwen Image Edit checkpoints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("model_dir", type=Path)
    return result


def read_json(path: Path, errors: list[str]) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read {path.name}: {error}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.name} is not a JSON object")
        return {}
    return value


def indexed_shards(root: Path, relative_index: str, errors: list[str]) -> set[Path]:
    index_path = root / relative_index
    index = read_json(index_path, errors)
    weight_map = index.get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        errors.append(f"{relative_index} has no weight_map")
        return set()
    parent = index_path.parent
    return {parent / str(filename) for filename in weight_map.values()}


def verify(root: Path) -> dict[str, object]:
    root = root.expanduser().resolve()
    errors: list[str] = []
    if not root.is_dir():
        errors.append(f"checkpoint directory does not exist: {root}")
        return {"ok": False, "model_dir": str(root), "errors": errors}

    model_index = read_json(root / "model_index.json", errors)
    pipeline = model_index.get("_class_name")
    if pipeline != "QwenImageEditPlusPipeline":
        errors.append(f"expected QwenImageEditPlusPipeline, got {pipeline!r}")

    required = {
        root / "processor" / "preprocessor_config.json",
        root / "text_encoder" / "config.json",
        root / "transformer" / "config.json",
        root / "vae" / "config.json",
        root / "vae" / "diffusion_pytorch_model.safetensors",
    }
    required |= indexed_shards(root, "text_encoder/model.safetensors.index.json", errors)
    required |= indexed_shards(root, "transformer/diffusion_pytorch_model.safetensors.index.json", errors)

    missing = sorted(str(path.relative_to(root)) for path in required if not path.is_file() or path.stat().st_size == 0)
    if missing:
        errors.append(f"missing or empty files: {', '.join(missing)}")

    temporary_root = root / "._____temp"
    partials = (
        sorted(str(path.relative_to(root)) for path in temporary_root.rglob("*") if path.is_file())
        if temporary_root.is_dir()
        else []
    )
    if partials:
        errors.append(f"partial download files remain: {len(partials)}")

    files = [path for path in root.rglob("*") if path.is_file() and "._____temp" not in path.parts]
    return {
        "ok": not errors,
        "model_dir": str(root),
        "pipeline": pipeline,
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "required_files": len(required),
        "partial_files": partials,
        "errors": errors,
    }


def main() -> int:
    args = parser().parse_args()
    result = verify(args.model_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
