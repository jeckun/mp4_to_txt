#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监控 videos 目录，发现新视频后自动触发 mp4_to_txt 处理。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".webm",
    ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp", ".3g2", ".rm",
    ".rmvb", ".vob", ".ogv", ".f4v",
}


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="监控 videos 目录，发现新视频后自动执行 mp4_to_txt.py",
    )
    parser.add_argument("--videos-dir", type=Path, default=base / "videos", help="要监控的视频目录")
    parser.add_argument("--processor", type=Path, default=base / "mp4_to_txt.py", help="处理脚本路径")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="轮询间隔秒数，默认 2")
    parser.add_argument("--stable-seconds", type=float, default=3.0, help="文件稳定判定秒数，默认 3")
    parser.add_argument("--run-existing", action="store_true", help="启动后立刻处理当前已存在视频")
    args, passthrough = parser.parse_known_args(argv)
    args.videos_dir = args.videos_dir.resolve()
    args.processor = args.processor.resolve()
    return args, passthrough


def find_video_files(videos_dir: Path) -> list[Path]:
    return sorted(
        path for path in videos_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def collect_stats(paths: list[Path]) -> dict[Path, tuple[int, int]]:
    stats: dict[Path, tuple[int, int]] = {}
    for path in paths:
        try:
            st = path.stat()
            stats[path] = (st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            continue
    return stats


def run_processor(processor: Path, passthrough_args: list[str]) -> int:
    command = [sys.executable, str(processor), *passthrough_args]
    print(f"\n[监控] 触发处理: {' '.join(command)}")
    result = subprocess.run(command)
    print(f"[监控] 处理结束，退出码: {result.returncode}")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    args, passthrough_args = parse_args(argv)

    if not args.videos_dir.is_dir():
        print(f"监控目录不存在: {args.videos_dir}")
        return 2
    if not args.processor.is_file():
        print(f"处理脚本不存在: {args.processor}")
        return 2

    print(f"[监控] 正在监控: {args.videos_dir}")
    print(f"[监控] 处理脚本: {args.processor}")
    print(f"[监控] 轮询间隔: {args.poll_interval} 秒，稳定判定: {args.stable_seconds} 秒")
    if passthrough_args:
        print(f"[监控] 透传参数: {' '.join(passthrough_args)}")

    baseline_stats = collect_stats(find_video_files(args.videos_dir))
    if args.run_existing:
        baseline_stats = {}
    pending_since: dict[Path, float] = {}
    pending_stats: dict[Path, tuple[int, int]] = {}

    while True:
        current_stats = collect_stats(find_video_files(args.videos_dir))
        now = time.time()

        for path, stat in current_stats.items():
            baseline_stat = baseline_stats.get(path)
            if baseline_stat != stat:
                if pending_stats.get(path) != stat:
                    pending_stats[path] = stat
                    pending_since[path] = now

        removed_paths = [path for path in list(pending_since) if path not in current_stats]
        for path in removed_paths:
            pending_since.pop(path, None)
            pending_stats.pop(path, None)

        newly_stable = [
            path for path, since in pending_since.items()
            if path in current_stats
            and pending_stats.get(path) == current_stats.get(path)
            and (now - since) >= args.stable_seconds
        ]

        if newly_stable:
            print("[监控] 检测到新视频并已稳定:")
            for path in newly_stable:
                print(f"  - {path}")
            trigger_snapshot = dict(current_stats)
            exit_code = run_processor(args.processor, passthrough_args)
            post_stats = collect_stats(find_video_files(args.videos_dir))

            # 处理窗口内新增或变化的文件需要加入下一轮，否则会被 baseline 吞掉。
            now = time.time()
            for path, stat in post_stats.items():
                before_stat = trigger_snapshot.get(path)
                if before_stat != stat:
                    pending_stats[path] = stat
                    pending_since[path] = now

            # 本轮触发前的待处理项已交给处理器，先清掉；处理窗口新增项已在上面重新登记。
            for path in newly_stable:
                pending_stats.pop(path, None)
                pending_since.pop(path, None)

            # 若处理器失败，保留当前目录中的所有文件为待重试项，确保最终可自动补处理。
            if exit_code != 0:
                for path, stat in post_stats.items():
                    pending_stats[path] = stat
                    pending_since[path] = now

            current_stats = post_stats

        baseline_stats = current_stats
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
