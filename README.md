# mp4_to_txt

`mp4_to_txt` 是一个面向批处理场景的视频文本提取工具：

- 自动递归扫描 `videos/` 下的视频文件
- 优先提取内嵌文本字幕
- 无可用字幕时自动回退到本地语音转写（faster-whisper）
- 转写阶段先输出临时文本，归档时再转正
- 可选输出 SRT
- 在长任务场景中提供实时日志、转写进度与阶段性报告

## 核心能力

- 字幕优先策略：存在可解码字幕流时直接提取字幕内容，避免不必要转写开销。
- 音频转写回退：无字幕时提取 16kHz 单声道 WAV，再执行本地 ASR。
- 残留源视频保护：如果源视频仍留在 `videos/`，即使已有同名输出，也会视为上次未完整归档并覆盖重写输出。
- 进度可观测：转写阶段提供单行进度更新（基于片段时间位置估算百分比）。
- 运行可追溯：
  - 实时运行日志：`output/_runtime.log`
  - 阶段性处理报告：`output/_processing_report.txt`（每个视频处理后刷新）
- 自动归档：处理成功后，源视频按相对路径移动到项目根目录 `archiving/`。
- 临时转正：转写结果先写入 `output/*.tmp`，归档时改名为 `.txt` 并移动到 `archiving/` 与视频同路径。
- 中间文件清理：用于转写的 `wav` 音频中间文件在转写完成后自动删除。

## 处理流程

1. 扫描 `videos/` 下所有支持格式的视频。
2. 探测流信息（字幕流/音频流/时长）。
3. 执行文本提取：
  - 字幕可用：直接提取
  - 字幕不可用：提取音频并转写
  - 若检测到同名输出已存在但源视频仍在待处理目录：覆盖已有输出并重跑
4. 在 `output/` 生成临时文本（`.tmp`）与可选 `srt`（保持原目录结构）。
5. 归档阶段将视频移入 `archiving/`，同时把对应 `.tmp` 改名为 `.txt` 并移入同路径。
6. 刷新报告并清理音频中间文件。

## 支持的输入格式

常见视频格式包括：`mp4`、`mkv`、`avi`、`mov`、`flv`、`wmv`、`m4v`、`webm`、`ts`、`mts`、`m2ts`、`mpg`、`mpeg`、`3gp`、`3g2`、`rm`、`rmvb`、`vob`、`ogv`、`f4v`。

## 输出规范

- 临时文本：`output/<原相对路径>/<视频同名>.tmp`
- 正式文本（归档后）：`archiving/<原相对路径>/<视频同名>.txt`
- 可选字幕输出：`output/<原相对路径>/<视频同名>.srt`
- 运行日志：`output/_runtime.log`
- 处理报告：`output/_processing_report.txt`
- 归档目录：`archiving/<原相对路径>/<原视频文件>`

## 环境要求

- Python 3.10+
- Windows / macOS
- 建议使用虚拟环境 `.venv`

## 安装

### 方式一：一键安装（推荐）

Windows：运行 `install.bat`

macOS：运行 `./install.sh`

### 方式二：手动安装

Windows：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

国内网络可配置镜像：

```bat
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
```

## 快速开始

Windows：

```bat
python mp4_to_txt.py
```

macOS：

```bash
python3 mp4_to_txt.py
```

持续监控并自动处理：

Windows：

```bat
python watch_videos.py --run-existing
```

macOS：

```bash
./run_watch_videos.sh
```

在 VS Code 中建议选择解释器：Windows 用 `.venv\Scripts\python.exe`，macOS 用 `.venv/bin/python`。

## 常用命令

```bat
python mp4_to_txt.py --model small --language zh
python mp4_to_txt.py --model large-v3 --language zh --srt
python mp4_to_txt.py --dry-run
python mp4_to_txt.py --force
python mp4_to_txt.py --videos-dir D:\my_videos --output-dir D:\my_output
python watch_videos.py --run-existing --model small --language zh
```

## 参数说明

| 参数 | 说明 |
| --- | --- |
| `--videos-dir` | 输入视频根目录，默认 `./videos` |
| `--output-dir` | 输出根目录，默认 `./output` |
| `--model` | Whisper 模型（`tiny/base/small/medium/large-v3`），默认 `small` |
| `--model-dir` | 模型缓存目录，默认 `./models` |
| `--language` | 语言代码，如 `zh`、`en`、`ja`；为空时自动识别 |
| `--device` | 推理设备：`auto` / `cpu` / `cuda` |
| `--compute-type` | 推理精度：`auto/int8/int8_float16/float16/float32` |
| `--beam-size` | 解码 beam size，默认 `5` |
| `--cpu-threads` | CPU 线程数，`0` 表示自动 |
| `--no-vad` | 关闭 VAD（静音检测） |
| `--srt` | 同时生成 `.srt` |
| `--force` | 显式声明覆盖已有输出；当源视频仍在待处理目录时，脚本默认也会覆盖重跑 |
| `--dry-run` | 仅探测并打印流信息，不执行处理 |
| `--verbose` | 输出更详细的运行日志 |

`watch_videos.py` 额外参数：

| 参数 | 说明 |
| --- | --- |
| `--videos-dir` | 监控目录，默认 `./videos` |
| `--processor` | 处理脚本路径，默认 `./mp4_to_txt.py` |
| `--poll-interval` | 轮询间隔秒数，默认 `2` |
| `--stable-seconds` | 文件稳定判定秒数，默认 `3` |
| `--run-existing` | 启动后连同当前已存在视频一并处理 |

说明：`watch_videos.py` 会把它不认识的参数透传给 `mp4_to_txt.py`，例如 `--model`、`--language`、`--srt`。
说明：如果在 `mp4_to_txt.py` 正在执行期间又放入了新视频，监控器会在本轮结束后自动识别并触发下一轮处理，无需人工干预。

## 稳定性与可观测性说明

- 运行期日志实时落盘，异常可在 `output/_runtime.log` 直接定位。
- 报告按视频粒度持续刷新，中途中断也可保留已完成阶段结果。
- 音频提取与转写采用子进程隔离策略，降低底层库异常对主流程的影响范围。
- 若上次中断后出现“源视频仍在 `videos/`、但 `output/` 已有同名文本”的残留状态，脚本会优先重跑并覆盖旧输出，而不是直接跳过。

## 已知边界

- 纯图片字幕（如 PGS）无法直接提取文本，会回退到音频转写。
- 若视频无字幕且无音频，将标记为失败并记录到报告。
- 首次使用新模型时，模型下载依赖网络连通性。
