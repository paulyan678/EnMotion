"""Final-project export backed by FFmpeg."""

import os
import subprocess
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ...utils import get_logger
from ...utils.system_check import get_ffmpeg_install_instructions, get_ffmpeg_path
from .models import Script

logger = get_logger(__name__)


RESOLUTION_SIZES = {
    "source": None,
    "360p": (640, 360),
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2160p": (3840, 2160),
    "4k": (3840, 2160),
}
SUPPORTED_FORMATS = {"mp4", "webm"}
SUPPORTED_SUBTITLE_MODES = {"none", "sidecar", "embedded", "burn-in"}


class ExportManager:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.output_root = self.config.get("output_root", "output")
        self.output_dir = self.config.get(
            "output_dir",
            os.path.join(self.output_root, "export"),
        )
        if self.config.get("create_output_dir", True):
            os.makedirs(self.output_dir, exist_ok=True)

    def render_project(self, script: Script, options: Dict[str, Any]) -> str:
        """Transcode the merged project video using validated export options."""
        resolution, output_format, subtitle_mode = self._normalize_options(options)
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError(
                "FFmpeg is required for export but was not found.\n\n"
                + get_ffmpeg_install_instructions()
            )

        input_path = self._resolve_merged_video(script.merged_video_url)
        output_filename = f"export_{script.id}_{uuid.uuid4().hex[:12]}.{output_format}"
        output_path = os.path.join(self.output_dir, output_filename)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        subtitle_path: Optional[str] = None
        keep_subtitle = False
        export_succeeded = False
        command = [ffmpeg_path, "-y", "-i", input_path]
        video_filters = []

        target_size = RESOLUTION_SIZES[resolution]
        if target_size:
            width, height = target_size
            video_filters.append(
                "scale="
                f"{width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            )

        if subtitle_mode != "none":
            subtitle_entries = self._subtitle_entries(script)
            if not subtitle_entries:
                raise ValueError("Subtitles were requested but the project has no dialogue")
            if subtitle_mode == "burn-in" and not self._ffmpeg_supports_filter(
                ffmpeg_path, "subtitles"
            ):
                raise RuntimeError(
                    "This FFmpeg build does not include the subtitles filter required "
                    "for burn-in export"
                )
            subtitle_path = os.path.splitext(output_path)[0] + ".srt"
            try:
                self._write_srt(subtitle_path, subtitle_entries)
            except Exception:
                self._remove_file(subtitle_path)
                raise

            if subtitle_mode == "sidecar":
                keep_subtitle = True
            elif subtitle_mode == "embedded":
                command.extend(["-i", subtitle_path])
            elif subtitle_mode == "burn-in":
                video_filters.append(f"subtitles={self._escape_filter_path(subtitle_path)}")

        if video_filters:
            command.extend(["-vf", ",".join(video_filters)])

        if subtitle_mode == "embedded":
            command.extend(["-map", "0:v:0", "-map", "0:a?", "-map", "1:0"])
        else:
            # A request for no/sidecar/burn-in subtitles must not accidentally
            # retain a subtitle stream already present in the merged input.
            command.append("-sn")

        if output_format == "mp4":
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                ]
            )
            if subtitle_mode == "embedded":
                command.extend(["-c:s", "mov_text", "-metadata:s:s:0", "language=und"])
            command.extend(["-movflags", "+faststart"])
        else:
            command.extend(
                [
                    "-c:v",
                    "libvpx-vp9",
                    "-deadline",
                    "good",
                    "-cpu-used",
                    "2",
                    "-crf",
                    "30",
                    "-b:v",
                    "0",
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "128k",
                ]
            )

        command.append(output_path)
        logger.info(
            "Exporting project %s as %s/%s subtitles=%s",
            script.id,
            resolution,
            output_format,
            subtitle_mode,
        )

        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
                raise RuntimeError("FFmpeg export completed without creating a valid output file")
            logger.debug("FFmpeg export output: %s", result.stdout[-500:])
            export_succeeded = True
            return os.path.relpath(output_path, self.output_root).replace(os.sep, "/")
        except subprocess.TimeoutExpired:
            self._remove_file(output_path)
            raise RuntimeError("FFmpeg export timed out") from None
        except subprocess.CalledProcessError as exc:
            self._remove_file(output_path)
            logger.error("FFmpeg export failed: %s", (exc.stderr or "")[-1000:])
            raise RuntimeError("FFmpeg export failed; check the application logs") from None
        except Exception:
            self._remove_file(output_path)
            raise
        finally:
            if subtitle_path and (not keep_subtitle or not export_succeeded):
                self._remove_file(subtitle_path)

    @staticmethod
    def _normalize_options(options: Dict[str, Any]) -> Tuple[str, str, str]:
        values = options or {}
        resolution = str(values.get("resolution", "1080p")).strip().lower()
        output_format = str(values.get("format", "mp4")).strip().lower().lstrip(".")
        subtitle_mode = str(values.get("subtitles", "none")).strip().lower().replace("_", "-")

        if resolution not in RESOLUTION_SIZES:
            raise ValueError(
                f"Unsupported export resolution {resolution!r}; "
                f"choose one of {sorted(RESOLUTION_SIZES)}"
            )
        if output_format not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported export format {output_format!r}; "
                f"choose one of {sorted(SUPPORTED_FORMATS)}"
            )
        if subtitle_mode not in SUPPORTED_SUBTITLE_MODES:
            raise ValueError(
                f"Unsupported subtitle mode {subtitle_mode!r}; "
                f"choose one of {sorted(SUPPORTED_SUBTITLE_MODES)}"
            )
        if subtitle_mode == "embedded" and output_format != "mp4":
            raise ValueError("Embedded subtitles are currently supported only for MP4")
        return resolution, output_format, subtitle_mode

    def _resolve_merged_video(self, media_ref: Optional[str]) -> str:
        if not media_ref:
            raise ValueError("Project has no merged video to export")

        raw = media_ref.strip().replace("\\", "/")
        if raw.startswith(("http://", "https://", "blob:", "data:")):
            raise ValueError("Only local merged videos can be exported")
        raw = raw.lstrip("/")
        for prefix in ("files/", "output/", "outputs/"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
        if raw.startswith("videos/"):
            raw = "video/" + raw[len("videos/") :]

        output_root = os.path.realpath(self.output_root)
        resolved = os.path.realpath(os.path.join(output_root, raw))
        if not resolved.startswith(output_root + os.sep):
            raise ValueError("Merged video path escapes the output directory")
        if not os.path.isfile(resolved):
            raise ValueError("Merged video file does not exist")
        return resolved

    @staticmethod
    def _subtitle_entries(script: Script) -> List[Tuple[float, float, str]]:
        entries = []
        cursor = 0.0
        for frame in script.frames:
            duration = max(0.1, float(frame.duration or 5))
            structured = frame.dialogue_structured
            text = structured.line if structured else frame.dialogue
            speaker = structured.speaker if structured else frame.speaker
            if text and text.strip():
                clean_text = text.strip().replace("\r", "").replace("\x00", "")
                if speaker and speaker.strip():
                    speaker_text = speaker.strip()
                    already_labelled = clean_text.startswith(
                        (f"{speaker_text}:", f"{speaker_text}：")
                    )
                    if not already_labelled:
                        clean_text = f"{speaker_text}: {clean_text}"
                entries.append((cursor, cursor + duration, clean_text))
            cursor += duration
        return entries

    @classmethod
    def _write_srt(cls, path: str, entries: List[Tuple[float, float, str]]) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as subtitle_file:
            for index, (start, end, text) in enumerate(entries, start=1):
                subtitle_file.write(
                    f"{index}\n{cls._srt_timestamp(start)} --> {cls._srt_timestamp(end)}\n"
                    f"{text}\n\n"
                )

    @staticmethod
    def _srt_timestamp(seconds: float) -> str:
        milliseconds = max(0, round(seconds * 1000))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        whole_seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"

    @staticmethod
    def _ffmpeg_supports_filter(ffmpeg_path: str, filter_name: str) -> bool:
        try:
            result = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-filters"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        for line in result.stdout.splitlines():
            columns = line.split()
            if len(columns) >= 2 and columns[1] == filter_name:
                return True
        return False

    @staticmethod
    def _escape_filter_path(path: str) -> str:
        escaped = path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        return f"'{escaped}'"

    @staticmethod
    def _remove_file(path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass
