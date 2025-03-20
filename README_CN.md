[![Upload Python Package](https://github.com/Blaizzy/mlx-vlm/actions/workflows/python-publish.yml/badge.svg)](https://github.com/Blaizzy/mlx-vlm/actions/workflows/python-publish.yml)
# MLX-VLM

MLX-VLM 是一个用于在 Mac 上使用 MLX 进行视觉语言模型 (VLMs) 推理和微调的软件包。

## 目录
- [MLX-VLM](#mlx-vlm)
  - [目录](#目录)
  - [安装](#安装)
  - [使用方法](#使用方法)
    - [命令行界面 (CLI)](#命令行界面-cli)
    - [使用 Gradio 的聊天界面](#使用-gradio-的聊天界面)
    - [Python 脚本](#python-脚本)
  - [多图像聊天支持](#多图像聊天支持)
    - [支持的模型](#支持的模型)
    - [使用示例](#使用示例)
      - [Python 脚本](#python-脚本-1)
      - [命令行](#命令行)
  - [视频理解](#视频理解)
    - [支持的模型](#支持的模型-1)
    - [使用示例](#使用示例-1)
      - [命令行](#命令行-1)
- [微调](#微调)
  - [LoRA 和 QLoRA](#lora-和-qlora)

## 安装

最简单的入门方式是使用 pip 安装 `mlx-vlm` 包：

```sh
pip install mlx-vlm
```

## 使用方法

### 命令行界面 (CLI)

使用 CLI 从模型生成输出：

```sh
python -m mlx_vlm.generate --model mlx-community/Qwen2-VL-2B-Instruct-4bit --max-tokens 100 --temp 0.0 --image http://images.cocodataset.org/val2017/000000039769.jpg
```


使用代理下载模型：
```sh
python -m mlx_vlm.generate --model mlx-community/Qwen2-VL-2B-Instruct-4bit --max-tokens 100 --temp 0.0 --image http://images.cocodataset.org/val2017/000000039769.jpg --proxy http://127.0.0.1:7890
```
或者，如果您已经下载了模型到本地，可以直接指定本地路径：
```sh
python -m mlx_vlm.generate --local-model-path /Users/yangcailu/models/Qwen2-VL-2B-Instruct-4bit --image h
```
或者，如果您已经下载了模型到本地，可以直接指定本地路径：
```sh
python -m mlx_vlm.generate --local-model-path /Users/yangcailu/models/Qwen2-VL-2B-Instruct-4bit --image URL_ADDRESS.cocodataset.org/val2017/000000039769.jpg
```

### 使用 Gradio 的聊天界面

使用 Gradio 启动聊天界面：

```sh
python -m mlx_vlm.chat_ui --model mlx-community/Qwen2-VL-2B-Instruct-4bit
```

### Python 脚本

以下是如何在 Python 脚本中使用 MLX-VLM 的示例：

```python
import mlx.core as mx
from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config

# 加载模型
model_path = "mlx-community/Qwen2-VL-2B-Instruct-4bit"
model, processor = load(model_path)
config = load_config(model_path)

# 准备输入
image = ["http://images.cocodataset.org/val2017/000000039769.jpg"]
prompt = "描述这张图片。"

# 应用聊天模板
formatted_prompt = apply_chat_template(
    processor, config, prompt, num_images=len(image)
)

# 生成输出
output = generate(model, processor, formatted_prompt, image, verbose=False)
print(output)
```

## 多图像聊天支持

MLX-VLM 支持使用特定模型同时分析多张图像。此功能使更复杂的视觉推理任务和跨多张图像的综合分析成为可能。

### 支持的模型

以下模型支持多图像聊天：

1. Idefics 2
2. LLaVA (Interleave)
3. Qwen2-VL
4. Phi3-Vision
5. Pixtral

### 使用示例

#### Python 脚本

```python
from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config

model_path = "mlx-community/Qwen2-VL-2B-Instruct-4bit"
model, processor = load(model_path)
config = load_config(model_path)

images = ["path/to/image1.jpg", "path/to/image2.jpg"]
prompt = "比较这两张图片。"

formatted_prompt = apply_chat_template(
    processor, config, prompt, num_images=len(images)
)

output = generate(model, processor, formatted_prompt, images, verbose=False)
print(output)
```

#### 命令行

```sh
python -m mlx_vlm.generate --model mlx-community/Qwen2-VL-2B-Instruct-4bit --max-tokens 100 --prompt "比较这些图片" --image path/to/image1.jpg path/to/image2.jpg
```

## 视频理解

MLX-VLM 还支持使用特定模型进行视频分析，如字幕生成、摘要等。

### 支持的模型

以下模型支持视频聊天：

1. Qwen2-VL
2. Qwen2.5-VL
3. Idefics3
4. LLaVA

更多模型即将推出。

### 使用示例

#### 命令行
```sh
python -m mlx_vlm.video_generate --model mlx-community/Qwen2-VL-2B-Instruct-4bit --max-tokens 100 --prompt "描述这个视频" --video path/to/video.mp4 --max-pixels 224 224 --fps 1.0
```


这些示例展示了如何使用 MLX-VLM 的多图像功能进行更复杂的视觉推理任务。

# 微调

MLX-VLM 支持使用 LoRA 和 QLoRA 微调模型。

## LoRA 和 QLoRA

要了解更多关于 LoRA 的信息，请参阅 [LoRA.md](./mlx_vlm/LORA.MD) 文件。