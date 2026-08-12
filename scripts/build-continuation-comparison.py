#!/usr/bin/env python3
"""Build a labeled, audio-preserving A/B/C continuation comparison.

The script deliberately keeps the original candidate files byte-for-byte in
the output directory and performs all visual normalization on derived files.
It is useful for comparing continuation strategies without changing the
source videos produced by a model server.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Candidate:
    key: str
    source: Path
    label: str


@dataclass(frozen=True)
class Tooling:
    ffmpeg: Path
    ffprobe: Path


def _absolute_existing(path_value: str, description: str) -> Path:
    path = Path(path_value).expanduser()
    path = (Path.cwd() / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file():
        raise ValueError(f"{description} does not exist or is not a file: {path}")
    return path


def _absolute_output(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    path = (Path.cwd() / path).resolve() if not path.is_absolute() else path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_tool(explicit: str | None, env_name: str, wrapper_name: str, binary_name: str) -> Path:
    """Resolve a tool to an absolute executable path.

    ``STUDIO_FFMPEG``/``STUDIO_FFPROBE`` may point to either a binary or the
    repository's container wrapper.  A local executable wins when no explicit
    value is supplied; the wrapper is the documented fallback for hosts that
    do not install media tools.
    """

    value = explicit or os.getenv(env_name)
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_absolute() or "/" in value:
            candidate = candidate if candidate.is_absolute() else (Path.cwd() / candidate)
            candidate = candidate.resolve()
        else:
            resolved = shutil.which(value)
            if resolved is None:
                raise ValueError(f"{env_name} points to an unavailable executable: {value}")
            candidate = Path(resolved).resolve()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise ValueError(f"media tool is not executable: {candidate}")
        return candidate

    local = shutil.which(binary_name)
    if local:
        return Path(local).resolve()

    wrapper = (SCRIPT_DIR / wrapper_name).resolve()
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        return wrapper
    raise ValueError(f"could not find {binary_name}; install it, set {env_name}, or make {wrapper} available")


def _run(command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    print("[comparison]", shlex.join(str(item) for item in command), flush=True)
    return subprocess.run(command, check=True, text=True, timeout=timeout)


def _probe(tool: Path, path: Path) -> dict[str, Any]:
    command = [
        str(tool),
        "-hide_banner",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path.resolve()),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}: {result.stdout!r}") from error


def _video_stream(metadata: dict[str, Any], path: Path) -> dict[str, Any]:
    for stream in metadata.get("streams", []):
        if stream.get("codec_type") == "video":
            try:
                width = int(stream["width"])
                height = int(stream["height"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError(f"video stream has no usable dimensions: {path}") from error
            if width < 2 or height < 2:
                raise RuntimeError(f"video stream dimensions are invalid: {path}")
            return stream
    raise RuntimeError(f"no video stream found: {path}")


def _has_audio(metadata: dict[str, Any]) -> bool:
    return any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", []))


def _duration(metadata: dict[str, Any], video: dict[str, Any]) -> float:
    for value in (video.get("duration"), (metadata.get("format") or {}).get("duration")):
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    raise RuntimeError("ffprobe did not report a positive duration")


def _escape_filter_text(value: str) -> str:
    # drawtext parses these characters even when the filter is passed as one
    # argv element (there is no shell layer to do this escaping for us).
    return value.replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,").replace("'", "\\'")


def _escape_filter_path(value: Path) -> str:
    return _escape_filter_text(str(value.resolve()))


def _font_file() -> Path | None:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path.resolve()
    fc_match = shutil.which("fc-match")
    if fc_match:
        try:
            result = subprocess.run(
                [fc_match, "-f", "%{file}", "Sans:style=Bold"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        candidate = Path(result.stdout.strip())
        if candidate.is_file():
            return candidate.resolve()
    return None


def _make_label_filter(label: str, width: int, height: int, label_seconds: float) -> str:
    label_height = max(64, min(height, int(height * 0.13)))
    font_size = max(22, min(52, int(label_height * 0.42)))
    text = _escape_filter_text(label)
    font = _font_file()
    font_arg = f":fontfile={_escape_filter_path(font)}" if font else ":font='Sans'"
    enable = f"between(t,0,{label_seconds:.6f})"
    return ",".join(
        (
            f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease:flags=lanczos",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
            "setsar=1",
            "format=yuv420p",
            f"drawbox=x=0:y=0:w=iw:h={label_height}:color=black@0.74:t=fill:enable='{enable}'",
            f"drawtext=text='{text}'{font_arg}:fontcolor=white:fontsize={font_size}:x=28:y=({label_height}-text_h)/2:enable='{enable}'",
            "setpts=PTS-STARTPTS[outv]",
        )
    )


def _preserve_source(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination.resolve():
        return "same-path"
    destination.unlink(missing_ok=True)
    shutil.copy2(source, destination)
    return "copy"


def _render_labeled_segment(
    tools: Tooling,
    candidate: Candidate,
    destination: Path,
    metadata: dict[str, Any],
    width: int,
    height: int,
    label_seconds: float,
) -> None:
    video = _video_stream(metadata, candidate.source)
    duration = _duration(metadata, video)
    has_audio = _has_audio(metadata)
    command: list[str] = [
        str(tools.ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(candidate.source.resolve()),
    ]
    audio_input = 0
    if not has_audio:
        # Keep the comparison A/V aligned even for a silent candidate.  The
        # resulting AAC track is explicit in the manifest as synthesized audio.
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                f"{duration:.6f}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        )
        audio_input = 1
    command.extend(
        [
            "-filter_complex",
            _make_label_filter(candidate.label, width, height, label_seconds),
            "-map",
            "[outv]",
            "-map",
            f"{audio_input}:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-af",
            "aresample=async=1:first_pts=0",
            "-shortest",
            "-movflags",
            "+faststart",
            "-y",
            str(destination.resolve()),
        ]
    )
    _run(command, timeout=max(900.0, duration * 60.0))


def _concat_segments(tools: Tooling, segments: list[Path], destination: Path) -> None:
    list_path = destination.with_name(f".{destination.stem}.concat.txt")
    list_path.write_text("".join(f"file '{_concat_quote(path)}'\n" for path in segments), encoding="utf-8")
    try:
        command = [
            str(tools.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path.resolve()),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(destination.resolve()),
        ]
        _run(command, timeout=900)
    finally:
        list_path.unlink(missing_ok=True)


def _concat_quote(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def _artifact_record(tools: Tooling, path: Path, *, kind: str) -> dict[str, Any]:
    metadata = _probe(tools.ffprobe, path)
    video = _video_stream(metadata, path)
    audio_streams = [stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "audio"]
    return {
        "path": str(path.resolve()),
        "kind": kind,
        "bytes": path.stat().st_size,
        "duration_seconds": _duration(metadata, video),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "video_codec": video.get("codec_name"),
        "audio_present": bool(audio_streams),
        "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
        "ffprobe": metadata,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preserve three A/B/C MP4 candidates, create a padded labeled sequential comparison video, "
            "and write an ffprobe manifest."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("a", metavar="A_MP4", help="A candidate MP4")
    parser.add_argument("b", metavar="B_MP4", help="B candidate MP4")
    parser.add_argument("c", metavar="C_MP4", help="C candidate MP4")
    parser.add_argument("output_dir", metavar="OUTPUT_DIR", help="directory for comparison artifacts")
    parser.add_argument("--label-a", default="A | boundary frame -> FL2VA", help="label burned into A's segment")
    parser.add_argument("--label-b", default="B | full clip -> Ref2VA", help="label burned into B's segment")
    parser.add_argument("--label-c", default="C | tail 5s -> Ref2VA", help="label burned into C's segment")
    parser.add_argument(
        "--label-seconds",
        type=float,
        default=1.5,
        help="how long each method label remains on screen",
    )
    parser.add_argument("--width", type=int, help="comparison canvas width; defaults to A's video width")
    parser.add_argument("--height", type=int, help="comparison canvas height; defaults to A's video height")
    parser.add_argument("--ffmpeg", help="absolute ffmpeg binary or repository wrapper")
    parser.add_argument("--ffprobe", help="absolute ffprobe binary or repository wrapper")
    return parser


def build(args: argparse.Namespace) -> Path:
    if args.label_seconds <= 0:
        raise ValueError("--label-seconds must be positive")
    if (args.width is None) != (args.height is None):
        raise ValueError("--width and --height must be supplied together")
    if args.width is not None and (args.width < 2 or args.height < 2):
        raise ValueError("canvas dimensions must be at least 2x2")

    candidates = [
        Candidate("A", _absolute_existing(args.a, "A candidate"), args.label_a),
        Candidate("B", _absolute_existing(args.b, "B candidate"), args.label_b),
        Candidate("C", _absolute_existing(args.c, "C candidate"), args.label_c),
    ]
    output_dir = _absolute_output(args.output_dir)
    tools = Tooling(
        _resolve_tool(args.ffmpeg, "STUDIO_FFMPEG", "ffmpeg-container-wrapper.sh", "ffmpeg"),
        _resolve_tool(args.ffprobe, "STUDIO_FFPROBE", "ffprobe-container-wrapper.sh", "ffprobe"),
    )

    source_metadata = {candidate.key: _probe(tools.ffprobe, candidate.source) for candidate in candidates}
    first_video = _video_stream(source_metadata["A"], candidates[0].source)
    width = args.width or int(first_video["width"])
    height = args.height or int(first_video["height"])
    # yuv420p and libx264 need even dimensions.  Rounding down keeps the
    # selected canvas and never stretches or crops an input frame.
    width -= width % 2
    height -= height % 2
    if width < 2 or height < 2:
        raise ValueError("canvas dimensions must resolve to at least 2x2 even pixels")

    records: list[dict[str, Any]] = []
    segments: list[Path] = []
    for candidate in candidates:
        preserved = output_dir / f"candidate-{candidate.key}-original.mp4"
        preservation_mode = _preserve_source(candidate.source, preserved)
        labeled = output_dir / f"candidate-{candidate.key}-labeled.mp4"
        labeled.unlink(missing_ok=True)
        _render_labeled_segment(
            tools,
            candidate,
            labeled,
            source_metadata[candidate.key],
            width,
            height,
            args.label_seconds,
        )
        segments.append(labeled)
        records.append(
            {
                "id": candidate.key,
                "method_label": candidate.label,
                "source": _artifact_record(tools, candidate.source, kind="source"),
                "preserved_original": {
                    **_artifact_record(tools, preserved, kind="preserved-original"),
                    "preservation_mode": preservation_mode,
                },
                "labeled_segment": _artifact_record(tools, labeled, kind="labeled-segment"),
            }
        )

    comparison = output_dir / "continuation-ABC-comparison.mp4"
    comparison.unlink(missing_ok=True)
    _concat_segments(tools, segments, comparison)

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": " ".join(shlex.quote(item) for item in [str(Path(__file__).resolve()), *sys.argv[1:]]),
        "tools": {"ffmpeg": str(tools.ffmpeg), "ffprobe": str(tools.ffprobe)},
        "canvas": {
            "width": width,
            "height": height,
            "fit": "scale-down-or-up-preserve-aspect-pad-black",
            "source_for_default": "A video stream" if args.width is None else "explicit CLI dimensions",
        },
        "label_seconds": args.label_seconds,
        "candidates": records,
        "comparison_video": _artifact_record(tools, comparison, kind="sequential-comparison"),
    }
    manifest_path = output_dir / "continuation-ABC-comparison.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[comparison] wrote {comparison.resolve()}", flush=True)
    print(f"[comparison] wrote {manifest_path.resolve()}", flush=True)
    return comparison


def main() -> int:
    args = _parser().parse_args()
    try:
        build(args)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"build-continuation-comparison: error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
