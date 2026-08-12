from __future__ import annotations

import asyncio
from pathlib import Path

from long_video_studio.adapters.h3 import H3Client
from long_video_studio.adapters.image_edit import (
    ImageEditProvider,
    ImageEditReference,
    ImageEditRequest,
    build_first_frame_prompt,
    provider_from_settings,
)
from long_video_studio.adapters.media import MediaTools
from long_video_studio.config import Settings
from long_video_studio.domain import (
    AssetKind,
    ContinuationMode,
    FilmProject,
    RenderJob,
    ShotSpec,
    ShotStatus,
    ShotTask,
    effective_video_task,
    resolved_continuation_mode,
    utc_now,
)
from long_video_studio.repository import StudioRepository


class RenderManager:
    CONTINUATION_TAIL_SECONDS = 5.0
    CONTINUATION_REF2VA_RULE = (
        "Continue from the moment immediately after the reference video's final frame. "
        "Do not replay, restage, summarize, or repeat any action that already occurred "
        "in the reference video. Begin with the next new action while preserving character "
        "identity, scene geometry, camera direction, motion, and audio continuity."
    )

    def __init__(self, settings: Settings, repository: StudioRepository):
        self.settings = settings
        self.repository = repository
        self.media = MediaTools(settings.ffmpeg_binary, settings.ffprobe_binary)
        self.image_edit_provider: ImageEditProvider | None = None
        self.image_edit_provider_error: str | None = None
        try:
            self.image_edit_provider = provider_from_settings(settings)
        except ValueError as error:
            self.image_edit_provider_error = str(error)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def submit(self, project_id: str) -> RenderJob:
        project = self.repository.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        for job_id, task in tuple(self._tasks.items()):
            if task.done():
                self._tasks.pop(job_id, None)
                continue
            active_job = self.repository.get_job(job_id)
            if active_job and active_job.project_id == project_id:
                return active_job
        job = self.repository.save_job(RenderJob(project_id=project_id))
        # FastAPI invokes the render route on its event-loop thread.  Resolve
        # that loop explicitly so a worker-thread refactor cannot lose it.
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._run(job.id))
        self._tasks[job.id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(job.id, None))
        return job

    async def _run(self, job_id: str) -> None:
        job = self.repository.get_job(job_id)
        if not job:
            return
        project = self.repository.get_project(job.project_id)
        if not project:
            return
        job.status = "running"
        job.message = "starting render"
        self.repository.save_job(job)
        project.status = "rendering"
        self.repository.save_project(project)
        output_dir = self.settings.output_dir / project.id
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered: list[Path] = []
        rendered_by_shot: dict[str, Path] = {}
        boundary_frames: dict[str, Path] = {}
        width, height = self._video_canvas(project.brief.aspect_ratio)
        try:
            for position, shot in enumerate(sorted(project.shots, key=lambda value: value.index)):
                job.current_shot_id = shot.id
                job.progress = position / max(len(project.shots), 1)
                job.message = f"rendering shot {position + 1}/{len(project.shots)}"
                self.repository.save_job(job)
                output_path = output_dir / f"shot-{position + 1:03d}.mp4"
                reusable_take = self.reusable_take_path(shot)
                if reusable_take is not None:
                    shot.status = ShotStatus.COMPLETE
                    rendered.append(reusable_take)
                    rendered_by_shot[shot.id] = reusable_take
                    boundary = (
                        Path(shot.boundary_frame_path)
                        if shot.boundary_frame_path
                        else output_dir / f"shot-{position + 1:03d}-boundary.png"
                    )
                    if not boundary.is_file():
                        await asyncio.to_thread(
                            self.media.extract_last_stable_frame,
                            reusable_take,
                            boundary,
                        )
                    shot.boundary_frame_path = str(boundary)
                    boundary_frames[shot.id] = boundary
                    job.progress = (position + 1) / len(project.shots)
                    job.message = f"reused shot {position + 1}/{len(project.shots)}"
                    self.repository.save_job(job)
                    self.repository.save_project(project)
                    continue
                shot.status = ShotStatus.RENDERING
                self.repository.save_project(project)
                runtime_task = effective_video_task(
                    shot,
                    ref2va_configured=bool(self.settings.h3_ref2va_url),
                    fl2va_configured=bool(self.settings.h3_fl2va_url),
                )
                is_ref2va_continuation = bool(
                    runtime_task == ShotTask.REF2VA and shot.continuity_from_shot_id and not shot.start_frame_asset_id
                )
                if runtime_task == ShotTask.FL2VA:
                    start_frame = self._start_frame(shot, boundary_frames)
                    anchor = await self._maybe_make_anchor(
                        project,
                        shot,
                        position,
                        boundary_frames,
                        output_dir,
                    )
                    if anchor:
                        start_frame = anchor
                    prepared_start = output_dir / f"shot-{position + 1:03d}-start-{width}x{height}.png"
                    await asyncio.to_thread(
                        self.media.fit_image_to_canvas,
                        start_frame,
                        prepared_start,
                        width,
                        height,
                    )
                    if not self.settings.h3_fl2va_url:
                        raise RuntimeError("STUDIO_H3_FL2VA_URL is not configured")
                    await H3Client(
                        self.settings.h3_fl2va_url,
                        self.settings.h3_timeout_seconds,
                        self.settings.h3_flow_shift,
                    ).generate_fl2va(
                        shot,
                        prepared_start,
                        output_path,
                        width=width,
                        height=height,
                        async_job=True,
                    )
                elif is_ref2va_continuation:
                    if not self.settings.h3_ref2va_url:
                        raise RuntimeError("STUDIO_H3_REF2VA_URL is not configured")
                    image, media = await self._continuation_ref2va_inputs(
                        project,
                        shot,
                        position,
                        rendered_by_shot,
                        boundary_frames,
                        output_dir,
                    )
                    request_shot = self._with_continuation_rule(shot)
                    await H3Client(
                        self.settings.h3_ref2va_url,
                        self.settings.h3_timeout_seconds,
                        self.settings.h3_flow_shift,
                    ).generate_ref2va(
                        request_shot,
                        image,
                        media,
                        output_path,
                        width=width,
                        height=height,
                        async_job=True,
                    )
                else:
                    if not self.settings.h3_ref2va_url:
                        raise RuntimeError("STUDIO_H3_REF2VA_URL is not configured")
                    image, media = self._ref2va_inputs(shot)
                    await H3Client(
                        self.settings.h3_ref2va_url,
                        self.settings.h3_timeout_seconds,
                        self.settings.h3_flow_shift,
                    ).generate_ref2va(
                        shot,
                        image,
                        media,
                        output_path,
                        width=width,
                        height=height,
                        async_job=True,
                    )
                shot.selected_take_path = str(output_path)
                shot.status = ShotStatus.COMPLETE
                rendered.append(output_path)
                rendered_by_shot[shot.id] = output_path
                boundary = output_dir / f"shot-{position + 1:03d}-boundary.png"
                await asyncio.to_thread(self.media.extract_last_stable_frame, output_path, boundary)
                shot.boundary_frame_path = str(boundary)
                boundary_frames[shot.id] = boundary
                self.repository.save_project(project)

            final_path = output_dir / "final.mp4"
            continuous_boundaries = [
                bool(shot.continuity_from_shot_id and not shot.start_frame_asset_id) for shot in project.shots[1:]
            ]
            await asyncio.to_thread(
                self.media.concatenate,
                rendered,
                final_path,
                self.settings.transition_seconds,
                continuous_boundaries,
            )
            subtitle_path = self._write_sidecar_subtitles(project, output_dir)
            project.status = "complete"
            self.repository.save_project(project)
            job.status = "complete"
            job.progress = 1
            job.current_shot_id = None
            job.message = "render complete"
            job.output_path = str(final_path)
            job.subtitle_path = str(subtitle_path) if subtitle_path else None
            self.repository.save_job(job)
        except Exception as error:  # noqa: BLE001 - background job must persist failures
            project.status = "failed"
            for shot in project.shots:
                if shot.status == ShotStatus.RENDERING:
                    shot.status = ShotStatus.FAILED
            self.repository.save_project(project)
            job.status = "failed"
            job.error = str(error)
            job.message = "render failed"
            self.repository.save_job(job)

    @staticmethod
    def _write_sidecar_subtitles(project, output_dir: Path) -> Path | None:
        if project.brief.subtitle_mode != "sidecar":
            return None
        entries: list[str] = []
        elapsed = 0.0
        sequence = 1
        for shot in sorted(project.shots, key=lambda value: value.index):
            text = (shot.subtitle_text or "").strip()
            start = elapsed
            elapsed += shot.duration_seconds
            if not text:
                continue
            end = elapsed
            entries.extend(
                [
                    str(sequence),
                    f"{RenderManager._srt_time(start)} --> {RenderManager._srt_time(end)}",
                    text,
                    "",
                ]
            )
            sequence += 1
        if not entries:
            return None
        path = output_dir / "final.srt"
        path.write_text("\n".join(entries), encoding="utf-8")
        return path

    @staticmethod
    def _srt_time(seconds: float) -> str:
        millis = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(millis, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds_value, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds_value:02d},{millis:03d}"

    @staticmethod
    def _video_canvas(aspect_ratio: str) -> tuple[int, int]:
        # MiniMax-H3 rounds canvases to multiples of 32. These shapes preserve
        # roughly equal pixel area while honoring landscape/portrait/square.
        return {
            "16:9": (1280, 704),
            "9:16": (704, 1280),
            "1:1": (960, 960),
        }[aspect_ratio]

    @staticmethod
    def reusable_take_path(shot) -> Path | None:
        if not shot.selected_take_path or shot.status != ShotStatus.COMPLETE:
            return None
        path = Path(shot.selected_take_path)
        if not path.is_file() or path.stat().st_size <= 0:
            return None
        return path

    def _start_frame(self, shot, boundary_frames: dict[str, Path]) -> Path:
        # A creator-selected start frame is an explicit composition decision
        # and therefore wins even when the shot also carries continuity
        # metadata from an earlier storyboard plan.
        if shot.start_frame_asset_id:
            asset = self.repository.get_asset(shot.start_frame_asset_id)
            if asset:
                return Path(asset.resolved_path)
        if shot.continuity_from_shot_id and shot.continuity_from_shot_id in boundary_frames:
            return boundary_frames[shot.continuity_from_shot_id]
        for asset_id in shot.reference_asset_ids:
            asset = self.repository.get_asset(asset_id)
            if asset and asset.kind == AssetKind.IMAGE:
                return Path(asset.resolved_path)
        raise RuntimeError(f"shot {shot.id} has no start frame")

    async def _maybe_make_anchor(
        self,
        project,
        shot,
        position: int,
        boundary_frames: dict[str, Path],
        output_dir: Path,
    ) -> Path | None:
        """Optionally build a story-aware FL2VA anchor through Image Edit."""

        if self.image_edit_provider_error:
            raise RuntimeError(self.image_edit_provider_error)
        provider = self.image_edit_provider
        if provider is None:
            return None
        if shot.task != ShotTask.FL2VA:
            return None
        # An explicit start frame is a creator decision, not an edit
        # reference. Only synthesize an anchor when the shot has no start.
        if shot.start_frame_asset_id:
            return None
        mode = self.settings.image_edit_anchor_mode
        if mode not in {"first-shot", "scene-cuts", "every-shot"}:
            raise RuntimeError(f"unsupported STUDIO_IMAGE_EDIT_ANCHOR_MODE: {mode}")
        is_scene_cut = position == 0 or not shot.continuity_from_shot_id
        if mode == "first-shot" and position != 0:
            return None
        if mode == "scene-cuts" and not is_scene_cut:
            return None

        references = self._anchor_references(shot, boundary_frames)
        anchor_path = output_dir / f"shot-{position + 1:03d}-anchor.png"
        prompt = build_first_frame_prompt(
            tuple(references),
            shot.prompt,
            project.brief.aspect_ratio,
        )
        shot.anchor_prompt = prompt
        project.updated_at = utc_now()
        self.repository.save_project(project)
        await provider.edit(
            ImageEditRequest(
                prompt=prompt,
                references=tuple(references),
                output_path=anchor_path,
                width={"16:9": 1280, "9:16": 720, "1:1": 1024}[project.brief.aspect_ratio],
                height={"16:9": 720, "9:16": 1280, "1:1": 1024}[project.brief.aspect_ratio],
                negative_prompt=shot.negative_prompt or None,
                extra_body={
                    "num_inference_steps": self.settings.image_edit_steps,
                    "true_cfg_scale": self.settings.image_edit_true_cfg_scale,
                    "guidance_scale": self.settings.image_edit_guidance_scale,
                },
            )
        )
        shot.anchor_frame_path = str(anchor_path)
        self.repository.save_project(project)
        return anchor_path

    def _anchor_references(
        self,
        shot,
        boundary_frames: dict[str, Path],
    ) -> list[ImageEditReference]:
        references: list[ImageEditReference] = []
        seen: set[Path] = set()

        def add(path: Path, label: str, role: str, tags: tuple[str, ...] = (), caption: str | None = None):
            resolved = path.resolve()
            if resolved in seen:
                return
            seen.add(resolved)
            references.append(ImageEditReference(path, label, role, tags, caption))

        if shot.continuity_from_shot_id and shot.continuity_from_shot_id in boundary_frames:
            add(boundary_frames[shot.continuity_from_shot_id], "previous shot boundary", "continuity")
        if shot.start_frame_asset_id:
            asset = self.repository.get_asset(shot.start_frame_asset_id)
            if asset and asset.kind == AssetKind.IMAGE:
                add(
                    Path(asset.resolved_path),
                    asset.display_name or asset.caption or Path(asset.original_name).stem,
                    "start_frame",
                    tuple(asset.tags),
                    asset.caption,
                )
        for asset_id in shot.reference_asset_ids:
            asset = self.repository.get_asset(asset_id)
            if not asset or asset.kind != AssetKind.IMAGE:
                continue
            role = next(
                (
                    getattr(value, "value", value)
                    for value in asset.roles
                    if getattr(value, "value", value) != "reference"
                ),
                "reference",
            )
            add(
                Path(asset.resolved_path),
                asset.display_name or asset.caption or Path(asset.original_name).stem,
                role,
                tuple(asset.tags),
                asset.caption,
            )
        if not references:
            raise RuntimeError(f"shot {shot.id} has no image reference for Image Edit")
        return references

    def _ref2va_inputs(self, shot) -> tuple[Path, Path]:
        image: Path | None = None
        media: Path | None = None
        for asset_id in shot.reference_asset_ids:
            asset = self.repository.get_asset(asset_id)
            if not asset:
                continue
            if asset.kind == AssetKind.IMAGE and not image:
                image = Path(asset.resolved_path)
            elif asset.kind in {AssetKind.AUDIO, AssetKind.VIDEO} and not media:
                media = Path(asset.resolved_path)
        if shot.audio_asset_id:
            asset = self.repository.get_asset(shot.audio_asset_id)
            if asset:
                media = Path(asset.resolved_path)
        if not image or not media:
            raise RuntimeError(f"shot {shot.id} requires image plus audio/video references")
        return image, media

    async def _continuation_ref2va_inputs(
        self,
        project: FilmProject,
        shot: ShotSpec,
        position: int,
        rendered_by_shot: dict[str, Path],
        boundary_frames: dict[str, Path],
        output_dir: Path,
    ) -> tuple[Path, Path]:
        source_id = shot.continuity_from_shot_id
        if not source_id:
            raise RuntimeError(f"shot {shot.id} has no continuation source")
        source_video = rendered_by_shot.get(source_id)
        source_boundary = boundary_frames.get(source_id)
        if not source_video or not source_boundary:
            raise RuntimeError(f"shot {shot.id} continuation source {source_id} is not rendered")

        mode = resolved_continuation_mode(project, shot)
        if mode == ContinuationMode.QUALITY:
            return source_boundary, source_video

        tail_path = output_dir / (f"shot-{position + 1:03d}-continuation-tail-{self.CONTINUATION_TAIL_SECONDS:g}s.mp4")
        await asyncio.to_thread(
            self.media.extract_tail,
            source_video,
            tail_path,
            self.CONTINUATION_TAIL_SECONDS,
        )
        return source_boundary, tail_path

    @classmethod
    def _with_continuation_rule(cls, shot: ShotSpec) -> ShotSpec:
        """Build an ephemeral Ref2VA request without mutating storyboard text."""

        prompt = shot.prompt.rstrip()
        if cls.CONTINUATION_REF2VA_RULE not in prompt:
            prompt = f"{prompt}\n\nCONTINUATION CONSTRAINT:\n{cls.CONTINUATION_REF2VA_RULE}"
        return shot.model_copy(deep=True, update={"prompt": prompt})
