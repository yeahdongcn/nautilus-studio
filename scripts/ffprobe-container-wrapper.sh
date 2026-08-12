#!/usr/bin/env bash
set -euo pipefail

: "${STUDIO_FFMPEG_IMAGE:?set STUDIO_FFMPEG_IMAGE to an image containing ffmpeg and ffprobe}"
image="$STUDIO_FFMPEG_IMAGE"
exec docker run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "${HOME}:${HOME}" \
  --volume /tmp:/tmp \
  --entrypoint ffprobe "$image" "$@"
