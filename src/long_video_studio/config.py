from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def _split_paths(value: str) -> tuple[Path, ...]:
    return tuple(Path(item.strip()).expanduser().resolve() for item in value.split(":") if item.strip())


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _codex_planner_defaults() -> dict[str, Any]:
    if not _enabled("STUDIO_USE_CODEX_CONFIG"):
        return {}
    codex_home = Path(os.getenv("CODEX_HOME", Path.home() / ".codex")).expanduser()
    config_path = Path(os.getenv("STUDIO_CODEX_CONFIG", codex_home / "config.toml")).expanduser()
    if not config_path.is_file():
        raise FileNotFoundError(f"Codex config not found: {config_path}")
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    provider_name = payload.get("model_provider")
    providers = payload.get("model_providers") or {}
    provider = providers.get(provider_name) if isinstance(providers, dict) else None
    if not isinstance(provider, dict):
        raise ValueError(f"Codex model provider is not configured: {provider_name}")
    api_key = None
    env_key = provider.get("env_key")
    if isinstance(env_key, str):
        api_key = os.getenv(env_key)
    if not api_key:
        candidate = provider.get("experimental_bearer_token")
        if isinstance(candidate, str):
            api_key = candidate
    return {
        "base_url": provider.get("base_url"),
        "model": payload.get("model"),
        "wire_api": provider.get("wire_api") or "responses",
        "api_key": api_key,
        "source": f"codex:{provider_name}",
    }


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    asset_dir: Path
    output_dir: Path
    allowed_import_roots: tuple[Path, ...]
    copy_imported_assets: bool
    planner_base_url: str | None
    planner_api_key: str | None
    planner_model: str | None
    planner_wire_api: str
    planner_allow_fallback: bool
    planner_source: str
    h3_fl2va_url: str | None
    h3_ref2va_url: str | None
    h3_flow_shift: float
    h3_timeout_seconds: float
    transition_seconds: float
    ffmpeg_binary: str
    ffprobe_binary: str
    image_edit_provider: str = "disabled"
    image_edit_base_url: str | None = None
    image_edit_api_key: str | None = None
    image_edit_model: str | None = None
    image_edit_timeout_seconds: float = 600.0
    image_edit_max_references: int = 4
    image_edit_anchor_mode: str = "scene-cuts"
    image_edit_steps: int = 40
    image_edit_true_cfg_scale: float = 4.0
    image_edit_guidance_scale: float = 1.0
    render_estimate_scale: float = 1.0
    web_root: str | None = None

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> Settings:
        root = project_root or Path(__file__).resolve().parents[2]
        data_dir = Path(os.getenv("STUDIO_DATA_DIR", root / "data")).expanduser().resolve()
        roots_value = os.getenv("STUDIO_IMPORT_ROOTS", str(Path.home()))
        codex = _codex_planner_defaults()
        planner_base_url = os.getenv("STUDIO_PLANNER_BASE_URL") or codex.get("base_url")
        planner_model = os.getenv("STUDIO_PLANNER_MODEL") or codex.get("model")
        planner_api_key = os.getenv("STUDIO_PLANNER_API_KEY") or codex.get("api_key")
        planner_wire_api = os.getenv("STUDIO_PLANNER_WIRE_API") or codex.get("wire_api") or "chat_completions"
        planner_source = "env" if os.getenv("STUDIO_PLANNER_BASE_URL") else codex.get("source") or "heuristic"
        return cls(
            data_dir=data_dir,
            database_path=Path(os.getenv("STUDIO_DATABASE", data_dir / "studio.db")).expanduser().resolve(),
            asset_dir=Path(os.getenv("STUDIO_ASSET_DIR", data_dir / "assets")).expanduser().resolve(),
            output_dir=Path(os.getenv("STUDIO_OUTPUT_DIR", data_dir / "outputs")).expanduser().resolve(),
            allowed_import_roots=_split_paths(roots_value),
            copy_imported_assets=os.getenv("STUDIO_COPY_IMPORTED_ASSETS", "1") != "0",
            planner_base_url=planner_base_url or None,
            planner_api_key=planner_api_key or None,
            planner_model=planner_model or None,
            planner_wire_api=str(planner_wire_api),
            planner_allow_fallback=_enabled(
                "STUDIO_PLANNER_ALLOW_FALLBACK",
                default=not bool(codex),
            ),
            planner_source=str(planner_source),
            h3_fl2va_url=os.getenv("STUDIO_H3_FL2VA_URL") or None,
            h3_ref2va_url=os.getenv("STUDIO_H3_REF2VA_URL") or None,
            h3_flow_shift=float(os.getenv("STUDIO_H3_FLOW_SHIFT", "12.0")),
            h3_timeout_seconds=float(os.getenv("STUDIO_H3_TIMEOUT_SECONDS", "1800")),
            transition_seconds=float(os.getenv("STUDIO_TRANSITION_SECONDS", "0.12")),
            ffmpeg_binary=os.getenv("STUDIO_FFMPEG", "ffmpeg"),
            ffprobe_binary=os.getenv("STUDIO_FFPROBE", "ffprobe"),
            image_edit_provider=os.getenv("STUDIO_IMAGE_EDIT_PROVIDER", "disabled").strip().lower(),
            image_edit_base_url=os.getenv("STUDIO_IMAGE_EDIT_BASE_URL") or None,
            image_edit_api_key=os.getenv("STUDIO_IMAGE_EDIT_API_KEY") or None,
            image_edit_model=os.getenv("STUDIO_IMAGE_EDIT_MODEL") or None,
            image_edit_timeout_seconds=float(os.getenv("STUDIO_IMAGE_EDIT_TIMEOUT_SECONDS", "600")),
            image_edit_max_references=int(os.getenv("STUDIO_IMAGE_EDIT_MAX_REFERENCES", "4")),
            image_edit_anchor_mode=os.getenv("STUDIO_IMAGE_EDIT_ANCHOR_MODE", "scene-cuts").strip().lower(),
            image_edit_steps=int(os.getenv("STUDIO_IMAGE_EDIT_STEPS", "40")),
            image_edit_true_cfg_scale=float(os.getenv("STUDIO_IMAGE_EDIT_TRUE_CFG_SCALE", "4.0")),
            image_edit_guidance_scale=float(os.getenv("STUDIO_IMAGE_EDIT_GUIDANCE_SCALE", "1.0")),
            render_estimate_scale=max(
                0.1,
                float(os.getenv("STUDIO_RENDER_ESTIMATE_SCALE", "1.0")),
            ),
            web_root=os.getenv("STUDIO_WEB_ROOT") or None,
        )

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.asset_dir, self.output_dir):
            path.mkdir(parents=True, exist_ok=True)
