from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageOps


class MediaTools:
    def __init__(self, ffmpeg_binary: str = "ffmpeg", ffprobe_binary: str = "ffprobe"):
        self.ffmpeg_binary = ffmpeg_binary
        self.ffprobe_binary = ffprobe_binary

    @staticmethod
    def fit_image_to_canvas(source: Path, output: Path, width: int, height: int) -> Path:
        """Center-crop an image to a canvas without geometric stretching."""

        output.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            fitted = ImageOps.fit(
                image,
                (width, height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            fitted.save(output, format="PNG")
        return output

    def extract_last_stable_frame(self, video_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                self.ffmpeg_binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-sseof",
                "-0.25",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-y",
                str(output_path),
            ],
            check=True,
            timeout=120,
        )
        return output_path

    def extract_tail(
        self,
        video_path: Path,
        output_path: Path,
        duration_seconds: float = 5.0,
    ) -> Path:
        """Create a tail reference of up to the requested duration with video and audio."""

        if duration_seconds <= 0:
            raise ValueError("tail duration must be positive")
        probe = subprocess.run(
            [
                self.ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        source_duration = float(json.loads(probe.stdout)["format"]["duration"])
        if source_duration <= 0:
            raise ValueError("source video has no positive duration")
        start_seconds = max(0.0, source_duration - duration_seconds)
        tail_duration = source_duration - start_seconds
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                self.ffmpeg_binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-ss",
                f"{start_seconds:.6f}",
                "-t",
                f"{tail_duration:.6f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
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
                "-movflags",
                "+faststart",
                "-y",
                str(output_path),
            ],
            check=True,
            timeout=600,
        )
        return output_path

    def concatenate(
        self,
        videos: list[Path],
        output_path: Path,
        transition_seconds: float = 0.0,
        continuous_boundaries: list[bool] | None = None,
    ) -> Path:
        if not videos:
            raise ValueError("cannot concatenate an empty video list")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        boundaries = [True] * (len(videos) - 1) if continuous_boundaries is None else continuous_boundaries
        use_transition = (
            transition_seconds > 0 and len(videos) > 1 and len(boundaries) == len(videos) - 1 and any(boundaries)
        )
        if use_transition:
            try:
                self._concatenate_with_transitions(videos, output_path, transition_seconds, boundaries)
                return output_path
            except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError):
                # Preserve a usable demo result if an optional ffprobe/filter
                # capability is unavailable on a host.
                pass
        return self._concatenate_copy(videos, output_path)

    def _concatenate_copy(self, videos: list[Path], output_path: Path) -> Path:
        list_path = output_path.with_suffix(".concat.txt")
        list_path.write_text(
            "".join(f"file '{self._quote(path.resolve())}'\n" for path in videos),
            encoding="utf-8",
        )
        try:
            subprocess.run(
                [
                    self.ffmpeg_binary,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(output_path),
                ],
                check=True,
                timeout=600,
            )
        finally:
            list_path.unlink(missing_ok=True)
        return output_path

    def _concatenate_with_transitions(
        self,
        videos: list[Path],
        output_path: Path,
        transition_seconds: float,
        continuous_boundaries: list[bool] | None = None,
    ) -> None:
        """Join clips without replaying the anchor at each continuous cut.

        The old implementation chained multiple ``xfade`` filters.  With the
        H3 clips' slightly different audio/video durations, later xfade
        inputs could be held on their last frame for an entire clip while the
        corresponding audio continued.  Since the next clip already starts
        from the preceding clip's boundary frame, trimming that duplicate
        lead-in and concatenating normalized streams is both deterministic and
        visually continuous.
        """
        durations: list[float] = []
        for video in videos:
            probe = subprocess.run(
                [
                    self.ffprobe_binary,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_type",
                    "-of",
                    "json",
                    str(video),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            metadata = json.loads(probe.stdout)
            if not any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", [])):
                raise ValueError("continuous transition requires an audio stream")
            duration = float(metadata["format"]["duration"])
            if duration <= transition_seconds:
                raise ValueError("clip is shorter than transition duration")
            durations.append(duration)

        boundaries = continuous_boundaries
        if boundaries is None:
            boundaries = [True] * (len(videos) - 1)
        if len(boundaries) != len(videos) - 1:
            raise ValueError("continuous_boundaries must have one entry per boundary")

        filters: list[str] = []
        for index in range(len(videos)):
            trim_seconds = transition_seconds if index > 0 and boundaries[index - 1] else 0.0
            filters.append(
                f"[{index}:v]trim=start={trim_seconds:.6f},fps=24,format=yuv420p,setpts=PTS-STARTPTS[v{index}]"
            )
            filters.append(
                f"[{index}:a]atrim=start={trim_seconds:.6f},asetpts=PTS-STARTPTS,"
                f"aresample=async=1,aformat=sample_rates=32000:channel_layouts=stereo[a{index}]"
            )

        concat_inputs = "".join(f"[v{index}][a{index}]" for index in range(len(videos)))
        filters.append(f"{concat_inputs}concat=n={len(videos)}:v=1:a=1[vout][aout]")

        command = [
            self.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        for video in videos:
            command.extend(["-i", str(video)])
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[vout]",
                "-map",
                "[aout]",
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
                "-movflags",
                "+faststart",
                "-y",
                str(output_path),
            ]
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(command, check=True, timeout=900)

    @staticmethod
    def _quote(path: Path) -> str:
        return str(path).replace("'", "'\\''")
