from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from long_video_studio.assets import AssetService
from long_video_studio.domain import AssetKind, AssetRole, AssetUpdate
from long_video_studio.repository import StudioRepository


def png_bytes(color: str = "tomato") -> BytesIO:
    stream = BytesIO()
    Image.new("RGB", (48, 32), color=color).save(stream, format="PNG")
    stream.seek(0)
    return stream


def test_upload_deduplicates_and_merges_metadata(settings):
    repository = StudioRepository(settings.database_path)
    service = AssetService(settings, repository)
    first = service.ingest_stream(
        png_bytes(),
        "hero.png",
        "image/png",
        tags=["Hero"],
        roles=[AssetRole.CHARACTER],
    )
    second = service.ingest_stream(
        png_bytes(),
        "duplicate.png",
        "image/png",
        tags=["warm"],
        roles=[AssetRole.STYLE],
    )
    assert first.id == second.id
    assert second.display_name == "hero"
    assert second.kind == AssetKind.IMAGE
    assert (second.width, second.height) == (48, 32)
    assert second.tags == ["hero", "warm"]
    assert {role.value for role in second.roles} == {"character", "style"}
    assert len(repository.list_assets()) == 1


def test_directory_import_search_and_update(settings):
    source = settings.allowed_import_roots[0] / "library"
    source.mkdir()
    path = source / "orange-cat.png"
    path.write_bytes(png_bytes("orange").read())
    service = AssetService(settings, StudioRepository(settings.database_path))
    imported = service.import_path(
        str(source),
        tags=["cat"],
        roles=[AssetRole.CHARACTER],
    )
    assert len(imported) == 1
    assert service.search("cat", kind=AssetKind.IMAGE)[0].id == imported[0].id
    updated = service.update(
        imported[0].id,
        AssetUpdate(
            display_name="Orange Cat",
            caption="orange cat protagonist",
            tags=["cat", "lead"],
        ),
    )
    assert updated.display_name == "Orange Cat"
    assert updated.caption == "orange cat protagonist"
    assert service.search("lead")[0].id == updated.id
    assert service.search("orange")[0].id == updated.id


def test_asset_update_replaces_roles_instead_of_merging(settings):
    repository = StudioRepository(settings.database_path)
    service = AssetService(settings, repository)
    asset = service.ingest_stream(
        png_bytes(),
        "hero.png",
        "image/png",
        roles=[AssetRole.REFERENCE],
    )

    updated = service.update(
        asset.id,
        AssetUpdate(roles=[AssetRole.CHARACTER]),
    )

    assert updated.roles == [AssetRole.CHARACTER]
    assert repository.get_asset(asset.id).roles == [AssetRole.CHARACTER]


def test_asset_update_normalizes_tags(settings):
    repository = StudioRepository(settings.database_path)
    service = AssetService(settings, repository)
    asset = service.ingest_stream(png_bytes(), "hero.png", "image/png")

    updated = service.update(
        asset.id,
        AssetUpdate(tags=[" Hero ", "hero", "LEAD", ""]),
    )

    assert updated.tags == ["hero", "lead"]
    assert repository.get_asset(asset.id).tags == ["hero", "lead"]


def test_import_rejects_path_outside_allowlist(settings, tmp_path: Path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(png_bytes().read())
    service = AssetService(settings, StudioRepository(settings.database_path))
    with pytest.raises(PermissionError):
        service.import_path(str(outside))
