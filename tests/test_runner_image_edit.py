import asyncio
from dataclasses import replace
from pathlib import Path

from test_assets import png_bytes

from long_video_studio.adapters.image_edit import (
    ImageEditCapabilities,
    ImageEditRequest,
)
from long_video_studio.assets import AssetService
from long_video_studio.domain import (
    AssetRole,
    AssetUpdate,
    FilmProject,
    ProjectBrief,
    ShotSpec,
    ShotTask,
    WorldBible,
)
from long_video_studio.repository import StudioRepository
from long_video_studio.runner import RenderManager


class FakeImageEditProvider:
    def __init__(self):
        self.request: ImageEditRequest | None = None

    @property
    def capabilities(self):
        return ImageEditCapabilities("fake", "test", True, 4)

    async def edit(self, request: ImageEditRequest):
        self.request = request
        request.output_path.write_bytes(png_bytes().getvalue())
        return request.output_path


def test_missing_first_frame_generates_anchor_from_ordered_scene_and_character_references(settings):
    configured = replace(settings, image_edit_anchor_mode="first-shot")
    repository = StudioRepository(configured.database_path)
    assets = AssetService(configured, repository)
    scene = assets.ingest_stream(
        png_bytes(),
        "scene.png",
        "image/png",
        roles=[AssetRole.LOCATION],
        tags=["old town"],
    )
    character = assets.ingest_stream(
        png_bytes("royalblue"),
        "hero.png",
        "image/png",
        roles=[AssetRole.CHARACTER],
        tags=["red coat"],
    )
    scene = assets.update(scene.id, AssetUpdate(display_name="Old Town"))
    character = assets.update(character.id, AssetUpdate(display_name="Bai Lu"))
    shot = ShotSpec(
        index=0,
        title="Arrival",
        purpose="Introduce the hero",
        prompt="The hero steps into the rainy old town.",
        negative_prompt="text, logo, watermark",
        duration_seconds=4,
        task=ShotTask.FL2VA,
        reference_asset_ids=[scene.id, character.id],
    )
    project = repository.save_project(
        FilmProject(
            brief=ProjectBrief(prompt="A rainy arrival."),
            world_bible=WorldBible(logline="Arrival", visual_style="cinematic"),
            shots=[shot],
        )
    )
    provider = FakeImageEditProvider()
    manager = RenderManager(configured, repository)
    manager.image_edit_provider = provider
    output_dir = configured.output_dir / project.id
    output_dir.mkdir(parents=True)

    anchor = asyncio.run(manager._maybe_make_anchor(project, shot, 0, {}, output_dir))

    assert anchor == output_dir / "shot-001-anchor.png"
    assert provider.request is not None
    assert [reference.role for reference in provider.request.references] == [
        "location",
        "character",
    ]
    assert [reference.label for reference in provider.request.references] == [
        "Old Town",
        "Bai Lu",
    ]
    assert "red coat" in provider.request.references[1].tags
    assert "Old Town" in provider.request.prompt
    assert "Bai Lu" in provider.request.prompt
    assert "scene/background" in provider.request.prompt
    assert "character identity" in provider.request.prompt
    assert "仅按序号称呼参考图" in provider.request.prompt
    assert (provider.request.width, provider.request.height) == (1280, 720)
    assert provider.request.negative_prompt == "text, logo, watermark"
    assert provider.request.extra_body == {
        "num_inference_steps": 40,
        "true_cfg_scale": 4.0,
        "guidance_scale": 1.0,
    }
    persisted = repository.get_project(project.id)
    assert persisted.shots[0].anchor_frame_path == str(anchor)
    assert persisted.shots[0].anchor_prompt == provider.request.prompt


def test_explicit_start_frame_bypasses_image_edit(settings):
    configured = replace(settings, image_edit_anchor_mode="scene-cuts")
    repository = StudioRepository(configured.database_path)
    assets = AssetService(configured, repository)
    start = assets.ingest_stream(
        png_bytes(),
        "creator-start.png",
        "image/png",
        roles=[AssetRole.START_FRAME],
    )
    shot = ShotSpec(
        index=0,
        title="Creator opening",
        purpose="Honor the selected frame",
        prompt="Continue naturally from the exact creator frame.",
        duration_seconds=4,
        task=ShotTask.FL2VA,
        start_frame_asset_id=start.id,
        reference_asset_ids=[start.id],
    )
    project = repository.save_project(
        FilmProject(
            brief=ProjectBrief(prompt="A creator-controlled opening."),
            world_bible=WorldBible(logline="Opening", visual_style="cinematic"),
            shots=[shot],
        )
    )
    provider = FakeImageEditProvider()
    manager = RenderManager(configured, repository)
    manager.image_edit_provider = provider
    output_dir = configured.output_dir / project.id
    output_dir.mkdir(parents=True)

    anchor = asyncio.run(manager._maybe_make_anchor(project, shot, 0, {}, output_dir))

    assert anchor is None
    assert provider.request is None


def test_explicit_start_frame_wins_over_previous_boundary(settings, tmp_path):
    repository = StudioRepository(settings.database_path)
    assets = AssetService(settings, repository)
    start = assets.ingest_stream(
        png_bytes(),
        "creator-start.png",
        "image/png",
        roles=[AssetRole.START_FRAME],
    )
    previous_boundary = tmp_path / "previous-boundary.png"
    previous_boundary.write_bytes(png_bytes("royalblue").getvalue())
    shot = ShotSpec(
        index=1,
        title="Creator override",
        purpose="Honor the selected frame",
        prompt="Begin from the exact creator-selected composition.",
        duration_seconds=4,
        task=ShotTask.FL2VA,
        start_frame_asset_id=start.id,
        reference_asset_ids=[start.id],
        continuity_from_shot_id="shot_previous",
    )
    manager = RenderManager(settings, repository)

    selected = manager._start_frame(shot, {"shot_previous": previous_boundary})

    assert selected == Path(start.resolved_path)
