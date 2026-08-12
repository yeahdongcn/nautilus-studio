#!/usr/bin/env bash
set -euo pipefail

# Optional CPU-only media-tool fallback for hosts that do not install ffmpeg.
# The image is deliberately operator-supplied: public releases must not depend
# on a private registry or silently pull an unreviewed image.
: "${STUDIO_FFMPEG_IMAGE:?set STUDIO_FFMPEG_IMAGE to an image containing ffmpeg and ffprobe}"
image="$STUDIO_FFMPEG_IMAGE"
exec docker run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --volume "${HOME}:${HOME}" \
  --volume /tmp:/tmp \
  --entrypoint ffmpeg "$image" "$@"
