from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from long_video_studio.domain import AssetRecord, FilmProject, RenderJob, utc_now


class StudioRepository:
    """Small SQLite repository storing versionable domain objects as JSON."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
                """
            )

    @staticmethod
    def _dump(value: AssetRecord | FilmProject | RenderJob) -> str:
        return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))

    def save_asset(self, asset: AssetRecord) -> AssetRecord:
        existing = self.get_asset_by_sha256(asset.sha256)
        # A different ID with the same digest is an ingest-time duplicate, so
        # merge its metadata. Saving the same ID is an explicit edit and must
        # replace roles/tags instead of keeping stale values.
        if existing and existing.id != asset.id:
            merged = existing.model_copy(
                update={
                    "caption": asset.caption or existing.caption,
                    "tags": sorted(set(existing.tags) | set(asset.tags)),
                    "roles": list(dict.fromkeys([*existing.roles, *asset.roles])),
                }
            )
            asset = merged
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO assets(id, sha256, kind, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    sha256=excluded.sha256,
                    kind=excluded.kind,
                    payload=excluded.payload
                """,
                (
                    asset.id,
                    asset.sha256,
                    asset.kind.value,
                    asset.created_at.isoformat(),
                    self._dump(asset),
                ),
            )
        return asset

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return AssetRecord.model_validate_json(row["payload"]) if row else None

    def get_asset_by_sha256(self, sha256: str) -> AssetRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM assets WHERE sha256 = ?", (sha256,)).fetchone()
        return AssetRecord.model_validate_json(row["payload"]) if row else None

    def list_assets(self) -> list[AssetRecord]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM assets ORDER BY created_at DESC").fetchall()
        return [AssetRecord.model_validate_json(row["payload"]) for row in rows]

    def save_assets(self, assets: Iterable[AssetRecord]) -> list[AssetRecord]:
        return [self.save_asset(asset) for asset in assets]

    def delete_asset(self, asset_id: str) -> AssetRecord | None:
        asset = self.get_asset(asset_id)
        if not asset:
            return None
        with self._connect() as connection:
            connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        return asset

    def save_project(self, project: FilmProject) -> FilmProject:
        project.updated_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    payload=excluded.payload
                """,
                (
                    project.id,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    self._dump(project),
                ),
            )
        return project

    def get_project(self, project_id: str) -> FilmProject | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM projects WHERE id = ?", (project_id,)).fetchone()
        return FilmProject.model_validate_json(row["payload"]) if row else None

    def list_projects(self) -> list[FilmProject]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM projects ORDER BY updated_at DESC").fetchall()
        return [FilmProject.model_validate_json(row["payload"]) for row in rows]

    def save_job(self, job: RenderJob) -> RenderJob:
        job.updated_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs(id, project_id, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    payload=excluded.payload
                """,
                (
                    job.id,
                    job.project_id,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                    self._dump(job),
                ),
            )
        return job

    def get_job(self, job_id: str) -> RenderJob | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return RenderJob.model_validate_json(row["payload"]) if row else None

    def get_latest_job(self, project_id: str) -> RenderJob | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM jobs WHERE project_id = ? ORDER BY updated_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return RenderJob.model_validate_json(row["payload"]) if row else None
