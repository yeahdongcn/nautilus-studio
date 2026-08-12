# Qwen-Image-Edit-2511 acceptance record

This is a fill-in evidence template for a real MUSA run. Keep private media
and credentials outside Git; store only sanitized receipts and hashes.

## Environment

- Date/time:
- Host / lease handle:
- GPU model/count:
- Driver / runtime / SDK:
- Container image + digest:
- Model path and revision:
- vLLM-Omni revision:
- Tensor parallel size:
- Server command:
- Provider base URL (redacted if private):

## Contract smoke

- `/health`:
- `/v1/chat/completions` response shape:
- Generated image MIME type:
- Generated image dimensions:
- Seed / steps / guidance:

## Visual cases

| Case | Ordered references | Output receipt | Dimensions | Human review |
| --- | --- | --- | --- | --- |
| Scene + one character |  |  |  |  |
| Scene + two characters |  |  |  |  |
| Scene + two characters + prop |  |  |  |  |

Review each output for identity/wardrobe retention, correct placement and
scale, aspect-ratio preservation without stretching, temporal-anchor
suitability, and absence of unintended text/logo/watermark.

## Nautilus integration smoke

- Provider configured in Studio:
- Anchor mode:
- Project ID / job ID:
- Anchor files generated:
- FL2VA request succeeded:
- Final video preview checked:
- Existing Ref2VA/FL2VA regression checks:

## Result

- [ ] Functional pass
- [ ] Human visual pass
- [ ] Safe to expose through the creator UI
- Open issues / follow-ups:
