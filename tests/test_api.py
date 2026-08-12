from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
from test_assets import png_bytes

from long_video_studio.app import create_app
from long_video_studio.domain import (
    FilmProject,
    ProjectBrief,
    RenderJob,
    ShotSpec,
    ShotTask,
    WorldBible,
)
from long_video_studio.planner import PlannerError
from long_video_studio.runner import RenderManager


def test_creator_flow_upload_plan_edit_compile(settings):
    client = TestClient(create_app(settings))
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["planner"] == "heuristic"
    assert health.json()["fl2va_healthy"] is False
    assert health.json()["ref2va_healthy"] is False

    upload = client.post(
        "/api/assets/upload",
        data={"tags": "hero,warm", "roles": "character,start_frame"},
        files=[("files", ("hero.png", png_bytes().read(), "image/png"))],
    )
    assert upload.status_code == 200
    asset = upload.json()[0]

    planned = client.post(
        "/api/projects/plan",
        json={
            "title": "A creator flow",
            "prompt": "A woman plays with her cat and the mood grows increasingly joyful.",
            "duration_seconds": 30,
            "reference_asset_ids": [asset["id"]],
            "quality": "draft",
        },
    )
    assert planned.status_code == 200
    project = planned.json()
    assert project["shots"]
    assert project["shots"][0]["start_frame_asset_id"] == asset["id"]

    first_shot = project["shots"][0]
    edited = client.patch(
        f"/api/projects/{project['id']}/shots/{first_shot['id']}",
        json={"duration_seconds": 10, "prompt": "A smoother opening shot."},
    )
    assert edited.status_code == 200
    assert edited.json()["shots"][0]["prompt"] == "A smoother opening shot."

    compiled = client.post(f"/api/projects/{project['id']}/compile")
    assert compiled.status_code == 200
    assert compiled.json()["stages"][-1]["kind"] == "assembly"
    render = client.post(f"/api/projects/{project['id']}/render")
    assert render.status_code == 409
    assert "STUDIO_H3_FL2VA_URL" in render.json()["detail"]
    assert client.get("/").status_code == 200


def test_asset_content_rejects_paths_outside_media_roots(settings):
    app = create_app(settings)
    client = TestClient(app)
    uploaded = client.post(
        "/api/assets/upload",
        files=[("files", ("hero.png", png_bytes().read(), "image/png"))],
    )
    assert uploaded.status_code == 200
    asset_id = uploaded.json()[0]["id"]
    assert client.get(f"/api/assets/{asset_id}/content").status_code == 200

    outside = settings.data_dir.parent / "outside.png"
    outside.write_bytes(png_bytes().read())
    asset = app.state.services.repository.get_asset(asset_id)
    assert asset is not None
    app.state.services.repository.save_asset(asset.model_copy(update={"stored_path": str(outside)}))

    assert client.get(f"/api/assets/{asset_id}/content").status_code == 404


def test_asset_delete_succeeds_when_unreferenced(settings):
    client = TestClient(create_app(settings))
    upload = client.post(
        "/api/assets/upload",
        files=[("files", ("unused.png", png_bytes().read(), "image/png"))],
    )
    asset_id = upload.json()[0]["id"]

    deleted = client.delete(f"/api/assets/{asset_id}")

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert all(asset["id"] != asset_id for asset in client.get("/api/assets").json())


def test_asset_delete_is_blocked_while_project_references_it(settings):
    client = TestClient(create_app(settings))
    upload = client.post(
        "/api/assets/upload",
        data={"roles": "start_frame"},
        files=[("files", ("used.png", png_bytes().read(), "image/png"))],
    )
    asset_id = upload.json()[0]["id"]
    planned = client.post(
        "/api/projects/plan",
        json={
            "title": "Referenced asset",
            "prompt": "A short scene anchored by the uploaded frame.",
            "duration_seconds": 15,
            "reference_asset_ids": [asset_id],
            "quality": "draft",
        },
    )
    assert planned.status_code == 200

    deleted = client.delete(f"/api/assets/{asset_id}")

    assert deleted.status_code == 409
    assert "仍被项目或分镜引用" in deleted.json()["detail"]


def test_failed_planning_keeps_a_recoverable_project_draft(settings, monkeypatch):
    app = create_app(settings)

    async def fail_plan(brief, project_id=None):
        raise PlannerError("temporary planner failure")

    monkeypatch.setattr(app.state.services.planner, "plan", fail_plan)
    client = TestClient(app)

    response = client.post(
        "/api/projects/plan",
        json={
            "title": "Recoverable draft",
            "prompt": "A creator crosses a quiet station before dawn.",
            "duration_seconds": 30,
        },
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["message"] == "temporary planner failure"
    project_id = detail["project_id"]
    draft = client.get(f"/api/projects/{project_id}")
    assert draft.status_code == 200
    assert draft.json()["id"] == project_id
    assert draft.json()["status"] == "failed"
    assert draft.json()["shots"] == []
    assert any(project["id"] == project_id for project in client.get("/api/projects").json())


def test_render_endpoint_schedules_background_job_on_event_loop(settings, monkeypatch):
    async def no_op_run(self: RenderManager, job_id: str) -> None:
        return None

    monkeypatch.setattr(RenderManager, "_run", no_op_run)
    configured = replace(settings, h3_fl2va_url="http://fl2va.test")
    client = TestClient(create_app(configured))
    upload = client.post(
        "/api/assets/upload",
        data={"roles": "start_frame"},
        files=[("files", ("hero.png", png_bytes().read(), "image/png"))],
    )
    assert upload.status_code == 200
    asset_id = upload.json()[0]["id"]
    planned = client.post(
        "/api/projects/plan",
        json={
            "title": "Async render regression",
            "prompt": "A woman and a cat share a joyful moment.",
            "duration_seconds": 30,
            "reference_asset_ids": [asset_id],
            "quality": "draft",
        },
    )
    assert planned.status_code == 200

    response = client.post(f"/api/projects/{planned.json()['id']}/render")

    assert response.status_code == 200
    assert response.json()["status"] in {"queued", "running"}
    latest = client.get(f"/api/projects/{planned.json()['id']}/jobs/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == response.json()["id"]


def test_render_preflight_accepts_previous_clip_as_ref2va_continuation_input(settings, monkeypatch):
    async def no_op_run(self: RenderManager, job_id: str) -> None:
        return None

    monkeypatch.setattr(RenderManager, "_run", no_op_run)
    configured = replace(
        settings,
        h3_fl2va_url="http://fl2va.test",
        h3_ref2va_url="http://ref2va.test",
    )
    app = create_app(configured)
    client = TestClient(app)
    upload = client.post(
        "/api/assets/upload",
        data={"roles": "start_frame"},
        files=[("files", ("hero.png", png_bytes().read(), "image/png"))],
    )
    start_id = upload.json()[0]["id"]
    first = ShotSpec(
        index=0,
        title="Opening",
        purpose="Open",
        duration_seconds=7.5,
        task=ShotTask.FL2VA,
        prompt="Open on the creator.",
        start_frame_asset_id=start_id,
    )
    continuation = ShotSpec(
        index=1,
        title="Continue",
        purpose="Continue",
        duration_seconds=7.5,
        task=ShotTask.FL2VA,
        prompt="Continue with the next action.",
        continuity_from_shot_id=first.id,
    )
    project = app.state.services.repository.save_project(
        FilmProject(
            brief=ProjectBrief(prompt="A creator continues one flowing action."),
            world_bible=WorldBible(logline="One action", visual_style="realistic"),
            shots=[first, continuation],
        )
    )

    response = client.post(f"/api/projects/{project.id}/render")

    assert response.status_code == 200


def test_render_preflight_keeps_ordinary_ref2va_asset_contract(settings):
    configured = replace(settings, h3_ref2va_url="http://ref2va.test")
    app = create_app(configured)
    shot = ShotSpec(
        index=0,
        title="Asset reference",
        purpose="Use explicit references",
        duration_seconds=15,
        task=ShotTask.REF2VA,
        prompt="Animate the supplied reference media.",
    )
    project = app.state.services.repository.save_project(
        FilmProject(
            brief=ProjectBrief(prompt="Animate creator-provided reference media."),
            world_bible=WorldBible(logline="Reference", visual_style="realistic"),
            shots=[shot],
        )
    )

    response = TestClient(app).post(f"/api/projects/{project.id}/render")

    assert response.status_code == 409
    assert "image plus audio/video references for shot 1" in response.json()["detail"]


def test_project_and_shot_dialog_updates_persist_and_invalidate_old_take(settings):
    client = TestClient(create_app(settings))
    planned = client.post(
        "/api/projects/plan",
        json={
            "title": "Dialog editing",
            "prompt": "A creator walks through a warm studio and finds a glowing prop.",
            "duration_seconds": 30,
        },
    )
    assert planned.status_code == 200
    project = planned.json()
    shot = project["shots"][0]

    project_update = client.patch(
        f"/api/projects/{project['id']}",
        json={
            "brief": {
                "title": "Edited title",
                "style": "A hand-crafted 16mm look",
                "style_preset": "custom",
                "style_instructions": "Soft tungsten pools, patient camera, tactile grain.",
                "continuation_mode": "quality",
            },
            "world_bible": {
                "logline": "The glowing prop changes the room's mood.",
                "character_notes": ["The creator wears a blue jacket."],
                "location_notes": ["A compact workshop at dusk."],
            },
        },
    )

    assert project_update.status_code == 200
    updated = project_update.json()
    assert updated["brief"]["title"] == "Edited title"
    assert updated["brief"]["style_instructions"].startswith("Soft tungsten")
    assert updated["brief"]["continuation_mode"] == "quality"
    assert updated["world_bible"]["character_notes"] == ["The creator wears a blue jacket."]

    shot_update = client.patch(
        f"/api/projects/{project['id']}/shots/{shot['id']}",
        json={
            "title": "Edited opening",
            "purpose": "Introduce the prop before the reveal.",
            "prompt": "A slow push-in toward the glowing prop, no jump cut.",
            "negative_prompt": "text, logo, watermark",
            "duration_seconds": 8,
            "inference_steps": 50,
            "start_frame_asset_id": None,
            "continuation_mode": "fast",
        },
    )

    assert shot_update.status_code == 200
    edited_shot = shot_update.json()["shots"][0]
    assert edited_shot["title"] == "Edited opening"
    assert edited_shot["duration_seconds"] == 8
    assert edited_shot["start_frame_asset_id"] is None
    assert edited_shot["continuation_mode"] == "fast"
    assert edited_shot["status"] == "planned"
    assert edited_shot["selected_take_path"] is None
    assert shot_update.json()["timeline"][1]["start_seconds"] == 8


def test_completed_video_is_inline_unless_download_is_requested(settings):
    app = create_app(settings)
    project = app.state.services.repository.save_project(
        FilmProject(
            brief=ProjectBrief(prompt="A short preview film."),
            world_bible=WorldBible(logline="Preview", visual_style="Natural"),
            shots=[],
        )
    )
    output_path = settings.output_dir / project.id / "final.mp4"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"fake mp4")
    job = app.state.services.repository.save_job(
        RenderJob(
            project_id=project.id,
            status="complete",
            progress=1,
            output_path=str(output_path),
        )
    )
    client = TestClient(app)

    preview = client.get(f"/api/jobs/{job.id}/output")
    download = client.get(f"/api/jobs/{job.id}/output?download=true")

    assert preview.status_code == 200
    assert preview.headers.get("content-disposition", "").startswith("inline")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment")


def test_outputs_do_not_expose_absolute_paths(settings):
    app = create_app(settings)
    project = app.state.services.repository.save_project(
        FilmProject(
            brief=ProjectBrief(prompt="A short preview film."),
            world_bible=WorldBible(logline="Preview", visual_style="Natural"),
            shots=[],
        )
    )
    output_path = settings.output_dir / project.id / "final.mp4"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"video")
    app.state.services.repository.save_job(
        RenderJob(
            project_id=project.id,
            status="complete",
            progress=1,
            output_path=str(output_path),
        )
    )

    response = TestClient(app).get("/api/outputs", params={"project_id": project.id})

    assert response.status_code == 200
    assert response.json() == [{"name": "final.mp4", "size_bytes": 5}]


def test_completed_video_cannot_escape_project_output_directory(settings, tmp_path):
    app = create_app(settings)
    project = app.state.services.repository.save_project(
        FilmProject(
            brief=ProjectBrief(prompt="A short preview film."),
            world_bible=WorldBible(logline="Preview", visual_style="Natural"),
            shots=[],
        )
    )
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not a project output")
    job = app.state.services.repository.save_job(
        RenderJob(
            project_id=project.id,
            status="complete",
            progress=1,
            output_path=str(outside),
        )
    )

    response = TestClient(app).get(f"/api/jobs/{job.id}/output")

    assert response.status_code == 404


def test_react_web_root_serves_vite_assets(settings, tmp_path):
    assets = tmp_path / "web" / "assets"
    assets.mkdir(parents=True)
    (tmp_path / "web" / "index.html").write_text(
        '<script type="module" src="/assets/index-test.js"></script>', encoding="utf-8"
    )
    (assets / "index-test.js").write_text("export const ready = true;\n", encoding="utf-8")
    app = create_app(replace(settings, web_root=tmp_path / "web"))
    client = TestClient(app)

    assert client.get("/").status_code == 200
    asset = client.get("/assets/index-test.js")
    assert asset.status_code == 200
    assert "ready" in asset.text
