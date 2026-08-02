# mp4_to_txt

功能：视频内容提取为文本，支持多种视频格式

把 `videos` 目录下所有视频（含任意子目录）逐一提成文字，按原目录结构输出到 `output`，文本文件与视频同名。

## 工作方式

1. 递归扫描 `videos` 下所有子目录中的视频文件，支持 mp4/mkv/avi/mov/flv/wmv/ts/m4v/webm 等常见格式。
2. 使用 PyAV（自带 FFmpeg 解码能力）探测视频流：
   - 有内嵌文本字幕（srt/ass/mov_text 等）时，直接抽取字幕；
   - 没有可用字幕时，提取音频为 16 kHz 单声道 WAV，交给 faster-whisper 本地 AI 语音转文字。
3. 输出到 `output/<原目录结构>/<视频同名>.txt`，加 `--srt` 可同时输出 `.srt`。
4. 已生成过结果的视频会自动跳过，加 `--force` 强制重跑；成功处理后会把原视频所在子目录归档到项目根目录的 `./archiving`，并在 `output/_processing_report.txt` 生成处理报告。

## 首次安装

双击 `install.bat`，或在终端执行：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

国内下载慢时，可先把 pip 源换成阿里云镜像（只设置一次即可）：

```bat
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
```

## 在 VS Code 中运行

1. 打开本项目文件夹。
2. 按 `Ctrl+Shift+P`，选择“Python: Select Interpreter”，选中项目里的 `.venv\Scripts\python.exe`。
3. 打开终端（`Ctrl+`）执行：

```bat
python mp4_to_txt.py
```

也可以直接双击 `run.bat`。

## 常用参数

```bat
python mp4_to_txt.py --model small --language zh
python mp4_to_txt.py --model large-v3 --language zh --srt
python mp4_to_txt.py --dry-run
python mp4_to_txt.py --force
python mp4_to_txt.py --videos-dir D:\我的视频 --output-dir D:\我的文字
```

| 参数 | 说明 |
| --- | --- |
| `--model` | Whisper 模型：`tiny`/`base`/`small`/`medium`/`large-v3`，默认 `small`。中文想更准用 `large-v3`，但 CPU 上更慢 |
| `--language` | 语言代码，如 `zh`/`en`/`ja`；不填自动识别 |
| `--srt` | 同时输出带时间戳的 `.srt` |
| `--force` | 强制重跑已生成过的视频 |
| `--dry-run` | 只列出视频及字幕/音频流信息 |
| `--device` | `auto`/`cpu`/`cuda`，默认自动检测 GPU |

## 说明

- 第一次语音转写会从 HuggingFace 下载模型到项目下的 `models` 目录，需要联网。国内网络下载失败时，先执行 `set HF_ENDPOINT=https://hf-mirror.com` 再运行。
- 纯图片字幕（如蓝光 PGS）无法直接转成文本，这种情况会自动回退到语音转写；如果视频既无字幕也无音频，会记录到报告并跳过。
- 不需要单独安装 ffmpeg，PyAV 已内置解码能力。
