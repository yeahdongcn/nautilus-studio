from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_assets import png_bytes

from long_video_studio.assets import AssetService
from long_video_studio.compiler import FilmCompiler
from long_video_studio.domain import (
    AssetRole,
    ContinuationMode,
    FilmProject,
    ProjectBrief,
    ShotSpec,
    ShotTask,
    WorldBible,
)
from long_video_studio.planner import PlannerOutput, PlannerService
from long_video_studio.repository import StudioRepository


def test_planner_builds_continuity_aware_film_ir(settings):
    repository = StudioRepository(settings.database_path)
    assets = AssetService(settings, repository)
    hero = assets.ingest_stream(
        png_bytes(),
        "hero.png",
        "image/png",
        roles=[AssetRole.CHARACTER, AssetRole.START_FRAME],
    )
    planner = PlannerService(settings, repository)
    project = asyncio.run(
        planner.plan(
            ProjectBrief(
                title="Cat story",
                prompt="A woman and her cat turn a rainy evening into a joyful game.",
                duration_seconds=60,
                reference_asset_ids=[hero.id],
            )
        )
    )
    assert len(project.shots) == 5
    assert sum(shot.duration_seconds for shot in project.shots) == 60
    assert all(4 <= shot.duration_seconds <= 15 for shot in project.shots)
    assert project.shots[0].start_frame_asset_id == hero.id
    assert project.shots[1].continuity_from_shot_id == project.shots[0].id
    assert len(project.timeline) == len(project.shots)


def test_compiler_hides_unavailable_model_in_warnings(settings):
    repository = StudioRepository(settings.database_path)
    planner = PlannerService(settings, repository)
    project = asyncio.run(planner.plan(ProjectBrief(prompt="A calm 30 second travel story.", duration_seconds=30)))
    plan = FilmCompiler(settings).compile(project)
    video_stages = [stage for stage in plan.stages if stage.kind == "video"]
    assert len(video_stages) == len(project.shots)
    assert any("endpoint is not configured" in warning for warning in plan.warnings)
    assert any("generated anchor frame" in warning for warning in plan.warnings)
    assert plan.stages[-1].kind == "assembly"
    assert plan.deployments[0].capability_id == "minimax-h3-fl2va"
    assert plan.deployments[0].status == "unconfigured"
    assert plan.estimated_seconds > 0


def test_compiler_exposes_configured_image_edit_provider(settings):
    configured = replace(
        settings,
        image_edit_provider="openai-compatible",
        image_edit_base_url="https://images.example.test",
        image_edit_model="Qwen-Image-Edit-2509",
        image_edit_max_references=4,
    )

    capability = FilmCompiler(configured).capabilities()[0]

    assert capability.available is True
    assert capability.endpoint == "https://images.example.test"
    assert capability.supports_multiple_references is True
    assert capability.recommended_gpus == 0


def test_compiler_rejects_multi_image_claim_for_base_qwen_image_edit(settings):
    configured = replace(
        settings,
        image_edit_provider="vllm-omni",
        image_edit_base_url="https://images.example.test",
        image_edit_model="Qwen/Qwen-Image-Edit",
        image_edit_max_references=4,
    )

    capability = FilmCompiler(configured).capabilities()[0]

    assert capability.available is False
    assert capability.supports_multiple_references is False
    assert any("single-image" in note for note in capability.notes)


def _anchor_mode_project() -> FilmProject:
    first = ShotSpec(
        index=0,
        title="Opening",
        purpose="Establish the scene",
        duration_seconds=4,
        task=ShotTask.FL2VA,
        prompt="Establish the scene with the lead character.",
        reference_asset_ids=["scene"],
    )
    continuous = ShotSpec(
        index=1,
        title="Follow-through",
        purpose="Continue the action",
        duration_seconds=4,
        task=ShotTask.FL2VA,
        prompt="Continue the action without a cut.",
        reference_asset_ids=["scene"],
        continuity_from_shot_id=first.id,
    )
    ref2va = ShotSpec(
        index=2,
        title="Audio beat",
        purpose="Use the audio-conditioned branch",
        duration_seconds=4,
        task=ShotTask.REF2VA,
        prompt="A distinct audio-conditioned beat.",
        reference_asset_ids=["scene", "audio"],
    )
    cut = ShotSpec(
        index=3,
        title="New scene",
        purpose="Start a new scene",
        duration_seconds=4,
        task=ShotTask.FL2VA,
        prompt="Start the new scene with a clean visual anchor.",
        reference_asset_ids=["scene"],
    )
    return FilmProject(
        brief=ProjectBrief(prompt="A sixteen second story.", duration_seconds=16),
        world_bible=WorldBible(logline="A short story", visual_style="cinematic"),
        shots=[first, continuous, ref2va, cut],
    )


def _configured_image_edit(settings, mode: str):
    return replace(
        settings,
        image_edit_provider="vllm-omni",
        image_edit_base_url="http://image-edit.test",
        image_edit_model="Qwen/Qwen-Image-Edit-2509",
        image_edit_anchor_mode=mode,
        h3_fl2va_url="http://fl2va.test",
        h3_ref2va_url="http://ref2va.test",
    )


@pytest.mark.parametrize(
    ("mode", "expected_shots"),
    [
        ("first-shot", {0}),
        ("scene-cuts", {0, 3}),
        ("every-shot", {0, 3}),
    ],
)
def test_compiler_image_edit_anchor_modes_apply_only_to_fl2va(settings, mode, expected_shots):
    project = _anchor_mode_project()
    plan = FilmCompiler(_configured_image_edit(settings, mode)).compile(project)

    keyframes = [stage for stage in plan.stages if stage.kind == "keyframe"]
    positions = {
        project.shots.index(next(shot for shot in project.shots if shot.id == stage.shot_id)) for stage in keyframes
    }

    assert positions == expected_shots
    assert all(stage.capability_id == "qwen-image-edit" for stage in keyframes)
    assert all(stage.inputs["anchor_mode"] == mode for stage in keyframes)
    assert not any(stage.shot_id == project.shots[2].id for stage in keyframes)


def test_compiler_anchor_dependencies_follow_continuity_boundary(settings):
    project = _anchor_mode_project()
    plan = FilmCompiler(_configured_image_edit(settings, "every-shot")).compile(project)
    keyframes = {stage.shot_id: stage for stage in plan.stages if stage.kind == "keyframe"}
    videos = {stage.shot_id: stage for stage in plan.stages if stage.kind == "video"}

    first, continuous, _, cut = project.shots
    assert keyframes[first.id].depends_on == []
    assert continuous.id not in keyframes
    assert videos[continuous.id].depends_on == [videos[first.id].id]
    assert videos[continuous.id].capability_id == "minimax-h3-ref2va"
    assert videos[continuous.id].inputs["continuation_mode"] == "fast"
    assert keyframes[cut.id].depends_on == []
    assert videos[cut.id].depends_on == [keyframes[cut.id].id]
    # REF2VA never gets an Image Edit stage, even when the global mode is
    # every-shot.
    assert project.shots[2].id not in keyframes


def test_compiler_quality_continuation_uses_full_clip_ref2va(settings):
    project = _anchor_mode_project()
    fast_plan = FilmCompiler(_configured_image_edit(settings, "scene-cuts")).compile(project)
    fast_stage = next(
        stage for stage in fast_plan.stages if stage.kind == "video" and stage.shot_id == project.shots[1].id
    )
    project.brief.continuation_mode = ContinuationMode.QUALITY

    plan = FilmCompiler(_configured_image_edit(settings, "scene-cuts")).compile(project)
    stage = next(stage for stage in plan.stages if stage.kind == "video" and stage.shot_id == project.shots[1].id)

    assert stage.capability_id == "minimax-h3-ref2va"
    assert stage.inputs["continuation_mode"] == "quality"
    assert stage.inputs["continuity_from_shot_id"] == project.shots[0].id
    assert stage.estimated_seconds > fast_stage.estimated_seconds


def test_compiler_keeps_fl2va_as_unconfigured_ref2va_fallback(settings):
    project = _anchor_mode_project()
    configured = replace(settings, h3_fl2va_url="http://fl2va.test")

    plan = FilmCompiler(configured).compile(project)
    stage = next(stage for stage in plan.stages if stage.kind == "video" and stage.shot_id == project.shots[1].id)

    assert stage.capability_id == "minimax-h3-fl2va"
    assert any("internal FL2VA boundary fallback" in warning for warning in plan.warnings)


def test_compiler_explicit_start_frame_is_not_replaced_by_continuation_ref2va(settings):
    project = _anchor_mode_project()
    project.shots[1].start_frame_asset_id = "creator-start"

    plan = FilmCompiler(_configured_image_edit(settings, "scene-cuts")).compile(project)
    stage = next(stage for stage in plan.stages if stage.kind == "video" and stage.shot_id == project.shots[1].id)

    assert stage.capability_id == "minimax-h3-fl2va"
    assert stage.inputs["continuation_mode"] is None
    assert stage.depends_on == []


def test_compiler_first_shot_mode_does_not_promote_later_fl2va(settings):
    project = _anchor_mode_project()
    project.shots[0].task = ShotTask.REF2VA
    plan = FilmCompiler(_configured_image_edit(settings, "first-shot")).compile(project)

    keyframes = [stage for stage in plan.stages if stage.kind == "keyframe"]

    assert keyframes == []


def test_compiler_disabled_image_edit_keeps_direct_video_path(settings):
    project = _anchor_mode_project()
    project.shots[0].start_frame_asset_id = "scene"
    project.shots[3].start_frame_asset_id = "scene"
    plan = FilmCompiler(settings).compile(project)

    assert [stage for stage in plan.stages if stage.kind == "keyframe"] == []
    assert not any(deployment.capability_id == "qwen-image-edit" for deployment in plan.deployments)
    videos = {stage.shot_id: stage for stage in plan.stages if stage.kind == "video"}
    assert videos[project.shots[1].id].depends_on == [videos[project.shots[0].id].id]


def test_compiler_explicit_start_frame_bypasses_configured_image_edit(settings):
    project = _anchor_mode_project()
    project.shots[0].start_frame_asset_id = "creator-start"

    plan = FilmCompiler(_configured_image_edit(settings, "scene-cuts")).compile(project)

    keyframes = [stage for stage in plan.stages if stage.kind == "keyframe"]
    assert all(stage.shot_id != project.shots[0].id for stage in keyframes)


def test_planner_retrieves_matching_library_assets_when_none_are_selected(settings):
    repository = StudioRepository(settings.database_path)
    assets = AssetService(settings, repository)
    cat = assets.ingest_stream(
        png_bytes("orange"),
        "orange-cat.png",
        "image/png",
        tags=["猫", "客厅"],
        roles=[AssetRole.CHARACTER, AssetRole.START_FRAME],
    )
    planner = PlannerService(settings, repository)
    project = asyncio.run(
        planner.plan(
            ProjectBrief(
                prompt="一个女孩在客厅逗猫，猫开心地追逐玩具。",
                duration_seconds=30,
            )
        )
    )
    assert cat.id in project.brief.reference_asset_ids
    assert project.shots[0].start_frame_asset_id == cat.id


def test_planner_leaves_first_frame_empty_for_image_edit_references(settings):
    configured = replace(
        settings,
        image_edit_provider="vllm-omni",
        image_edit_base_url="http://image-edit.test",
        image_edit_model="Qwen/Qwen-Image-Edit-2511",
    )
    repository = StudioRepository(configured.database_path)
    assets = AssetService(configured, repository)
    scene = assets.ingest_stream(
        png_bytes("green"),
        "workshop.png",
        "image/png",
        tags=["工作室"],
        roles=[AssetRole.LOCATION],
    )

    project = asyncio.run(
        PlannerService(configured, repository).plan(
            ProjectBrief(
                prompt="一位创作者走进工作室，拿起桌上的发光道具。",
                duration_seconds=30,
                reference_asset_ids=[scene.id],
            )
        )
    )

    assert scene.id in project.shots[0].reference_asset_ids
    assert project.shots[0].start_frame_asset_id is None


def test_planner_preserves_explicit_start_frame_with_image_edit_configured(settings):
    configured = replace(
        settings,
        image_edit_provider="vllm-omni",
        image_edit_base_url="http://image-edit.test",
        image_edit_model="Qwen/Qwen-Image-Edit-2511",
    )
    repository = StudioRepository(configured.database_path)
    assets = AssetService(configured, repository)
    start = assets.ingest_stream(
        png_bytes("purple"),
        "opening.png",
        "image/png",
        tags=["指定首帧"],
        roles=[AssetRole.START_FRAME],
    )

    project = asyncio.run(
        PlannerService(configured, repository).plan(
            ProjectBrief(
                prompt="从指定画面自然开始一个连续镜头。",
                duration_seconds=30,
                reference_asset_ids=[start.id],
            )
        )
    )

    assert project.shots[0].start_frame_asset_id == start.id


def test_responses_sse_planner_path_returns_structured_agent_output(settings):
    repository = StudioRepository(settings.database_path)
    planner = PlannerService(
        replace(
            settings,
            planner_base_url="http://planner.test/v1",
            planner_api_key="test-key",
            planner_model="test-model",
            planner_wire_api="responses",
            planner_allow_fallback=False,
            planner_source="codex:test",
        ),
        repository,
    )
    brief = ProjectBrief(
        prompt="A woman and a cat share a joyful evening.",
        duration_seconds=30,
        style_preset="documentary",
        style_instructions="One continuous handheld shot with honest room tone.",
    )
    expected = planner._plan_heuristically(brief, [])
    payload = PlannerOutput(world_bible=expected.world_bible, shots=expected.shots).model_dump_json()
    sse = "\n".join(
        [
            'data: {"type":"response.created"}',
            *[
                f"data: {json.dumps({'type': 'response.output_text.delta', 'delta': chunk})}"
                for chunk in (payload[:80], payload[80:])
            ],
            'data: {"type":"response.completed","response":{"status":"completed"}}',
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["text"]["format"]["type"] == "json_schema"
        assert "documentary" in body["input"][0]["content"][0]["text"]
        assert "honest room tone" in body["input"][0]["content"][0]["text"]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)

    planner._transport = httpx.MockTransport(handler)
    output = asyncio.run(planner._plan_with_llm(brief, []))

    assert len(output.shots) == len(expected.shots)
    assert output.shots[0].prompt == expected.shots[0].prompt
    assert all(shot.fps == 24 and shot.flow_shift == 12.0 for shot in output.shots)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (
            "7秒，紧接上一镜头的连续电影写实画面。太和殿中，孟子义向龙椅走近。",
            "太和殿中，孟子义向龙椅走近。",
        ),
        (
            "7 seconds. Continue directly from the previous shot in a continuous cinematic image. "
            "Meng Ziyi walks toward the throne.",
            "Meng Ziyi walks toward the throne.",
        ),
    ],
)
def test_planner_strips_duration_and_reference_video_boilerplate(prompt, expected):
    assert PlannerService._clean_generation_prompt(prompt) == expected
