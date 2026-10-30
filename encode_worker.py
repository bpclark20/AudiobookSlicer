"""Background encode worker for AudioBookSlicer."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from wav_markers_to_mp3 import (
    Chapter,
    encode_output,
    ensure_executable,
    format_bytes,
    probe_audio_info,
    read_riff_chunks,
    resolve_cover_images,
)


class EncodeWorker(QObject):
    progress = pyqtSignal(int, str, float)
    finished = pyqtSignal(str, int, object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        input_wav: Path,
        output_mp3: Path,
        chapters: list[Chapter],
        channels: int,
        vbr_quality: int,
        cbr_bitrate: str | None,
        source_files: dict[int, Path] | None = None,
        cover_map_override: dict[str, Path] | None = None,
        reuse_intermediate_wav: Path | None = None,
    ) -> None:
        super().__init__()
        self._input_wav = input_wav
        self._output_mp3 = output_mp3
        self._chapters = list(chapters)
        self._channels = channels
        self._vbr_quality = vbr_quality
        self._cbr_bitrate = cbr_bitrate
        self._source_files = source_files or {}
        self._cover_map_override = cover_map_override or {}
        self._reuse_intermediate_wav = reuse_intermediate_wav
        self._cancel_requested = False
        self._active_encode_process: subprocess.Popen[str] | None = None

    def request_cancel(self) -> None:
        self._cancel_requested = True
        self._force_stop_active_encode_process()

    def _set_active_encode_process(self, process: subprocess.Popen[str]) -> None:
        self._active_encode_process = process

    def _clear_active_encode_process(self) -> None:
        self._active_encode_process = None

    def _force_stop_active_encode_process(self) -> None:
        process = self._active_encode_process
        if process is None:
            return
        if process.poll() is not None:
            self._active_encode_process = None
            return

        if sys.platform == "win32":
            pid = int(process.pid)
            if pid > 0:
                run_kwargs: dict[str, Any] = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        check=False,
                        **run_kwargs,
                    )
                    return
                except OSError:
                    pass

        process.kill()

    def _run_ffmpeg_cancellable(self, cmd: list[str], failure_prefix: str) -> None:
        run_kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        with tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(cmd, stderr=stderr_file, **run_kwargs)
            self._set_active_encode_process(process)
            try:
                while True:
                    if self._cancel_requested:
                        self._force_stop_active_encode_process()
                        raise SystemExit
                    try:
                        return_code = process.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                self._clear_active_encode_process()

            stderr_file.seek(0)
            stderr_text = stderr_file.read().decode("utf-8", errors="replace").strip()

        if return_code != 0:
            raise SystemExit(
                f"{failure_prefix} "
                f"ffmpeg stderr: {stderr_text or '(empty)'}"
            )

    def _concatenate_individual_files(
        self,
        ffmpeg_bin: str,
        chapters: list[Chapter],
    ) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
        """Concatenate individual WAV files into one intermediate WAV."""
        temp_dir = tempfile.TemporaryDirectory(prefix="wav_concat_")
        output_wav = Path(temp_dir.name) / "concatenated.wav"

        # Build concat demuxer file
        concat_file = Path(temp_dir.name) / "concat.txt"
        concat_lines = []
        for row in range(len(chapters)):
            if row in self._source_files:
                concat_lines.append(f"file '{self._source_files[row]}'")
        
        concat_file.write_text("\n".join(concat_lines))

        # Run ffmpeg to concatenate
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(output_wav),
        ]
        self._run_ffmpeg_cancellable(cmd, "WAV concatenation failed.")

        return output_wav, temp_dir

    def _build_reordered_wav(
        self,
        ffmpeg_bin: str,
        input_wav: Path,
        chapters: list[Chapter],
    ) -> tuple[Path, list[Chapter], float, tempfile.TemporaryDirectory[str]]:
        temp_dir = tempfile.TemporaryDirectory(prefix="wav_reorder_")
        reordered_wav = Path(temp_dir.name) / "reordered.wav"

        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        for idx, ch in enumerate(chapters):
            start_sec = f"{ch.start_ms / 1000.0:.6f}"
            end_sec = f"{ch.end_ms / 1000.0:.6f}"
            label = f"c{idx}"
            filter_parts.append(
                f"[0:a]atrim=start={start_sec}:end={end_sec},asetpts=PTS-STARTPTS[{label}]"
            )
            concat_inputs.append(f"[{label}]")

        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={len(chapters)}:v=0:a=1[outa]"
        )
        filter_complex = ";".join(filter_parts)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_wav),
            "-filter_complex",
            filter_complex,
            "-map",
            "[outa]",
            "-c:a",
            "pcm_s16le",
            str(reordered_wav),
        ]
        self._run_ffmpeg_cancellable(cmd, "WAV chapter reordering failed.")

        remapped = self._sequential_timeline(chapters)
        total_sec = remapped[-1].end_ms / 1000.0 if remapped else 0.0
        return reordered_wav, remapped, total_sec, temp_dir

    @staticmethod
    def _sequential_timeline(chapters: list[Chapter]) -> list[Chapter]:
        remapped: list[Chapter] = []
        cursor_ms = 0
        for idx, ch in enumerate(chapters, start=1):
            seg_dur_ms = max(1, ch.end_ms - ch.start_ms)
            start_ms = cursor_ms
            end_ms = start_ms + seg_dur_ms
            remapped.append(
                Chapter(index=idx, title=ch.title, start_ms=start_ms, end_ms=end_ms)
            )
            cursor_ms = end_ms
        return remapped

    def _cleanup_tempdir_with_retries(
        self,
        temp_dir: tempfile.TemporaryDirectory[str] | None,
    ) -> None:
        if temp_dir is None:
            return

        # On Windows, force-killing ffmpeg can leave file handles open briefly.
        # Retry cleanup to avoid noisy PermissionError tracebacks from weakref finalizers.
        last_err: OSError | None = None
        for _ in range(20):
            try:
                temp_dir.cleanup()
                return
            except FileNotFoundError:
                return
            except PermissionError as exc:
                last_err = exc
                time.sleep(0.1)
            except OSError as exc:
                last_err = exc
                break

        if last_err is not None:
            try:
                self.progress.emit(
                    0,
                    f"Cleanup warning: temporary files could not be fully removed ({last_err})",
                    -1.0,
                )
            except Exception:
                pass

    def _copy_intermediate_for_reuse(self, source_wav: Path) -> Path | None:
        """Copy intermediate WAV to a stable temporary path for optional reuse."""
        target_fd, target_str = tempfile.mkstemp(
            prefix="audiobookslicer_intermediate_",
            suffix=".wav",
        )
        try:
            os.close(target_fd)
        except OSError:
            pass

        try:
            target = Path(target_str)
            with source_wav.open("rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            return target
        except OSError:
            return None

    def run(self) -> None:
        stderr_buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = stderr_buf
        reorder_tempdir: tempfile.TemporaryDirectory[str] | None = None
        concat_tempdir: tempfile.TemporaryDirectory[str] | None = None
        generated_intermediate: Path | None = None
        try:
            self.progress.emit(0, "Preparing encoder…", -1.0)
            ffmpeg_bin = ensure_executable("ffmpeg")
            ffprobe_bin = ensure_executable("ffprobe")

            chapters = list(self._chapters)
            if self._reuse_intermediate_wav is not None:
                input_for_encode = self._reuse_intermediate_wav
                if not input_for_encode.exists():
                    raise SystemExit(
                        f"Saved intermediate WAV is missing: {input_for_encode}"
                    )
                self.progress.emit(2, "Using saved intermediate WAV…", -1.0)
                sample_rate, _markers = read_riff_chunks(input_for_encode)
                duration_sec, _ = probe_audio_info(ffprobe_bin, input_for_encode)
                if self._source_files:
                    cover_root = next(iter(self._source_files.values())).parent
                else:
                    cover_root = self._input_wav.parent
                chapters_for_encode = (
                    self._sequential_timeline(chapters) if self._source_files else chapters
                )
                duration_for_encode = duration_sec
            else:
                if self._source_files:
                    if self._cancel_requested:
                        raise SystemExit
                    self.progress.emit(2, "Concatenating individual WAV files…", -1.0)
                    input_for_encode, concat_tempdir = self._concatenate_individual_files(
                        ffmpeg_bin, chapters
                    )
                    sample_rate, _markers = read_riff_chunks(input_for_encode)
                    duration_sec, _ = probe_audio_info(ffprobe_bin, input_for_encode)
                    cover_root = next(iter(self._source_files.values())).parent
                else:
                    self.progress.emit(2, "Reading markers…", -1.0)
                    sample_rate, _markers = read_riff_chunks(self._input_wav)
                    duration_sec, _ = probe_audio_info(ffprobe_bin, self._input_wav)
                    input_for_encode = self._input_wav
                    cover_root = self._input_wav.parent

                chapters_for_encode = chapters
                duration_for_encode = duration_sec
                if self._source_files:
                    # Concatenation already follows row order; only the chapter
                    # timeline needs to be laid out sequentially.
                    chapters_for_encode = self._sequential_timeline(chapters)
                needs_reorder = not self._source_files and any(
                    chapters[i].start_ms > chapters[i + 1].start_ms
                    for i in range(len(chapters) - 1)
                )
                if needs_reorder:
                    if self._cancel_requested:
                        raise SystemExit
                    self.progress.emit(4, "Reordering chapters in WAV…", -1.0)
                    (
                        input_for_encode,
                        chapters_for_encode,
                        duration_for_encode,
                        reorder_tempdir,
                    ) = self._build_reordered_wav(ffmpeg_bin, input_for_encode, chapters)

                if input_for_encode != self._input_wav:
                    generated_intermediate = input_for_encode

            if self._cover_map_override:
                cover_map = dict(self._cover_map_override)
            else:
                cover_map = resolve_cover_images(cover_root, chapters)

            self.progress.emit(5, "Encoding audio…", -1.0)

            def _progress_callback(percent: float, status_line: str, out_time_sec: float) -> None:
                pct = int(max(0.0, min(100.0, percent)))
                self.progress.emit(pct, f"Encoding… {status_line.strip()}", out_time_sec)

            actual_size_bytes = encode_output(
                ffmpeg_bin=ffmpeg_bin,
                input_file=input_for_encode,
                chapters=chapters_for_encode,
                duration_sec=duration_for_encode,
                output_file=self._output_mp3,
                channels=self._channels,
                vbr_quality=self._vbr_quality,
                cbr_bitrate=self._cbr_bitrate,
                cover_map=cover_map,
                progress_callback=_progress_callback,
                cancel_requested=lambda: self._cancel_requested,
                on_process_started=self._set_active_encode_process,
                on_process_finished=self._clear_active_encode_process,
                sample_rate=sample_rate,
            )
            self.progress.emit(100, "Encoding complete", -1.0)
            saved_intermediate: Path | None = None
            if generated_intermediate is not None:
                saved_intermediate = self._copy_intermediate_for_reuse(generated_intermediate)

            self.finished.emit(
                f"Created: {self._output_mp3} ({format_bytes(actual_size_bytes)})",
                actual_size_bytes,
                {
                    "intermediate_wav": str(saved_intermediate) if saved_intermediate else "",
                    "chapters_for_encode": list(chapters_for_encode),
                },
            )
        except SystemExit:
            if self._cancel_requested:
                self.failed.emit("Encode cancelled by user.")
            else:
                msg = stderr_buf.getvalue().strip()
                if msg.startswith("ERROR: "):
                    msg = msg[len("ERROR: "):]
                self.failed.emit(msg or "Encoding failed.")
        except Exception as exc:
            self.failed.emit(str(exc) or "Unexpected encoding error.")
        finally:
            self._cleanup_tempdir_with_retries(reorder_tempdir)
            self._cleanup_tempdir_with_retries(concat_tempdir)
            sys.stderr = old_stderr


