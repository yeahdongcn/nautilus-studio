#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://127.0.0.1:8092/v1/videos/sync}"
: "${REFERENCE_VIDEO:?set REFERENCE_VIDEO to an input video}"
OUT="${OUT:-/tmp/nautilus-ref2va-smoke.mp4}"
HEADERS="${HEADERS:-/tmp/nautilus-ref2va-smoke.headers}"
FFPROBE_BIN="${FFPROBE_BIN:-ffprobe}"

test -s "$REFERENCE_VIDEO"

# 1344x768 supplies enough VAE tiles for vae-patch-parallel-size=8.  The
# smaller 448x256 plumbing shape leaves some SP ranks empty on H3.
curl --fail-with-body --silent --show-error --request POST "$API_URL" \
  --dump-header "$HEADERS" \
  --write-out 'http_code=%{http_code} content_type=%{content_type} bytes=%{size_download} total_s=%{time_total}\n' \
  -F 'prompt=A realistic continuous shot that follows the same subject, identity, motion and sound as the reference video, with stable framing, temporal consistency and natural physics.' \
  -F 'width=1344' \
  -F 'height=768' \
  -F 'fps=24' \
  -F 'num_inference_steps=2' \
  -F 'flow_shift=12' \
  -F 'seed=42' \
  -F 'extra_params={"task":"ref2va","duration":4.0,"audio_flow_shift":3.0}' \
  -F "input_references=@${REFERENCE_VIDEO};type=video/mp4" \
  --output "$OUT"

test -s "$OUT"
grep -qi '^content-type: video/mp4' "$HEADERS"

"$FFPROBE_BIN" -v error \
  -show_entries 'stream=index,codec_name,codec_type,width,height,r_frame_rate,nb_frames,sample_rate,channels:format=duration,size' \
  -of json "$OUT"
