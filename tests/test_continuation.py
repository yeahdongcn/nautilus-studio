from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from test_assets import png_bytes

from long_video_studio.adapters.h3 import H3Client
from long_video_studio.assets import AssetService
from long_video_studio.domain import (
    AssetKind,
    AssetRecord,
    ContinuationMode,
    FilmProject,
    ProjectBrief,
    RenderJob,
    ShotSpec,
    ShotStatus,
    ShotTask,
    WorldBible,
    effective_video_task,
)
from long_video_studio.repository import StudioRepository
from long_video_studio.runner import RenderManager


def _shot(index: int, **updates) -> ShotSpec:
    values = {
        "index": index,
        "title": f"Shot {index + 1}",
        "purpose": "Continue the story",
        "duration_seconds": 5,
        "task": ShotTask.FL2VA,
        "prompt": f"Perform the new action for shot {index + 1}.",
    }
    values.update(updates)
    return ShotSpec(**values)


def test_continuation_fields_are_backward_compatible_and_overridable():
    brief = ProjectBrief.model_validate({"prompt": "A creator makes a long film."})
    shot = _shot(1, continuity_from_shot_id="shot_previous")

    assert brief.continuation_mode == ContinuationMode.FAST
    assert shot.continuation_mode is None

    overridden = shot.model_copy(update={"continuation_mode": ContinuationMode.QUALITY})
    assert overridden.continuation_mode == ContinuationMode.QUALITY


def test_effective_video_task_prefers_ref2va_but_preserves_safe_fallbacks():
    continuation = _shot(1, continuity_from_shot_id="shot_previous")
    explicit_start = continuation.model_copy(
        update={
            "start_frame_asset_id": "creator_frame",
            "task": ShotTask.REF2VA,
        }
    )

    assert (
        effective_video_task(
            continuation,
            ref2va_configured=True,
            fl2va_configured=True,
        )
        == ShotTask.REF2VA
    )
    assert (
        effective_video_task(
            continuation,
            ref2va_configured=False,
            fl2va_configured=True,
        )
        == ShotTask.FL2VA
    )
    assert (
        effective_video_task(
            explicit_start,
            ref2va_configured=True,
            fl2va_configured=True,
        )
        == ShotTask.FL2VA
    )


def test_continuation_rule_is_ephemeral_and_idempotent():
    original = _shot(1, continuity_from_shot_id="shot_previous")

    first_request = RenderManager._with_continuation_rule(original)
    retried_request = RenderManager._with_continuation_rule(first_request)

    assert RenderManager.CONTINUATION_REF2VA_RULE not in original.prompt
    assert first_request.prompt.count(RenderManager.CONTINUATION_REF2VA_RULE) == 1
    assert retried_request.prompt.count(RenderManager.CONTINUATION_REF2VA_RULE) == 1


def test_quality_uses_full_previous_clip_without_tail_transcode(settings, tmp_path, monkeypatch):
    configured = replace(settings, h3_fl2va_url="http://fl2va", h3_ref2va_url="http://ref2va")
    repository = StudioRepository(configured.database_path)
    first = _shot(0)
    continuation = _shot(1, continuity_from_shot_id=first.id)
    project = FilmProject(
        brief=ProjectBrief(
            prompt="A creator makes a long film.",
            duration_seconds=15,
            continuation_mode=ContinuationMode.QUALITY,
        ),
        world_bible=WorldBible(logline="A long film", visual_style="cinematic"),
        shots=[first, continuation],
    )
    manager = RenderManager(configured, repository)
    source = tmp_path / "shot-001.mp4"
    boundary = tmp_path / "shot-001-boundary.png"
    source.write_bytes(b"video")
    boundary.write_bytes(png_bytes().getvalue())

    def unexpected_extract(*args, **kwargs):
        raise AssertionError("quality mode must not extract a tail")

    monkeypatch.setattr(manager.media, "extract_tail", unexpected_extract)
    image, media = asyncio.run(
        manager._continuation_ref2va_inputs(
            project,
            continuation,
            1,
            {first.id: source},
            {first.id: boundary},
            tmp_path,
        )
    )

    assert image == boundary
    assert media == source


def test_fast_render_routes_continuation_to_tail_ref2va_and_leaves_asset_ref2va_ordinary(
    settings,
    tmp_path,
    monkeypatch,
):
    configured = replace(settings, h3_fl2va_url="http://fl2va", h3_ref2va_url="http://ref2va")
    repository = StudioRepository(configured.database_path)
    assets = AssetService(configured, repository)
    start = assets.ingest_stream(png_bytes(), "start.png", "image/png")
    ordinary_image_path = tmp_path / "ordinary.png"
    ordinary_video_path = tmp_path / "ordinary.mp4"
    ordinary_image_path.write_bytes(png_bytes("blue").getvalue())
    ordinary_video_path.write_bytes(b"reference-video")
    ordinary_image = repository.save_asset(
        AssetRecord(
            sha256="ordinary-image",
            original_name="ordinary.png",
            media_type="image/png",
            kind=AssetKind.IMAGE,
            size_bytes=ordinary_image_path.stat().st_size,
            external_path=str(ordinary_image_path),
            source="path",
        )
    )
    ordinary_video = repository.save_asset(
        AssetRecord(
            sha256="ordinary-video",
            original_name="ordinary.mp4",
            media_type="video/mp4",
            kind=AssetKind.VIDEO,
            size_bytes=ordinary_video_path.stat().st_size,
            external_path=str(ordinary_video_path),
            source="path",
        )
    )
    first = _shot(0, start_frame_asset_id=start.id, reference_asset_ids=[start.id])
    continuation = _shot(1, continuity_from_shot_id=first.id)
    ordinary = _shot(
        2,
        task=ShotTask.REF2VA,
        reference_asset_ids=[ordinary_image.id, ordinary_video.id],
    )
    project = repository.save_project(
        FilmProject(
            brief=ProjectBrief(
                prompt="A creator makes a long film.",
                duration_seconds=15,
                continuation_mode=ContinuationMode.FAST,
            ),
            world_bible=WorldBible(logline="A long film", visual_style="cinematic"),
            shots=[first, continuation, ordinary],
        )
    )
    manager = RenderManager(configured, repository)
    ref2va_requests: list[tuple[ShotSpec, Path]] = []
    extracted: list[tuple[Path, Path, float]] = []

    async def fake_fl2va(self, shot, start_frame, output_path, **kwargs):
        output_path.write_bytes(b"fl2va")
        return output_path

    async def fake_ref2va(self, shot, reference_image, reference_media, output_path, **kwargs):
        ref2va_requests.append((shot, reference_media))
        output_path.write_bytes(b"ref2va")
        return output_path

    def fake_fit(source, output, width, height):
        output.write_bytes(Path(source).read_bytes())
        return output

    def fake_boundary(source, output):
        output.write_bytes(png_bytes("green").getvalue())
        return output

    def fake_tail(source, output, duration):
        extracted.append((source, output, duration))
        output.write_bytes(b"tail-with-audio-video")
        return output

    def fake_concatenate(videos, output, *args, **kwargs):
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(H3Client, "generate_fl2va", fake_fl2va)
    monkeypatch.setattr(H3Client, "generate_ref2va", fake_ref2va)
    monkeypatch.setattr(manager.media, "fit_image_to_canvas", fake_fit)
    monkeypatch.setattr(manager.media, "extract_last_stable_frame", fake_boundary)
    monkeypatch.setattr(manager.media, "extract_tail", fake_tail)
    monkeypatch.setattr(manager.media, "concatenate", fake_concatenate)

    job = repository.save_job(RenderJob(project_id=project.id))
    asyncio.run(manager._run(job.id))

    completed = repository.get_job(job.id)
    assert completed is not None and completed.status == "complete"
    assert len(ref2va_requests) == 2
    continuation_request, continuation_media = ref2va_requests[0]
    ordinary_request, ordinary_media = ref2va_requests[1]
    assert continuation_request.prompt.count(RenderManager.CONTINUATION_REF2VA_RULE) == 1
    assert continuation_media.name.endswith("continuation-tail-5s.mp4")
    assert extracted == [
        (
            configured.output_dir / project.id / "shot-001.mp4",
            continuation_media,
            5.0,
        )
    ]
    assert RenderManager.CONTINUATION_REF2VA_RULE not in ordinary_request.prompt
    assert ordinary_media == ordinary_video_path
    persisted = repository.get_project(project.id)
    assert persisted is not None
    assert RenderManager.CONTINUATION_REF2VA_RULE not in persisted.shots[1].prompt


def test_failed_render_can_resume_a_completed_first_clip(settings, tmp_path, monkeypatch):
    configured = replace(settings, h3_fl2va_url="http://fl2va", h3_ref2va_url="http://ref2va")
    repository = StudioRepository(configured.database_path)
    completed_video = tmp_path / "completed-shot-001.mp4"
    completed_boundary = tmp_path / "completed-shot-001-boundary.png"
    completed_video.write_bytes(b"existing-video")
    completed_boundary.write_bytes(png_bytes("navy").getvalue())
    first = _shot(
        0,
        status=ShotStatus.COMPLETE,
        selected_take_path=str(completed_video),
        boundary_frame_path=str(completed_boundary),
    )
    continuation = _shot(1, continuity_from_shot_id=first.id)
    project = repository.save_project(
        FilmProject(
            brief=ProjectBrief(
                prompt="Resume a failed long film.",
                duration_seconds=15,
                continuation_mode=ContinuationMode.QUALITY,
            ),
            world_bible=WorldBible(logline="Resume", visual_style="cinematic"),
            shots=[first, continuation],
        )
    )
    manager = RenderManager(configured, repository)
    ref2va_media: list[Path] = []

    async def unexpected_fl2va(*args, **kwargs):
        raise AssertionError("the completed first clip must be reused")

    async def fake_ref2va(self, shot, reference_image, reference_media, output_path, **kwargs):
        ref2va_media.append(reference_media)
        output_path.write_bytes(b"continued-video")
        return output_path

    def fake_boundary(source, output):
        output.write_bytes(png_bytes("green").getvalue())
        return output

    def fake_concatenate(videos, output, *args, **kwargs):
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(H3Client, "generate_fl2va", unexpected_fl2va)
    monkeypatch.setattr(H3Client, "generate_ref2va", fake_ref2va)
    monkeypatch.setattr(manager.media, "extract_last_stable_frame", fake_boundary)
    monkeypatch.setattr(manager.media, "concatenate", fake_concatenate)

    job = repository.save_job(RenderJob(project_id=project.id))
    asyncio.run(manager._run(job.id))

    completed = repository.get_job(job.id)
    assert completed is not None and completed.status == "complete"
    assert ref2va_media == [completed_video]
