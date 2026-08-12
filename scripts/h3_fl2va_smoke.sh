#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://127.0.0.1:8091/v1/videos/sync}"
: "${FIRST_FRAME:?set FIRST_FRAME to an input image}"
OUT="${OUT:-/tmp/nautilus-fl2va-smoke.mp4}"
HEADERS="${HEADERS:-/tmp/nautilus-fl2va-smoke.headers}"
FFPROBE_BIN="${FFPROBE_BIN:-ffprobe}"

test -s "$FIRST_FRAME"

curl --fail-with-body --silent --show-error --request POST "$API_URL" \
  --dump-header "$HEADERS" \
  --write-out 'http_code=%{http_code} content_type=%{content_type} bytes=%{size_download} total_s=%{time_total}\n' \
  -F 'prompt=A clay fox turns toward the camera in a quiet miniature forest, with natural continuous motion and synchronized ambient sound.' \
  -F 'fps=24' \
  -F 'num_inference_steps=2' \
  -F 'flow_shift=12' \
  -F 'seed=0' \
  -F 'extra_params={"task":"fl2va","duration":4.0,"frame_indices":[0],"audio_flow_shift":3.0}' \
  -F "input_reference=@${FIRST_FRAME};type=image/png" \
  --output "$OUT"

test -s "$OUT"
grep -qi '^content-type: video/mp4' "$HEADERS"

"$FFPROBE_BIN" -v error \
  -show_entries 'stream=index,codec_name,codec_type,width,height,r_frame_rate,nb_frames,sample_rate,channels:format=duration,size' \
  -of json "$OUT"
