from __future__ import annotations

from dataclasses import dataclass

from long_video_studio.assets import AssetService
from long_video_studio.compiler import FilmCompiler
from long_video_studio.config import Settings
from long_video_studio.planner import PlannerService
from long_video_studio.repository import StudioRepository


@dataclass
class StudioServices:
    settings: Settings
    repository: StudioRepository
    assets: AssetService
    planner: PlannerService
    compiler: FilmCompiler

    @classmethod
    def create(cls, settings: Settings) -> StudioServices:
        settings.ensure_directories()
        repository = StudioRepository(settings.database_path)
        return cls(
            settings=settings,
            repository=repository,
            assets=AssetService(settings, repository),
            planner=PlannerService(settings, repository),
            compiler=FilmCompiler(settings),
        )
