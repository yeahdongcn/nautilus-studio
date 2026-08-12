from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class AssetKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"


class AssetRole(str, Enum):
    CHARACTER = "character"
    LOCATION = "location"
    PROP = "prop"
    STYLE = "style"
    START_FRAME = "start_frame"
    AUDIO = "audio"
    REFERENCE = "reference"


class ContinuationMode(str, Enum):
    """Creator-facing trade-off for extending an already rendered clip."""

    FAST = "fast"
    QUALITY = "quality"


class AssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("asset"))
    sha256: str
    original_name: str
    display_name: str = ""
    media_type: str
    kind: AssetKind
    size_bytes: int
    stored_path: str | None = None
    external_path: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    caption: str = ""
    tags: list[str] = Field(default_factory=list)
    roles: list[AssetRole] = Field(default_factory=lambda: [AssetRole.REFERENCE])
    source: Literal["upload", "path"] = "upload"
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def resolved_path(self) -> str:
        value = self.stored_path or self.external_path
        if not value:
            raise ValueError(f"asset {self.id} has no readable path")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().lower() for item in value if item.strip()})

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        return " ".join(value.split())


class AssetUpdate(BaseModel):
    display_name: str | None = None
    caption: str | None = None
    tags: list[str] | None = None
    roles: list[AssetRole] | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return sorted({item.strip().lower() for item in value if item.strip()})

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        return None if value is None else " ".join(value.split())


class AssetView(BaseModel):
    """Creator-facing asset metadata without server filesystem paths."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    sha256: str
    original_name: str
    display_name: str
    media_type: str
    kind: AssetKind
    size_bytes: int
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    caption: str
    tags: list[str]
    roles: list[AssetRole]
    source: Literal["upload", "path"]
    created_at: datetime


class ProjectBrief(BaseModel):
    title: str = "Untitled film"
    prompt: str = Field(min_length=3)
    duration_seconds: int = Field(default=60, ge=15, le=900)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    style: str = "cinematic realism"
    style_preset: str = "cinematic"
    style_instructions: str = ""
    language: str = "zh-CN"
    audience: str = "general"
    reference_asset_ids: list[str] = Field(default_factory=list)
    quality: Literal["draft", "final"] = "draft"
    subtitle_mode: Literal["none", "sidecar"] = "none"
    continuation_mode: ContinuationMode = ContinuationMode.FAST


class ContinuityState(BaseModel):
    characters: list[str] = Field(default_factory=list)
    wardrobe: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    location: str = ""
    lighting: str = ""
    camera: str = ""
    action: str = ""
    audio: str = ""


class WorldBible(BaseModel):
    logline: str
    visual_style: str
    character_notes: list[str] = Field(default_factory=list)
    location_notes: list[str] = Field(default_factory=list)
    prop_notes: list[str] = Field(default_factory=list)
    audio_notes: list[str] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)


class ShotTask(str, Enum):
    FL2VA = "fl2va"
    REF2VA = "ref2va"


class ShotStatus(str, Enum):
    PLANNED = "planned"
    READY = "ready"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"


class ShotSpec(BaseModel):
    id: str = Field(default_factory=lambda: new_id("shot"))
    index: int = Field(ge=0)
    title: str
    purpose: str
    duration_seconds: float = Field(ge=4, le=15)
    task: ShotTask = ShotTask.FL2VA
    prompt: str
    negative_prompt: str = ""
    subtitle_text: str | None = None
    camera: str = "medium shot, stable cinematic camera"
    reference_asset_ids: list[str] = Field(default_factory=list)
    start_frame_asset_id: str | None = None
    audio_asset_id: str | None = None
    continuity_from_shot_id: str | None = None
    continuation_mode: ContinuationMode | None = None
    continuity_in: ContinuityState = Field(default_factory=ContinuityState)
    continuity_out: ContinuityState = Field(default_factory=ContinuityState)
    seed: int = 42
    fps: int = 24
    inference_steps: int = 12
    flow_shift: float = 12.0
    status: ShotStatus = ShotStatus.PLANNED
    selected_take_path: str | None = None
    anchor_frame_path: str | None = None
    anchor_prompt: str | None = None
    boundary_frame_path: str | None = None

    @model_validator(mode="after")
    def validate_reference_contract(self) -> ShotSpec:
        if self.start_frame_asset_id and self.start_frame_asset_id not in self.reference_asset_ids:
            self.reference_asset_ids.insert(0, self.start_frame_asset_id)
        return self


def resolved_continuation_mode(project: FilmProject, shot: ShotSpec) -> ContinuationMode:
    """Resolve a per-shot override against the project's creator choice."""

    return shot.continuation_mode or project.brief.continuation_mode


def effective_video_task(
    shot: ShotSpec,
    *,
    ref2va_configured: bool,
    fl2va_configured: bool,
) -> ShotTask:
    """Select the runtime task without changing the storyboard's creative IR.

    FL2VA remains the internal compatibility fallback when it is the only
    configured continuation backend. A creator-selected start frame always
    forces FL2VA because that explicit composition must not be replaced by a
    previous-video reference.
    """

    if shot.start_frame_asset_id:
        return ShotTask.FL2VA
    is_generated_clip_continuation = bool(shot.continuity_from_shot_id)
    if not is_generated_clip_continuation:
        return shot.task
    if shot.task == ShotTask.REF2VA or ref2va_configured:
        return ShotTask.REF2VA
    if fl2va_configured:
        return ShotTask.FL2VA
    # Ref2VA is the intended continuation path. Returning it when neither
    # endpoint is configured makes preflight name the missing primary backend
    # instead of silently presenting FL2VA as the normal route.
    return ShotTask.REF2VA


class TimelineClip(BaseModel):
    shot_id: str
    start_seconds: float
    duration_seconds: float


class FilmProject(BaseModel):
    id: str = Field(default_factory=lambda: new_id("project"))
    brief: ProjectBrief
    world_bible: WorldBible
    shots: list[ShotSpec]
    timeline: list[TimelineClip] = Field(default_factory=list)
    status: Literal["planned", "compiled", "rendering", "complete", "failed"] = "planned"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def build_timeline(self) -> FilmProject:
        cursor = 0.0
        self.timeline = []
        for index, shot in enumerate(sorted(self.shots, key=lambda item: item.index)):
            shot.index = index
            self.timeline.append(
                TimelineClip(
                    shot_id=shot.id,
                    start_seconds=cursor,
                    duration_seconds=shot.duration_seconds,
                )
            )
            cursor += shot.duration_seconds
        return self


class ModelCapability(BaseModel):
    id: str
    display_name: str
    task: str
    endpoint: str | None = None
    available: bool = False
    max_duration_seconds: float | None = None
    supports_audio: bool = False
    supports_multiple_references: bool = False
    recommended_gpus: int = 0
    notes: list[str] = Field(default_factory=list)


class ExecutionStage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("stage"))
    shot_id: str | None = None
    kind: Literal["keyframe", "video", "continuity_check", "assembly"]
    capability_id: str
    depends_on: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    estimated_seconds: float | None = None


class DeploymentRequest(BaseModel):
    capability_id: str
    endpoint: str | None = None
    recommended_gpus: int = 0
    shot_ids: list[str] = Field(default_factory=list)
    status: Literal["ready", "unconfigured"]
    rationale: str


class ExecutionPlan(BaseModel):
    project_id: str
    stages: list[ExecutionStage]
    deployments: list[DeploymentRequest] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    estimated_seconds: float


class RenderJob(BaseModel):
    id: str = Field(default_factory=lambda: new_id("job"))
    project_id: str
    status: Literal["queued", "running", "complete", "failed"] = "queued"
    progress: float = Field(default=0, ge=0, le=1)
    current_shot_id: str | None = None
    message: str = "queued"
    output_path: str | None = None
    subtitle_path: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
