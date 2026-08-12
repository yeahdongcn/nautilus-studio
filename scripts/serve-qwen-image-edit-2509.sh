#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="${MODEL_PATH:?Set MODEL_PATH to the local Qwen-Image-Edit-2509 checkpoint directory}"
export MODEL_PATH
exec "$(dirname "$0")/serve-qwen-image-edit.sh"
