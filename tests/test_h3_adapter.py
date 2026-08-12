from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from long_video_studio.adapters.h3 import H3Client
from long_video_studio.domain import ShotSpec, ShotTask


def test_fl2va_adapter_uses_current_video_api(tmp_path: Path):
    request_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_body
        request_body = request.read()
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"\x00\x00mp4")

    image = tmp_path / "start.png"
    image.write_bytes(b"not-a-real-image-but-valid-for-multipart")
    output = tmp_path / "shot.mp4"
    shot = ShotSpec(
        index=0,
        title="Opening",
        purpose="Open the film",
        duration_seconds=10,
        prompt="A cinematic opening.",
        inference_steps=12,
    )
    client = H3Client("http://h3.example:8091", transport=httpx.MockTransport(handler))
    result = asyncio.run(
        client.generate_fl2va(
            shot,
            image,
            output,
            width=1280,
            height=704,
        )
    )
    assert result.read_bytes() == b"\x00\x00mp4"
    assert b'name="input_reference"' in request_body
    assert b'name="image"' not in request_body
    assert b'name="extra_params"' in request_body
    assert b'"task": "fl2va"' in request_body
    assert b'name="width"' in request_body
    assert b'name="height"' in request_body
    assert b"\r\n\r\n1280\r\n" in request_body
    assert b"\r\n\r\n704\r\n" in request_body
    assert b"CONTINUITY FROM THE PREVIOUS SHOT" not in request_body
    assert b"red umbrella" not in request_body
    assert b"burned-in subtitles" in request_body
    assert client.endpoint == "http://h3.example:8091/v1/videos/sync"


def test_ref2va_video_adapter_uses_plural_reference_field(tmp_path: Path):
    request_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_body
        request_body = request.read()
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"\x00\x00mp4")

    image = tmp_path / "identity.png"
    image.write_bytes(b"image")
    video = tmp_path / "motion.mp4"
    video.write_bytes(b"video")
    output = tmp_path / "shot.mp4"
    shot = ShotSpec(
        index=0,
        title="Reference",
        purpose="Follow a reference",
        duration_seconds=4,
        prompt="Follow the reference motion.",
    )
    client = H3Client("http://h3.example:8092", transport=httpx.MockTransport(handler))

    asyncio.run(client.generate_ref2va(shot, image, video, output, width=704, height=1280))

    assert b'name="input_references"' in request_body
    assert b'name="image"' not in request_body
    assert b'name="video"' not in request_body
    assert b'"task": "ref2va"' in request_body
    assert b'name="width"' in request_body
    assert b'name="height"' in request_body
    assert b"\r\n\r\n704\r\n" in request_body
    assert b"\r\n\r\n1280\r\n" in request_body


def test_ref2va_audio_adapter_uses_image_and_data_url(tmp_path: Path):
    request_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_body
        request_body = request.read()
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"\x00\x00mp4")

    image = tmp_path / "identity.png"
    image.write_bytes(b"image")
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audio")
    output = tmp_path / "shot.mp4"
    shot = ShotSpec(
        index=0,
        title="Reference",
        purpose="Follow audio",
        duration_seconds=4,
        prompt="Lip sync to the voice.",
    )
    client = H3Client("http://h3.example:8092", transport=httpx.MockTransport(handler))

    asyncio.run(client.generate_ref2va(shot, image, audio, output))

    assert b'name="input_reference"' in request_body
    assert b'name="audio_reference"' in request_body
    assert b"data:audio/mpeg;base64,YXVkaW8=" in request_body


def test_async_video_job_is_polled_and_downloaded(tmp_path: Path):
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"id": "submission-id", "status": "queued"})
        if request.url.path == "/v1/videos/submission-id":
            return httpx.Response(404, json={"detail": "not found"})
        if request.url.path == "/v1/videos":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "video-job-1",
                            "prompt": (
                                "Continue after the reference.\n\nDo not render burned-in subtitles, "
                                "captions, karaoke text, UI overlays, watermarks, or logos."
                            ),
                            "created_at": 1,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"\x00\x00async-video", headers={"content-type": "video/mp4"})
        return httpx.Response(200, json={"id": "video-job-1", "status": "completed"})

    image = tmp_path / "reference.png"
    video = tmp_path / "reference.mp4"
    output = tmp_path / "async.mp4"
    image.write_bytes(b"image")
    video.write_bytes(b"video")
    shot = ShotSpec(
        index=0,
        title="Continue",
        purpose="Advance the scene",
        prompt="Continue after the reference.",
        duration_seconds=4,
        task=ShotTask.REF2VA,
    )
    client = H3Client("http://h3.test:8092", transport=httpx.MockTransport(handler))

    asyncio.run(client.generate_ref2va(shot, image, video, output, async_job=True))

    assert output.read_bytes() == b"\x00\x00async-video"
    assert seen == [
        ("POST", "/v1/videos"),
        ("GET", "/v1/videos/submission-id"),
        ("GET", "/v1/videos"),
        ("GET", "/v1/videos/video-job-1"),
        ("GET", "/v1/videos/video-job-1/content"),
    ]


def test_async_failed_job_preserves_server_error_payload(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job-id", "status": "queued"})
        return httpx.Response(
            500,
            json={
                "id": "job-id",
                "status": "failed",
                "error": {"code": 500, "message": "MUSA out of memory"},
            },
        )

    image = tmp_path / "reference.png"
    video = tmp_path / "reference.mp4"
    output = tmp_path / "failed.mp4"
    image.write_bytes(b"image")
    video.write_bytes(b"video")
    shot = ShotSpec(
        index=0,
        title="Continue",
        purpose="Advance the scene",
        duration_seconds=4,
        prompt="Continue after the reference.",
        task=ShotTask.REF2VA,
    )
    client = H3Client("http://h3.test:8092", transport=httpx.MockTransport(handler))

    try:
        asyncio.run(client.generate_ref2va(shot, image, video, output, async_job=True))
    except RuntimeError as error:
        assert "MUSA out of memory" in str(error)
    else:  # pragma: no cover - the assertion is the test's failure branch
        raise AssertionError("failed async job did not raise")
