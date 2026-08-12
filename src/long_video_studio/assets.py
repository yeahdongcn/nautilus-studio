from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from long_video_studio.config import Settings
from long_video_studio.domain import AssetKind, AssetRecord, AssetRole, AssetUpdate
from long_video_studio.repository import StudioRepository

SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".aac",
    ".txt",
    ".md",
    ".pdf",
}


def _asset_kind(media_type: str) -> AssetKind:
    family = media_type.split("/", 1)[0]
    if family in {"image", "video", "audio"}:
        return AssetKind(family)
    if family == "text" or media_type == "application/pdf":
        return AssetKind.DOCUMENT
    return AssetKind.OTHER


def _default_caption(filename: str) -> str:
    words = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return " ".join(words.split())


class AssetService:
    def __init__(self, settings: Settings, repository: StudioRepository):
        self.settings = settings
        self.repository = repository

    def resolve_content_path(self, asset: AssetRecord) -> Path:
        """Resolve an asset path without escaping configured media roots."""
        candidate = Path(asset.resolved_path).expanduser().resolve()
        roots = [self.settings.asset_dir.resolve()]
        if not asset.stored_path:
            roots.extend(root.resolve() for root in self.settings.allowed_import_roots)
        if not any(candidate == root or root in candidate.parents for root in roots):
            raise ValueError("asset path is outside configured media roots")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    def ingest_stream(
        self,
        stream: BinaryIO,
        filename: str,
        media_type: str | None = None,
        *,
        tags: Iterable[str] = (),
        roles: Iterable[AssetRole] = (AssetRole.REFERENCE,),
        source: str = "upload",
    ) -> AssetRecord:
        safe_name = Path(filename).name or "asset.bin"
        guessed_type = media_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        digest = hashlib.sha256()
        size = 0
        self.settings.asset_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.settings.asset_dir, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                temporary.write(chunk)
                size += len(chunk)

        sha256 = digest.hexdigest()
        existing = self.repository.get_asset_by_sha256(sha256)
        if existing:
            temporary_path.unlink(missing_ok=True)
            merged = existing.model_copy(
                update={
                    "tags": sorted(set(existing.tags) | {item for item in tags if item}),
                    "roles": list(dict.fromkeys([*existing.roles, *roles])),
                }
            )
            return self.repository.save_asset(merged)

        suffix = Path(safe_name).suffix.lower()
        destination_dir = self.settings.asset_dir / sha256[:2]
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{sha256}{suffix}"
        os.replace(temporary_path, destination)
        width, height, duration = self._probe(destination, guessed_type)
        asset = AssetRecord(
            sha256=sha256,
            original_name=safe_name,
            display_name=_default_caption(safe_name),
            media_type=guessed_type,
            kind=_asset_kind(guessed_type),
            size_bytes=size,
            stored_path=str(destination),
            width=width,
            height=height,
            duration_seconds=duration,
            caption=_default_caption(safe_name),
            tags=list(tags),
            roles=list(roles) or [AssetRole.REFERENCE],
            source="path" if source == "path" else "upload",
        )
        return self.repository.save_asset(asset)

    def import_path(
        self,
        raw_path: str,
        *,
        recursive: bool = True,
        copy_into_library: bool | None = None,
        tags: Iterable[str] = (),
        roles: Iterable[AssetRole] = (AssetRole.REFERENCE,),
        limit: int = 500,
    ) -> list[AssetRecord]:
        path = Path(raw_path).expanduser().resolve(strict=True)
        self._assert_allowed(path)
        copy_assets = self.settings.copy_imported_assets if copy_into_library is None else copy_into_library
        candidates = [path]
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            candidates = [
                candidate
                for candidate in iterator
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
        if len(candidates) > limit:
            raise ValueError(f"import matched {len(candidates)} files; limit is {limit}")

        imported: list[AssetRecord] = []
        for candidate in sorted(candidates):
            if copy_assets:
                with candidate.open("rb") as stream:
                    imported.append(
                        self.ingest_stream(
                            stream,
                            candidate.name,
                            mimetypes.guess_type(candidate.name)[0],
                            tags=tags,
                            roles=roles,
                            source="path",
                        )
                    )
            else:
                imported.append(self._reference_external(candidate, tags=tags, roles=roles))
        return imported

    def _reference_external(
        self,
        path: Path,
        *,
        tags: Iterable[str],
        roles: Iterable[AssetRole],
    ) -> AssetRecord:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        sha256 = digest.hexdigest()
        existing = self.repository.get_asset_by_sha256(sha256)
        if existing:
            return existing
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        width, height, duration = self._probe(path, media_type)
        return self.repository.save_asset(
            AssetRecord(
                sha256=sha256,
                original_name=path.name,
                display_name=_default_caption(path.name),
                media_type=media_type,
                kind=_asset_kind(media_type),
                size_bytes=size,
                external_path=str(path),
                width=width,
                height=height,
                duration_seconds=duration,
                caption=_default_caption(path.name),
                tags=list(tags),
                roles=list(roles) or [AssetRole.REFERENCE],
                source="path",
            )
        )

    def search(
        self,
        query: str = "",
        *,
        kind: AssetKind | None = None,
        role: AssetRole | None = None,
    ) -> list[AssetRecord]:
        terms = {term.lower() for term in query.split() if term.strip()}
        scored: list[tuple[int, AssetRecord]] = []
        for asset in self.repository.list_assets():
            if kind and asset.kind != kind:
                continue
            if role and role not in asset.roles:
                continue
            haystack = " ".join(
                [
                    asset.original_name,
                    asset.display_name,
                    asset.caption,
                    *asset.tags,
                    *(item.value for item in asset.roles),
                ]
            ).lower()
            score = sum(3 if term in asset.tags else 1 for term in terms if term in haystack)
            if terms and not score:
                continue
            scored.append((score, asset))
        return [asset for _, asset in sorted(scored, key=lambda pair: (pair[0], pair[1].created_at), reverse=True)]

    def update(self, asset_id: str, update: AssetUpdate) -> AssetRecord:
        asset = self.repository.get_asset(asset_id)
        if not asset:
            raise KeyError(asset_id)
        payload = update.model_dump(exclude_none=True)
        return self.repository.save_asset(asset.model_copy(update=payload))

    def delete(self, asset_id: str) -> bool:
        asset = self.repository.delete_asset(asset_id)
        if not asset:
            return False
        if asset.stored_path:
            Path(asset.stored_path).unlink(missing_ok=True)
        return True

    def _assert_allowed(self, path: Path) -> None:
        if not any(path == root or path.is_relative_to(root) for root in self.settings.allowed_import_roots):
            roots = ", ".join(str(root) for root in self.settings.allowed_import_roots)
            raise PermissionError(f"{path} is outside STUDIO_IMPORT_ROOTS ({roots})")

    def _probe(self, path: Path, media_type: str) -> tuple[int | None, int | None, float | None]:
        kind = _asset_kind(media_type)
        if kind == AssetKind.IMAGE:
            try:
                with Image.open(path) as image:
                    return image.width, image.height, None
            except OSError:
                return None, None, None
        if kind not in {AssetKind.VIDEO, AssetKind.AUDIO}:
            return None, None, None
        try:
            completed = subprocess.run(
                [
                    self.settings.ffprobe_binary,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_type,width,height",
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None, None, None
        payload = json.loads(completed.stdout)
        video_stream = next(
            (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
            {},
        )
        duration_value = payload.get("format", {}).get("duration")
        duration = float(duration_value) if duration_value else None
        return video_stream.get("width"), video_stream.get("height"), duration

    def resolve_paths(self, asset_ids: Iterable[str]) -> list[Path]:
        paths: list[Path] = []
        for asset_id in asset_ids:
            asset = self.repository.get_asset(asset_id)
            if not asset:
                raise KeyError(asset_id)
            paths.append(Path(asset.resolved_path))
        return paths
