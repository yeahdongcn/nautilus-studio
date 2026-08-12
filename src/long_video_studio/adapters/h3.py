from __future__ import annotations

import asyncio
import json
import os
import time
from base64 import b64encode
from pathlib import Path

import httpx

from long_video_studio.domain import ShotSpec


class H3Client:
    def __init__(
        self,
        endpoint: str,
        timeout_seconds: float = 1800,
        flow_shift: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.endpoint = self._normalize_endpoint(endpoint)
        self.timeout_seconds = timeout_seconds
        self.timeout = httpx.Timeout(timeout_seconds, connect=30)
        self.flow_shift = flow_shift
        self.transport = transport

    @staticmethod
    def _normalize_endpoint(endpoint: str) -> str:
        value = endpoint.rstrip("/")
        if not value.endswith("/v1/videos/sync"):
            value += "/v1/videos/sync"
        return value

    async def health(self) -> bool:
        base_url = self.endpoint.removesuffix("/v1/videos/sync")
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                response = await client.get(base_url + "/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def generate_fl2va(
        self,
        shot: ShotSpec,
        start_frame: Path,
        output_path: Path,
        *,
        width: int | None = None,
        height: int | None = None,
        async_job: bool = False,
    ) -> Path:
        extra_params = {
            "task": "fl2va",
            "duration": shot.duration_seconds,
            "frame_indices": [0],
            "audio_flow_shift": 3.0,
        }
        data = self._common_data(shot, extra_params, width=width, height=height)
        post = self._post_async_job if async_job else self._post
        return await post(
            data,
            files={
                "input_reference": (
                    start_frame.name,
                    start_frame,
                    self._media_type(start_frame),
                )
            },
            output_path=output_path,
        )

    async def generate_ref2va(
        self,
        shot: ShotSpec,
        reference_image: Path,
        reference_media: Path,
        output_path: Path,
        *,
        width: int | None = None,
        height: int | None = None,
        async_job: bool = False,
    ) -> Path:
        extra_params = {
            "task": "ref2va",
            "duration": shot.duration_seconds,
            "ref_images_pixels_shape": [[-1, -1]],
            "audio_flow_shift": 3.0,
        }
        data = self._common_data(shot, extra_params, width=width, height=height)
        is_audio = reference_media.suffix.lower() in {
            ".wav",
            ".mp3",
            ".m4a",
            ".flac",
            ".aac",
        }
        if is_audio:
            media_type = self._media_type(reference_media)
            encoded = b64encode(reference_media.read_bytes()).decode("ascii")
            data["audio_reference"] = json.dumps({"audio_url": f"data:{media_type};base64,{encoded}"})
            files = {
                "input_reference": (
                    reference_image.name,
                    reference_image,
                    self._media_type(reference_image),
                )
            }
        else:
            # Video Ref2VA carries its own visual and audio conditioning.  The
            # current H3 API consumes it through the plural reference field.
            files = {
                "input_references": (
                    reference_media.name,
                    reference_media,
                    self._media_type(reference_media),
                )
            }
        post = self._post_async_job if async_job else self._post
        return await post(
            data,
            files=files,
            output_path=output_path,
        )

    def _common_data(
        self,
        shot: ShotSpec,
        extra_params: dict[str, object],
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, str]:
        prompt = shot.prompt
        prompt += "\n\nDo not render burned-in subtitles, captions, karaoke text, UI overlays, watermarks, or logos."
        if shot.negative_prompt:
            prompt += f" Constraints: {shot.negative_prompt}."
        data = {
            "prompt": prompt,
            "fps": str(shot.fps),
            "num_inference_steps": str(shot.inference_steps),
            "seed": str(shot.seed),
            "flow_shift": str(self.flow_shift),
            "extra_params": json.dumps(extra_params, ensure_ascii=False),
        }
        # Width/height are top-level VideoGenerationRequest fields. Putting
        # them only inside extra_params is silently ignored by vLLM-Omni's
        # sampling resolver, which then falls back to its 16:9 default.
        if width is not None and height is not None:
            data.update({"width": str(int(width)), "height": str(int(height))})
        return data

    async def _post(
        self,
        data: dict[str, str],
        files: dict[str, tuple[str, Path, str]],
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        handles = []
        multipart: dict[str, tuple[str, object, str]] = {}
        try:
            for field, (filename, path, media_type) in files.items():
                handle = path.open("rb")
                handles.append(handle)
                multipart[field] = (filename, handle, media_type)
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(self.endpoint, data=data, files=multipart)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "video" not in content_type and not response.content.startswith(b"\x00\x00"):
                raise RuntimeError(
                    f"H3 endpoint returned unexpected content type {content_type}: {response.text[:500]}"
                )
            temporary = output_path.with_suffix(output_path.suffix + ".tmp")
            temporary.write_bytes(response.content)
            os.replace(temporary, output_path)
            return output_path
        finally:
            for handle in handles:
                handle.close()

    async def _post_async_job(
        self,
        data: dict[str, str],
        files: dict[str, tuple[str, Path, str]],
        output_path: Path,
    ) -> Path:
        """Submit a durable video job, poll it, then download the result."""

        output_path.parent.mkdir(parents=True, exist_ok=True)
        handles = []
        multipart: dict[str, tuple[str, object, str]] = {}
        base_url = self.endpoint.removesuffix("/sync")
        try:
            for field, (filename, path, media_type) in files.items():
                handle = path.open("rb")
                handles.append(handle)
                multipart[field] = (filename, handle, media_type)
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(base_url, data=data, files=multipart)
                response.raise_for_status()
                payload = response.json()
                job_id = payload.get("id")
                if not job_id:
                    raise RuntimeError(f"H3 async endpoint returned no job id: {payload}")
                deadline = time.monotonic() + self.timeout_seconds
                status_url = f"{base_url}/{job_id}"
                resolved_job_id = False
                while True:
                    status_response = await client.get(status_url)
                    if status_response.status_code == 404 and not resolved_job_id:
                        # Some vLLM-Omni builds return a submission id that
                        # differs from the id stored in VIDEO_JOBS. Resolve the
                        # newest matching prompt once, then continue polling.
                        jobs_response = await client.get(base_url)
                        jobs_response.raise_for_status()
                        candidates = [
                            job
                            for job in jobs_response.json().get("data", [])
                            if job.get("prompt") == data.get("prompt")
                        ]
                        if candidates:
                            job_id = max(candidates, key=lambda job: job.get("created_at") or 0)["id"]
                            status_url = f"{base_url}/{job_id}"
                            resolved_job_id = True
                            continue
                    if status_response.is_error:
                        # vLLM-Omni serializes a failed async job as HTTP 500
                        # while still returning the durable job payload. Keep
                        # the model error instead of exposing only an opaque
                        # httpx status exception to the render job.
                        try:
                            failed_payload = status_response.json()
                        except ValueError:
                            failed_payload = {}
                        if failed_payload.get("status") == "failed":
                            error = failed_payload.get("error") or failed_payload
                            raise RuntimeError(f"H3 async job {job_id} failed: {error}")
                    status_response.raise_for_status()
                    status_payload = status_response.json()
                    status = status_payload.get("status")
                    if status == "completed":
                        content_response = await client.get(f"{status_url}/content")
                        content_response.raise_for_status()
                        break
                    if status == "failed":
                        raise RuntimeError(
                            f"H3 async job {job_id} failed: {status_payload.get('error') or status_payload}"
                        )
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"H3 async job {job_id} exceeded {self.timeout_seconds}s")
                    await asyncio.sleep(2)
            temporary = output_path.with_suffix(output_path.suffix + ".tmp")
            temporary.write_bytes(content_response.content)
            os.replace(temporary, output_path)
            return output_path
        finally:
            for handle in handles:
                handle.close()

    @staticmethod
    def _media_type(path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".png": "image/png",
            ".webp": "image/webp",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
        }.get(suffix, "application/octet-stream")
