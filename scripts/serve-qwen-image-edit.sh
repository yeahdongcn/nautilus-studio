#!/usr/bin/env bash
set -euo pipefail

# The checkpoint and tensor-parallel layout are intentionally explicit.
# Different CUDA/MUSA generations may need different TP sizes.
: "${MODEL_PATH:?set MODEL_PATH to a complete Qwen-Image-Edit-2509/2511 checkpoint}"
: "${TP_SIZE:?set TP_SIZE to a tensor-parallel size validated for this host}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8093}"
ULYSSES_DEGREE="${ULYSSES_DEGREE:-1}"
RING_DEGREE="${RING_DEGREE:-1}"
CFG_PARALLEL_SIZE="${CFG_PARALLEL_SIZE:-1}"
MAX_REFERENCE_IMAGES="${MAX_REFERENCE_IMAGES:-4}"
VAE_PATCH_PARALLEL_SIZE="${VAE_PATCH_PARALLEL_SIZE:-1}"
VAE_USE_TILING="${VAE_USE_TILING:-0}"
VAE_USE_SLICING="${VAE_USE_SLICING:-0}"
VLLM_BIN="${VLLM_BIN:-vllm}"

dit_parallel_size=$((TP_SIZE * ULYSSES_DEGREE * RING_DEGREE * CFG_PARALLEL_SIZE))
if ((VAE_PATCH_PARALLEL_SIZE > dit_parallel_size)); then
  echo "VAE_PATCH_PARALLEL_SIZE must not exceed DiT parallel size (${dit_parallel_size})" >&2
  exit 2
fi
if [[ -n "${EXPECTED_WORLD_SIZE:-}" ]] && ((dit_parallel_size != EXPECTED_WORLD_SIZE)); then
  echo "parallel product ${dit_parallel_size} does not match EXPECTED_WORLD_SIZE=${EXPECTED_WORLD_SIZE}" >&2
  exit 2
fi

args=(
  --tensor-parallel-size "${TP_SIZE}"
  --ulysses-degree "${ULYSSES_DEGREE}"
  --ring-degree "${RING_DEGREE}"
  --cfg-parallel-size "${CFG_PARALLEL_SIZE}"
  --limit-mm-per-prompt "{\"image\":${MAX_REFERENCE_IMAGES}}"
  --vae-patch-parallel-size "${VAE_PATCH_PARALLEL_SIZE}"
)
if [[ "${VAE_USE_TILING}" == "1" ]]; then
  args+=(--vae-use-tiling)
fi
if [[ "${VAE_USE_SLICING}" == "1" ]]; then
  args+=(--vae-use-slicing)
fi

exec "${VLLM_BIN}" serve "${MODEL_PATH}" \
  --omni \
  --host "${HOST}" \
  --port "${PORT}" \
  "${args[@]}" \
  --trust-remote-code
