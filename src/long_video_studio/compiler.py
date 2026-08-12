from __future__ import annotations

from long_video_studio.adapters.image_edit import known_multi_image_support
from long_video_studio.config import Settings
from long_video_studio.domain import (
    ContinuationMode,
    DeploymentRequest,
    ExecutionPlan,
    ExecutionStage,
    FilmProject,
    ModelCapability,
    ShotTask,
    effective_video_task,
    resolved_continuation_mode,
)


class FilmCompiler:
    """Compile creator-level Film IR into an infrastructure-facing execution plan."""

    _IMAGE_EDIT_ANCHOR_MODES = frozenset({"first-shot", "scene-cuts", "every-shot"})

    def __init__(self, settings: Settings):
        self.settings = settings

    def capabilities(self) -> list[ModelCapability]:
        image_edit_configured = bool(
            self.settings.image_edit_provider not in {"", "disabled", "none"}
            and self.settings.image_edit_base_url
            and self.settings.image_edit_model
        )
        known_multi_image = (
            known_multi_image_support(self.settings.image_edit_model) if self.settings.image_edit_model else None
        )
        invalid_reference_limit = known_multi_image is False and self.settings.image_edit_max_references > 1
        image_edit_available = image_edit_configured and not invalid_reference_limit
        image_edit_notes = [
            f"Provider: {self.settings.image_edit_provider}.",
            "Supports local vLLM-Omni and hosted OpenAI-compatible adapters.",
        ]
        if invalid_reference_limit:
            image_edit_notes.append(
                "Configured checkpoint is single-image; set max references to 1 or use Qwen-Image-Edit-2509."
            )
        return [
            ModelCapability(
                id="qwen-image-edit",
                display_name="Image Edit Provider",
                task="image_edit",
                endpoint=self.settings.image_edit_base_url,
                available=image_edit_available,
                supports_multiple_references=(
                    image_edit_available
                    and self.settings.image_edit_max_references > 1
                    and known_multi_image is not False
                ),
                # The Studio also accepts hosted providers. Do not claim a
                # self-hosted GPU count until that exact backend is validated.
                recommended_gpus=0,
                notes=image_edit_notes,
            ),
            ModelCapability(
                id="minimax-h3-fl2va",
                display_name="MiniMax-H3 FL2VA",
                task="fl2va",
                endpoint=self.settings.h3_fl2va_url,
                available=bool(self.settings.h3_fl2va_url),
                max_duration_seconds=15,
                supports_audio=True,
                recommended_gpus=8,
                notes=["First-frame-led video and audio generation."],
            ),
            ModelCapability(
                id="minimax-h3-ref2va",
                display_name="MiniMax-H3 Ref2VA",
                task="ref2va",
                endpoint=self.settings.h3_ref2va_url,
                available=bool(self.settings.h3_ref2va_url),
                max_duration_seconds=15,
                supports_audio=True,
                supports_multiple_references=True,
                recommended_gpus=8,
                notes=["Reference image plus audio/video conditioning."],
            ),
            ModelCapability(
                id="continuity-qc",
                display_name="Continuity supervisor",
                task="quality_control",
                available=True,
                notes=["MVP validates media boundaries; VLM scoring is an extension point."],
            ),
            ModelCapability(
                id="ffmpeg",
                display_name="Timeline renderer",
                task="assembly",
                available=True,
                notes=["Concatenates selected takes and extracts stable boundary frames."],
            ),
        ]

    def compile(self, project: FilmProject) -> ExecutionPlan:
        capability_map = {capability.id: capability for capability in self.capabilities()}
        stages: list[ExecutionStage] = []
        warnings: list[str] = []
        video_stage_by_shot: dict[str, str] = {}
        total_estimate = 0.0
        image_edit = capability_map["qwen-image-edit"]
        anchor_mode = self.settings.image_edit_anchor_mode
        if image_edit.available and anchor_mode not in self._IMAGE_EDIT_ANCHOR_MODES:
            warnings.append(f"unsupported STUDIO_IMAGE_EDIT_ANCHOR_MODE: {anchor_mode}")

        for position, shot in enumerate(sorted(project.shots, key=lambda value: value.index)):
            dependencies: list[str] = []
            if shot.continuity_from_shot_id and not shot.start_frame_asset_id:
                previous_stage = video_stage_by_shot.get(shot.continuity_from_shot_id)
                if previous_stage:
                    dependencies.append(previous_stage)

            runtime_task = effective_video_task(
                shot,
                ref2va_configured=bool(self.settings.h3_ref2va_url),
                fl2va_configured=bool(self.settings.h3_fl2va_url),
            )
            is_fl2va = runtime_task == ShotTask.FL2VA
            has_start_reference = bool(shot.start_frame_asset_id or shot.reference_asset_ids)
            missing_non_continuity_start = is_fl2va and not shot.continuity_from_shot_id and not has_start_reference
            selected_by_mode = is_fl2va and image_edit.available and self._anchor_selected(shot, position, anchor_mode)

            # A configured Image Edit provider follows RenderManager's anchor
            # mode exactly.  When it is disabled, retain the plan-only
            # keyframe stage for a genuinely missing non-continuity start image
            # so callers still receive the actionable warning below.  Direct
            # start/reference images continue to use the existing video path.
            needs_keyframe = selected_by_mode or (not image_edit.available and missing_non_continuity_start)
            if needs_keyframe:
                keyframe_stage = ExecutionStage(
                    shot_id=shot.id,
                    kind="keyframe",
                    capability_id="qwen-image-edit",
                    inputs={
                        "reference_asset_ids": shot.reference_asset_ids,
                        "instruction": shot.continuity_in.model_dump(mode="json"),
                        "anchor_mode": anchor_mode,
                    },
                    depends_on=list(dependencies),
                    estimated_seconds=20,
                )
                stages.append(keyframe_stage)
                # The generated frame is the sole input to FL2VA.  Keeping the
                # predecessor on the keyframe stage makes the dependency graph
                # explicit for continuous shots and avoids running Image Edit
                # before the previous boundary exists.
                dependencies = [keyframe_stage.id]
                total_estimate += keyframe_stage.estimated_seconds or 0
                if not image_edit.available:
                    warnings.append(
                        f"Shot {position + 1} has no non-continuity start image and requires a "
                        "generated anchor frame; image-edit adapter is not configured."
                    )
                elif missing_non_continuity_start:
                    warnings.append(f"Shot {position + 1} is selected for Image Edit but has no image reference.")
            elif missing_non_continuity_start:
                if image_edit.available:
                    warnings.append(
                        f"Shot {position + 1} has no non-continuity start image; "
                        f"anchor mode '{anchor_mode}' does not select it."
                    )
                else:
                    # This branch is defensive (the unavailable-provider
                    # fallback above normally creates the keyframe stage), but
                    # keeps the warning contract stable if that policy changes.
                    warnings.append(
                        f"Shot {position + 1} has no non-continuity start image and requires a "
                        "generated anchor frame; image-edit adapter is not configured."
                    )

            capability_id = "minimax-h3-ref2va" if runtime_task == ShotTask.REF2VA else "minimax-h3-fl2va"
            capability = capability_map[capability_id]
            if not capability.available:
                warnings.append(f"{capability.display_name} endpoint is not configured; render stays plan-only.")
            if (
                shot.continuity_from_shot_id
                and not shot.start_frame_asset_id
                and runtime_task == ShotTask.FL2VA
                and shot.task == ShotTask.FL2VA
            ):
                warnings.append(
                    f"Shot {position + 1} uses the internal FL2VA boundary fallback because "
                    "the Ref2VA endpoint is not configured."
                )
            continuation_mode = (
                resolved_continuation_mode(project, shot)
                if shot.continuity_from_shot_id and not shot.start_frame_asset_id and runtime_task == ShotTask.REF2VA
                else None
            )
            estimate = self._estimate_video_seconds(
                shot.duration_seconds,
                shot.inference_steps,
                continuation_mode,
            )
            video_stage = ExecutionStage(
                shot_id=shot.id,
                kind="video",
                capability_id=capability_id,
                depends_on=dependencies,
                inputs={
                    "prompt": shot.prompt,
                    "negative_prompt": shot.negative_prompt,
                    "duration_seconds": shot.duration_seconds,
                    "fps": shot.fps,
                    "inference_steps": shot.inference_steps,
                    "seed": shot.seed,
                    "reference_asset_ids": shot.reference_asset_ids,
                    "start_frame_asset_id": shot.start_frame_asset_id,
                    "audio_asset_id": shot.audio_asset_id,
                    "continuity_from_shot_id": shot.continuity_from_shot_id,
                    "continuation_mode": (
                        resolved_continuation_mode(project, shot).value
                        if (
                            shot.continuity_from_shot_id
                            and not shot.start_frame_asset_id
                            and runtime_task == ShotTask.REF2VA
                        )
                        else None
                    ),
                },
                estimated_seconds=estimate,
            )
            stages.append(video_stage)
            video_stage_by_shot[shot.id] = video_stage.id
            total_estimate += estimate

            qc_stage = ExecutionStage(
                shot_id=shot.id,
                kind="continuity_check",
                capability_id="continuity-qc",
                depends_on=[video_stage.id],
                inputs={
                    "expected_in": shot.continuity_in.model_dump(mode="json"),
                    "expected_out": shot.continuity_out.model_dump(mode="json"),
                },
                estimated_seconds=2,
            )
            stages.append(qc_stage)
            total_estimate += 2

        assembly = ExecutionStage(
            kind="assembly",
            capability_id="ffmpeg",
            depends_on=list(video_stage_by_shot.values()),
            inputs={"timeline": [clip.model_dump(mode="json") for clip in project.timeline]},
            estimated_seconds=max(5, project.brief.duration_seconds * 0.15),
        )
        stages.append(assembly)
        total_estimate += assembly.estimated_seconds or 0
        deployments: list[DeploymentRequest] = []
        for capability_id in ("minimax-h3-fl2va", "minimax-h3-ref2va", "qwen-image-edit"):
            capability = capability_map[capability_id]
            shot_ids = [
                stage.shot_id
                for stage in stages
                if stage.kind == "video" and stage.capability_id == capability_id and stage.shot_id
            ]
            if not shot_ids and capability_id == "qwen-image-edit":
                shot_ids = [stage.shot_id for stage in stages if stage.kind == "keyframe" and stage.shot_id]
            if not shot_ids:
                continue
            deployments.append(
                DeploymentRequest(
                    capability_id=capability_id,
                    endpoint=capability.endpoint,
                    recommended_gpus=capability.recommended_gpus,
                    shot_ids=shot_ids,
                    status="ready" if capability.available else "unconfigured",
                    rationale=(
                        "Keep this capability warm and batch dependent shots where possible."
                        if capability_id.startswith("minimax-h3")
                        else "Prepare anchor frames before the dependent video stages."
                    ),
                )
            )
        return ExecutionPlan(
            project_id=project.id,
            stages=stages,
            deployments=deployments,
            warnings=list(dict.fromkeys(warnings)),
            estimated_seconds=round(total_estimate, 1),
        )

    @classmethod
    def _anchor_selected(cls, shot, position: int, mode: str) -> bool:
        """Return whether Image Edit should create an anchor for this FL2VA shot."""

        if shot.task != ShotTask.FL2VA:
            return False
        if shot.start_frame_asset_id:
            return False
        if mode == "first-shot":
            # Deliberately use project position, matching RenderManager.  A
            # later FL2VA shot is not promoted to the first anchor merely
            # because an earlier shot used REF2VA.
            return position == 0
        if mode == "scene-cuts":
            return not shot.continuity_from_shot_id
        return mode == "every-shot"

    @staticmethod
    def _estimate_video_seconds(
        duration_seconds: float,
        steps: int,
        continuation_mode: ContinuationMode | None = None,
    ) -> float:
        # Measured on 8x MTT S5000 at 1280x704 with model-level CPU offload.
        reference_seconds = {
            None: 431.1,
            ContinuationMode.FAST: 635.0,
            ContinuationMode.QUALITY: 931.1,
        }[continuation_mode]
        denoise = reference_seconds * (steps / 50) * (duration_seconds / 15)
        fixed = 8.0
        return round(denoise + fixed, 1)
