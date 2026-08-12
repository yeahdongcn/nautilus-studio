from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from long_video_studio.adapters.media import MediaTools
from long_video_studio.domain import FilmProject, ProjectBrief, ShotSpec, WorldBible
from long_video_studio.runner import RenderManager


def test_fit_image_to_canvas_crops_without_stretching(tmp_path):
    source = tmp_path / "portrait.png"
    image = Image.new("RGB", (100, 200), "green")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 99, 49), fill="red")
    draw.rectangle((0, 150, 99, 199), fill="blue")
    image.save(source)

    output = MediaTools.fit_image_to_canvas(source, tmp_path / "landscape.png", 128, 72)

    with Image.open(output) as fitted:
        assert fitted.size == (128, 72)
        assert fitted.getpixel((64, 36)) == (0, 128, 0)
        assert fitted.getpixel((64, 0)) == (0, 128, 0)


def test_project_aspect_ratio_maps_to_h3_canvas():
    assert RenderManager._video_canvas("16:9") == (1280, 704)
    assert RenderManager._video_canvas("9:16") == (704, 1280)
    assert RenderManager._video_canvas("1:1") == (960, 960)


def test_extract_tail_preserves_video_and_optional_audio_streams(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout=json.dumps({"format": {"duration": "12.25"}}))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    output = tmp_path / "tail.mp4"

    result = MediaTools("ffmpeg", "ffprobe").extract_tail(
        tmp_path / "source.mp4",
        output,
        5.0,
    )

    assert result == output
    ffmpeg = commands[-1]
    assert ffmpeg[ffmpeg.index("-ss") + 1] == "7.250000"
    assert ffmpeg[ffmpeg.index("-t") + 1] == "5.000000"
    assert "0:v:0" in ffmpeg
    assert "0:a:0?" in ffmpeg
    assert ffmpeg[ffmpeg.index("-c:v") + 1] == "libx264"
    assert ffmpeg[ffmpeg.index("-c:a") + 1] == "aac"


def test_extract_tail_rejects_non_positive_duration(tmp_path):
    tools = MediaTools("ffmpeg", "ffprobe")

    try:
        tools.extract_tail(tmp_path / "source.mp4", tmp_path / "tail.mp4", 0)
    except ValueError as error:
        assert str(error) == "tail duration must be positive"
    else:
        raise AssertionError("non-positive tail duration should fail")


def test_continuous_concatenation_drops_repeated_anchor_and_joins_streams(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            return SimpleNamespace(
                stdout=json.dumps({"format": {"duration": "4.0"}, "streams": [{"codec_type": "audio"}]})
            )
        calls.append(command)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tools = MediaTools("ffmpeg", "ffprobe")
    tools.concatenate(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"],
        tmp_path / "final.mp4",
        transition_seconds=0.12,
        continuous_boundaries=[True],
    )

    command = calls[-1]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "trim=start=0.12" in filter_graph
    assert "atrim=start=0.12" in filter_graph
    assert "concat=n=2:v=1:a=1" in filter_graph
    assert command[command.index("-map") + 1] == "[vout]"


def test_continuous_concatenation_keeps_motion_through_every_clip(tmp_path):
    """A multi-clip assembly must not repeat a frozen intermediate clip."""
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return
    clips: list[Path] = []
    for index, color in enumerate(("red", "green", "blue")):
        clip = tmp_path / f"clip-{index}.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=96x64:r=24:d=1.2",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=32000:cl=stereo",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-y",
                str(clip),
            ],
            check=True,
        )
        clips.append(clip)

    output = tmp_path / "assembled.mp4"
    MediaTools(ffmpeg, ffprobe).concatenate(
        clips,
        output,
        transition_seconds=0.12,
        continuous_boundaries=[True, True],
    )
    frame_count = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert int(frame_count) >= 70


def test_sidecar_subtitles_are_external_srt(tmp_path):
    project = FilmProject(
        brief=ProjectBrief(prompt="demo", duration_seconds=15, subtitle_mode="sidecar"),
        world_bible=WorldBible(logline="demo", visual_style="realistic"),
        shots=[
            ShotSpec(
                index=0,
                duration_seconds=7.5,
                title="a",
                purpose="a",
                prompt="a",
                subtitle_text="第一句",
            ),
            ShotSpec(
                index=1,
                duration_seconds=7.5,
                title="b",
                purpose="b",
                prompt="b",
                subtitle_text="第二句",
            ),
        ],
    )

    output = RenderManager._write_sidecar_subtitles(project, tmp_path)

    assert output == tmp_path / "final.srt"
    assert output.read_text(encoding="utf-8").startswith("1\n00:00:00,000 --> 00:00:07,500")
