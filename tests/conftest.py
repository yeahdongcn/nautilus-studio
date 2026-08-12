from __future__ import annotations

from pathlib import Path

import pytest

from long_video_studio.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    import_root = tmp_path / "imports"
    import_root.mkdir()
    value = Settings(
        data_dir=data_dir,
        database_path=data_dir / "studio.db",
        asset_dir=data_dir / "assets",
        output_dir=data_dir / "outputs",
        allowed_import_roots=(import_root,),
        copy_imported_assets=True,
        planner_base_url=None,
        planner_api_key=None,
        planner_model=None,
        planner_wire_api="chat_completions",
        planner_allow_fallback=True,
        planner_source="heuristic",
        h3_fl2va_url=None,
        h3_ref2va_url=None,
        h3_flow_shift=12.0,
        h3_timeout_seconds=30,
        transition_seconds=0.12,
        ffmpeg_binary="ffmpeg",
        ffprobe_binary="ffprobe",
    )
    value.ensure_directories()
    return value
