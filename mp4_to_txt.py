#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""递归提取 videos 目录下所有视频的文字内容到 output 目录。

处理顺序：
1. 递归扫描 videos 下所有子目录中的视频文件；
2. 用 PyAV 探测视频流：
   - 有可解码文本字幕时，直接抽取字幕文本；
   - 无字幕时，提取音频为 16 kHz 单声道 WAV，交给 faster-whisper 语音转文字；
3. 在 output 下按原目录结构生成与视频同名的 .txt，可选生成 .srt。

示例：
    python mp4_to_txt.py
    python mp4_to_txt.py --model small --language zh --srt --force
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import re
import subprocess
import sys
import time
import traceback
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from lib.text_utils import normalize_output_text

try:
    import av
except ImportError:
    av = None

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".webm",
    ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp", ".3g2", ".rm",
    ".rmvb", ".vob", ".ogv", ".f4v",
}

ASS_TAG_RE = re.compile(r"\{\\[^}]*\}")
HTML_TAG_RE = re.compile(r"<[^>]+>")

@dataclass
class Segment:
    start: Optional[float]
    end: Optional[float]
    text: str


@dataclass
class VideoResult:
    video: Path
    rel: Path
    output_txt: Optional[Path] = None
    output_srt: Optional[Path] = None
    status: str = "ok"          # ok / skipped / failed
    method: str = ""            # subtitle / stt / -
    segments: int = 0
    duration: Optional[float] = None
    processing_seconds: float = 0.0
    task_seconds: dict[str, float] = field(default_factory=dict)
    error: str = ""


class TeeStream:
    """Mirror console output to a log file and flush immediately."""

    def __init__(self, primary, mirror):
        self._primary = primary
        self._mirror = mirror

    def write(self, data):
        self._primary.write(data)
        self._mirror.write(data)

    def flush(self):
        self._primary.flush()
        self._mirror.flush()

    def isatty(self):
        return getattr(self._primary, "isatty", lambda: False)()


def human_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    seconds = int(round(seconds))
    if seconds <= 0:
        return "0秒"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def describe_exit_code(return_code: int) -> str:
    if return_code == -1073741819:
        return f"{return_code}（Windows access violation，常见于底层 C 扩展崩溃）"
    return str(return_code)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="递归提取 videos 目录下所有视频的文字内容到 output 目录",
    )
    parser.add_argument("--videos-dir", type=Path, default=base / "videos",
                        help="视频根目录，默认取项目下的 videos")
    parser.add_argument("--output-dir", type=Path, default=base / "output",
                        help="输出目录，默认取项目下的 output")
    parser.add_argument("--model", default="small",
                        help="Whisper 模型：tiny/base/small/medium/large-v3，默认 small")
    parser.add_argument("--model-dir", type=Path, default=base / "models",
                        help="Whisper 模型下载/缓存目录，默认取项目下的 models")
    parser.add_argument("--language", default=None,
                        help="语音语言代码，如 zh/en/ja；不填则自动识别")
    parser.add_argument("--device", default="auto",
                        choices=["auto", "cpu", "cuda"],
                        help="运行设备，默认自动检测")
    parser.add_argument("--compute-type", default=None,
                        choices=["auto", "int8", "int8_float16", "float16", "float32"],
                        help="推理精度，不填自动选择")
    parser.add_argument("--beam-size", type=int, default=5,
                        help="beam search 宽度，默认 5")
    parser.add_argument("--cpu-threads", type=int, default=0,
                        help="CPU 线程数，0 表示自动")
    parser.add_argument("--no-vad", action="store_true",
                        help="关闭静音检测")
    parser.add_argument("--srt", action="store_true",
                        help="同时输出带时间戳的 .srt 文件")
    parser.add_argument("--force", action="store_true",
                        help="强制重新处理已有输出的视频")
    parser.add_argument("--dry-run", action="store_true",
                        help="只列出视频和流信息，不实际处理")
    parser.add_argument("--verbose", action="store_true",
                        help="显示更详细的日志")
    parser.add_argument("--_extract-audio-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_transcribe-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_worker-video", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--_worker-wav", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--_worker-stream-index", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.videos_dir = args.videos_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.model_dir = args.model_dir.resolve()
    if args._worker_video is not None:
        args._worker_video = args._worker_video.resolve()
    if args._worker_wav is not None:
        args._worker_wav = args._worker_wav.resolve()
    return args


def find_video_files(videos_dir: Path) -> list[Path]:
    return sorted(
        [
            path for path in videos_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        ]
    )


def probe_streams(video_path: Path) -> tuple[list[int], list[int], Optional[float]]:
    """返回 (字幕流索引列表, 音频流索引列表, 视频时长秒数)。"""
    if av is None:
        raise RuntimeError(
            "缺少 PyAV 依赖，请先运行 install.bat 或执行 pip install -r requirements.txt"
        )
    container = av.open(str(video_path))
    try:
        subtitles = [int(s.index) for s in container.streams if s.type == "subtitle"]
        audios = [int(s.index) for s in container.streams if s.type == "audio"]
        duration = container.duration / 1_000_000 if container.duration else None
        return subtitles, audios, duration
    finally:
        container.close()


def ass_text_to_lines(ass_text: str) -> list[str]:
    """把 ASS 形式的字幕文本解析成可读行。"""
    if not ass_text:
        return []
    lines: list[str] = []
    for raw in ass_text.splitlines():
        line = raw.strip()
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        text = parts[9]
        text = ASS_TAG_RE.sub("", text)
        text = text.replace("\\N", "\n").replace("\\n", "\n")
        text = HTML_TAG_RE.sub("", text)
        text = text.strip()
        if text:
            lines.append(text)
    if lines:
        return lines
    fallback = ass_text.strip()
    return [fallback] if fallback else []


def select_stream(container, stream_type: str, preferred_stream_index: Optional[int] = None):
    """从容器中选择目标流，优先按索引匹配。"""
    candidates = [s for s in container.streams if getattr(s, "type", None) == stream_type]
    if not candidates:
        return None

    if preferred_stream_index is None:
        return candidates[0]

    for stream in candidates:
        if getattr(stream, "index", None) == preferred_stream_index:
            return stream

    return candidates[0]


def extract_subtitle_entries(video_path: Path, subtitle_stream_index: Optional[int]) -> list[Segment]:
    """解码一条文本字幕流，返回带时间信息的字幕条目。"""
    if av is None:
        raise RuntimeError(
            "缺少 PyAV 依赖，请先运行 install.bat 或执行 pip install -r requirements.txt"
        )
    entries: list[Segment] = []
    container = av.open(str(video_path))
    try:
        stream = select_stream(container, "subtitle", subtitle_stream_index)
        if stream is None:
            return entries
        for frame in container.decode(stream):
            if frame is None:
                continue
            start = getattr(frame, "start", None)
            end = getattr(frame, "end", None)
            ass_text = getattr(frame, "ass", None) or ""
            lines = ass_text_to_lines(ass_text)
            if not lines:
                continue
            entries.append(Segment(start=start, end=end, text="\n".join(lines)))
    finally:
        container.close()
    return entries


def extract_audio_to_wav(video_path: Path, audio_stream_index: Optional[int], wav_path: Path) -> int:
    """把音频流重采样为 16 kHz 单声道 WAV，返回采样点数。"""
    if av is None:
        raise RuntimeError(
            "缺少 PyAV 依赖，请先运行 install.bat 或执行 pip install -r requirements.txt"
        )
    container = av.open(str(video_path))
    try:
        stream = None
        if audio_stream_index is not None:
            for candidate in container.streams:
                if getattr(candidate, "type", None) == "audio" and getattr(candidate, "index", None) == audio_stream_index:
                    stream = candidate
                    break
        if stream is None:
            stream = select_stream(container, "audio")
        if stream is None:
            raise RuntimeError("找不到音频流")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            written = 0
            for frame in container.decode(stream):
                for rframe in resampler.resample(frame):
                    data = rframe.to_ndarray()
                    wav.writeframes(data.tobytes())
                    written += data.shape[1]
            for rframe in resampler.resample(None):
                data = rframe.to_ndarray()
                wav.writeframes(data.tobytes())
                written += data.shape[1]
            return written
    finally:
        container.close()


def export_audio_to_output(video_path: Path, args: argparse.Namespace, audio_stream_index: Optional[int]) -> Path:
    """把音频导出为 output/<相对路径>/<视频名>.wav。"""
    rel = video_path.relative_to(args.videos_dir)
    out_dir = args.output_dir / rel.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"{video_path.stem}.wav"

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_extract-audio-worker",
        "--_worker-video",
        str(video_path),
        "--_worker-wav",
        str(wav_path),
    ]
    if audio_stream_index is not None:
        command.extend(["--_worker-stream-index", str(audio_stream_index)])

    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        if details:
            raise RuntimeError(
                f"音频提取子进程失败（退出码 {describe_exit_code(proc.returncode)}）：{details}"
            )
        raise RuntimeError(f"音频提取子进程失败（退出码 {describe_exit_code(proc.returncode)}）")

    return wav_path


def resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def build_whisper_model_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    device = resolve_device(args.device)
    compute_type = args.compute_type or "auto"
    if compute_type == "auto":
        compute_type = "int8_float16" if device == "cuda" else "int8"
    return {
        "model_size_or_path": args.model,
        "device": device,
        "compute_type": compute_type,
        "cpu_threads": args.cpu_threads,
        "num_workers": 1,
        "download_root": str(args.model_dir),
        "local_files_only": True,
    }


class Transcriber:
    """faster-whisper 模型的懒加载封装，失败只报一次。"""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self._model = None

    def should_use_vad(self) -> bool:
        if self.args.no_vad:
            return False
        try:
            import onnxruntime  # noqa: F401
        except Exception:
            return False
        return True

    def get_model(self):
        from faster_whisper import WhisperModel
        if self._model is None:
            try:
                import os

                self.args.model_dir.mkdir(parents=True, exist_ok=True)
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

                kwargs = build_whisper_model_kwargs(self.args)
                self._model = WhisperModel(**kwargs)
            except Exception as exc:
                raise RuntimeError(
                    "初始化 Whisper 模型失败。请先在安装阶段把模型下载好，然后再运行脚本。"
                ) from exc
        return self._model

    def transcribe(self, wav_path: Path) -> tuple[list[Segment], dict[str, Any]]:
        model = self.get_model()
        use_vad = self.should_use_vad()
        if not use_vad and not self.args.no_vad:
            print("  VAD 依赖不可用，已自动关闭 VAD 以继续运行")
        segments_iter, info = model.transcribe(
            str(wav_path),
            language=self.args.language or None,
            beam_size=self.args.beam_size,
            vad_filter=use_vad,
            condition_on_previous_text=True,
        )
        segments: list[Segment] = []
        for seg in segments_iter:
            text = seg.text.strip()
            if text:
                segments.append(Segment(start=float(seg.start), end=float(seg.end), text=text))
        meta = {
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
        }
        return segments, meta

    def transcribe_in_subprocess(
        self,
        wav_path: Path,
        media_duration_seconds: Optional[float] = None,
    ) -> tuple[list[Segment], dict[str, Any]]:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--_transcribe-worker",
            "--_worker-wav",
            str(wav_path),
            "--model",
            self.args.model,
            "--model-dir",
            str(self.args.model_dir),
            "--device",
            self.args.device,
            "--beam-size",
            str(self.args.beam_size),
            "--cpu-threads",
            str(self.args.cpu_threads),
        ]
        if self.args.compute_type is not None:
            command.extend(["--compute-type", self.args.compute_type])
        if self.args.language:
            command.extend(["--language", self.args.language])
        if self.args.no_vad:
            command.append("--no-vad")

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        segments: list[Segment] = []
        meta: dict[str, Any] = {}
        last_progress_bucket = -1
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"  [转写子进程] {line}")
                continue

            kind = event.get("event")
            if kind == "segment":
                text = str(event.get("text", "")).strip()
                if not text:
                    continue
                seg_end = event.get("end")
                segments.append(
                    Segment(
                        start=event.get("start"),
                        end=seg_end,
                        text=text,
                    )
                )
                if media_duration_seconds and media_duration_seconds > 0 and seg_end is not None:
                    try:
                        progress = max(0.0, min(100.0, float(seg_end) / media_duration_seconds * 100.0))
                        progress_bucket = int(progress)
                        # 仅在进度变化时刷新，避免刷屏。
                        if progress_bucket != last_progress_bucket:
                            print(f"\r  [状态] 正在转写... {progress:.1f}%", end="", flush=True)
                            last_progress_bucket = progress_bucket
                    except Exception:
                        pass
            elif kind == "done":
                meta = event.get("meta", {}) or {}

        if last_progress_bucket >= 0:
            print()

        stderr = ""
        if proc.stderr is not None:
            stderr = proc.stderr.read().strip()
        return_code = proc.wait()
        if return_code != 0:
            if stderr:
                raise RuntimeError(
                    f"语音转写子进程失败（退出码 {describe_exit_code(return_code)}）：{stderr}"
                )
            raise RuntimeError(f"语音转写子进程失败（退出码 {describe_exit_code(return_code)}）")
        return segments, meta


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def format_srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
        if s == 60:
            s = 0
            m += 1
            if m == 60:
                m = 0
                h += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(segments: list[Segment]) -> str:
    blocks: list[str] = []
    index = 1
    for seg in segments:
        if seg.start is None or seg.end is None:
            continue
        blocks.append(
            f"{index}\n"
            f"{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}\n"
            f"{seg.text}\n"
        )
        index += 1
    return "\n".join(blocks)


def archive_root_dir(args: argparse.Namespace) -> Path:
    """归档目录固定在项目根目录下的 archiving。"""
    return args.videos_dir.parent / "archiving"


def archive_processed_video(video_path: Path, args: argparse.Namespace) -> Path:
    """把成功处理的视频文件按相对路径移动到 archiving 目录。"""
    rel = video_path.relative_to(args.videos_dir)
    archive_root = archive_root_dir(args)
    archive_root.mkdir(parents=True, exist_ok=True)
    destination_path = archive_root / rel
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if not video_path.exists():
        if destination_path.exists():
            return destination_path
        raise FileNotFoundError(f"归档前找不到源文件: {video_path}")
    video_path.replace(destination_path)
    return destination_path


def has_existing_output(out_txt: Path, out_srt: Optional[Path]) -> bool:
    return out_txt.exists() and (out_srt is None or out_srt.exists())


def process_video(video_path: Path, args: argparse.Namespace,
                  transcriber: Transcriber) -> VideoResult:
    rel = video_path.relative_to(args.videos_dir)
    out_dir = args.output_dir / rel.parent
    out_txt = out_dir / f"{video_path.stem}.txt"
    out_srt = out_dir / f"{video_path.stem}.srt" if args.srt else None

    result = VideoResult(video=video_path, rel=rel, output_txt=out_txt, output_srt=out_srt)

    existing_output = has_existing_output(out_txt, out_srt)
    if existing_output:
        if args.force:
            print("  [状态] 已启用 --force，正在覆盖已有输出...")
        else:
            print("  [状态] 检测到源视频仍在待处理目录，已有输出将视为未完成结果并覆盖重写...")

    try:
        print(f"  [状态] 正在读取视频流信息: {video_path.name}")
        task_start = time.time()
        subtitles, audios, duration = probe_streams(video_path)
        stream_probe_seconds = time.time() - task_start
        result.task_seconds["流信息探测"] = stream_probe_seconds
        print(f"  [状态] 视频流信息读取完成（耗时 {human_duration(stream_probe_seconds)}）")
        result.duration = duration

        segments: list[Segment] = []
        method = ""
        if subtitles:
            print("  [状态] 检测到字幕流，正在读取字幕内容...")
            task_start = time.time()
            for subtitle_stream_index in subtitles:
                segments = extract_subtitle_entries(video_path, subtitle_stream_index)
                if segments:
                    method = "subtitle"
                    break
            subtitle_read_seconds = time.time() - task_start
            result.task_seconds["字幕读取"] = subtitle_read_seconds
            print(f"  [状态] 字幕读取完成（耗时 {human_duration(subtitle_read_seconds)}）")
            if not segments:
                print("  [状态] 未读取到有效字幕，继续尝试语音识别...")

        if not segments:
            if not audios:
                raise RuntimeError("视频既没有可用字幕，也没有音频流")
            method = "stt"
            print(f"  [状态] 正在提取音频（时长 {human_duration(duration)}）...")
            task_start = time.time()
            audio_stream_index = audios[0]
            wav_path = export_audio_to_output(video_path, args, audio_stream_index)
            audio_extract_seconds = time.time() - task_start
            result.task_seconds["音频提取"] = audio_extract_seconds
            print(f"  [状态] 音频提取完成（耗时 {human_duration(audio_extract_seconds)}）")
            print("  [状态] 音频已提取，正在转写...")
            task_start = time.time()
            segments, meta = transcriber.transcribe_in_subprocess(
                wav_path,
                media_duration_seconds=duration,
            )
            transcribe_seconds = time.time() - task_start
            result.task_seconds["语音转写"] = transcribe_seconds
            print(f"  [状态] 语音转写完成（耗时 {human_duration(transcribe_seconds)}）")
            if args.verbose:
                lang = meta.get("language", "?")
                prob = meta.get("language_probability")
                prob_text = f" ({prob:.2%})" if prob is not None else ""
                print(f"  识别语言: {lang}{prob_text}")

        result.segments = len(segments)
        result.method = method
        text = "\n".join(seg.text for seg in segments)
        normalized_text = normalize_output_text(text)
        if not normalized_text:
            raise RuntimeError("没有提取到任何文字（可能是无声视频或纯图片字幕）")

        print("  [状态] 正在写入输出文本...")
        task_start = time.time()
        write_text_file(out_txt, normalized_text + "\n")
        text_write_seconds = time.time() - task_start
        result.task_seconds["文本写入"] = text_write_seconds
        print(f"  [状态] 文本写入完成（耗时 {human_duration(text_write_seconds)}）")
        if args.srt:
            task_start = time.time()
            srt_content = build_srt(segments)
            if srt_content:
                write_text_file(out_srt, srt_content + "\n")
            srt_write_seconds = time.time() - task_start
            result.task_seconds["SRT写入"] = srt_write_seconds
            print(f"  [状态] SRT 写入完成（耗时 {human_duration(srt_write_seconds)}）")
        task_start = time.time()
        archived_path = archive_processed_video(video_path, args)
        archive_seconds = time.time() - task_start
        result.task_seconds["归档目录"] = archive_seconds
        print(f"  [状态] 归档完成（耗时 {human_duration(archive_seconds)}）")
        print(f"  [状态] 处理完成，已归档到 {archived_path}")
        result.status = "ok"
        return result
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        print(f"  [错误] 处理失败: {exc}")
        print(traceback.format_exc().rstrip())
        return result


def dry_run_video(video_path: Path, args: argparse.Namespace) -> None:
    rel = video_path.relative_to(args.videos_dir)
    try:
        subtitles, audios, duration = probe_streams(video_path)
        print(f"{rel}  时长 {human_duration(duration)}  字幕流 {len(subtitles)}  音频流 {len(audios)}")
    except Exception as exc:
        print(f"{rel}  读取失败: {exc}")


def write_report(report_path: Path, args: argparse.Namespace,
                 results: list[VideoResult], elapsed: float) -> None:
    status_names = {"ok": "成功", "skipped": "跳过", "failed": "失败"}
    method_names = {"subtitle": "内嵌字幕", "stt": "语音转写", "-": "-"}
    lines = [
        "mp4_to_txt 处理报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"总耗时: {human_duration(elapsed)}",
        f"视频目录: {args.videos_dir}",
        f"输出目录: {args.output_dir}",
        f"模型: {args.model}（device={args.device}）",
        "",
    ]
    counts = {"ok": 0, "skipped": 0, "failed": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    lines.append(f"成功: {counts['ok']}  跳过: {counts['skipped']}  失败: {counts['failed']}")
    lines.append("")
    for result in results:
        status = status_names.get(result.status, result.status)
        method = method_names.get(result.method, result.method)
        output = str(result.output_txt) if result.output_txt else "-"
        duration = human_duration(result.duration)
        task_breakdown = ", ".join(
            f"{name}={human_duration(seconds)}" for name, seconds in result.task_seconds.items()
        ) or "-"
        lines.append(
            f"{result.rel} | {status} | {method} | {result.segments} 段 | "
            f"{duration} | {human_duration(result.processing_seconds)} | {task_breakdown} | {output}"
        )
        if result.error:
            lines.append(f"  错误: {result.error}")
    write_text_file(report_path, "\n".join(lines) + "\n")


def print_summary(results: list[VideoResult], elapsed: float, report_path: Path) -> None:
    ok = sum(1 for r in results if r.status == "ok")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")
    print("\n========== 处理完成 ==========")
    print(f"总耗时: {human_duration(elapsed)}，成功 {ok}，跳过 {skipped}，失败 {failed}")
    if failed:
        print("失败的视频：")
        for result in results:
            if result.status == "failed":
                print(f"  {result.rel}: {result.error}")
    print(f"详细报告: {report_path}")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args._extract_audio_worker:
        if args._worker_video is None or args._worker_wav is None:
            print("音频提取子进程参数不完整", file=sys.stderr)
            return 2
        try:
            faulthandler.enable()
            extract_audio_to_wav(args._worker_video, args._worker_stream_index, args._worker_wav)
            return 0
        except Exception:
            print(traceback.format_exc().rstrip(), file=sys.stderr)
            return 1

    if args._transcribe_worker:
        if args._worker_wav is None:
            print("语音转写子进程参数不完整", file=sys.stderr)
            return 2
        try:
            faulthandler.enable()
            worker_transcriber = Transcriber(args)
            model = worker_transcriber.get_model()
            use_vad = worker_transcriber.should_use_vad()
            segments_iter, info = model.transcribe(
                str(args._worker_wav),
                language=args.language or None,
                beam_size=args.beam_size,
                vad_filter=use_vad,
                condition_on_previous_text=True,
            )

            for seg in segments_iter:
                text = seg.text.strip()
                if not text:
                    continue
                print(
                    json.dumps(
                        {
                            "event": "segment",
                            "start": float(seg.start),
                            "end": float(seg.end),
                            "text": text,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

            print(
                json.dumps(
                    {
                        "event": "done",
                        "meta": {
                            "language": info.language,
                            "language_probability": info.language_probability,
                            "duration": info.duration,
                        },
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0
        except Exception:
            print(traceback.format_exc().rstrip(), file=sys.stderr)
            return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    report_path = args.output_dir / "_processing_report.txt"
    runtime_log_path = args.output_dir / "_runtime.log"

    runtime_log = runtime_log_path.open("a", encoding="utf-8", newline="\n")
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = TeeStream(old_stdout, runtime_log)
    sys.stderr = TeeStream(old_stderr, runtime_log)
    faulthandler.enable(file=runtime_log, all_threads=True)

    try:
        if not args.videos_dir.is_dir():
            print(f"视频目录不存在: {args.videos_dir}")
            return 2

        videos = find_video_files(args.videos_dir)
        if not videos:
            print(f"{args.videos_dir} 下没有找到视频文件。")
            return 1

        print(f"\n===== 任务开始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
        print(f"运行日志: {runtime_log_path}")
        print(f"共找到 {len(videos)} 个视频：")
        if args.dry_run:
            for video in videos:
                dry_run_video(video, args)
            return 0

        transcriber = Transcriber(args)
        results: list[VideoResult] = []
        start_time = time.time()

        for index, video in enumerate(videos, 1):
            rel = video.relative_to(args.videos_dir)
            print(f"\n[{index}/{len(videos)}] {rel}")
            video_start = time.time()
            result: VideoResult
            try:
                result = process_video(video, args, transcriber)
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    print("\n[中断] 用户取消执行")
                    raise
                result = VideoResult(video=video, rel=rel, status="failed", method="-")
                result.error = f"{type(exc).__name__}: {exc}"
                print(f"  [错误] 捕获到未处理异常: {result.error}")
                print(traceback.format_exc().rstrip())

            result.processing_seconds = time.time() - video_start
            results.append(result)
            if result.status == "ok":
                print(f"  完成: {result.output_txt}（{result.method}，{result.segments} 段，"
                      f"{human_duration(result.processing_seconds)}）")
            elif result.status == "skipped":
                print("  跳过: 输出已存在（加 --force 可强制重跑）")
            else:
                print(f"  失败: {result.error}")

            # 每处理完一个视频就刷新报告，避免中途中断时无日志可查。
            write_report(report_path, args, results, time.time() - start_time)
            print(f"  [状态] 已刷新阶段性报告: {report_path}")

        elapsed = time.time() - start_time
        write_report(report_path, args, results, elapsed)
        print_summary(results, elapsed, report_path)
        return 0
    finally:
        try:
            faulthandler.disable()
        except Exception:
            pass
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        runtime_log.flush()
        runtime_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
