在 1060 3G 机器上跑 TIGER-DnR 分离
==================================

本包内含：separate.py（主脚本）、vendor\（模型代码）、requirements_gpu.txt、
run_separate.bat（一键运行，默认走 CPU，不需要显卡驱动）。

需要先装好：
1. Python 3.10 ~ 3.14（64 位）：https://www.python.org/downloads/
   安装时务必勾选 "Add to PATH"。
2. ffmpeg（能直接在命令行敲 ffmpeg 即可）；或者直接用 wav 文件，跳过 ffmpeg。

步骤：
1. 把原片（如 原片.mp4 或 wav）复制到本文件夹。
2. 双击 run_separate.bat，或把文件拖到 run_separate.bat 上。
3. 第一次运行会自动建环境、下载 CPU 版 PyTorch 和模型权重
   （约 17MB，缓存到 models\hf），需要几分钟，请等它跑完。
4. 跑完后结果在 work\separated\：
     原片_dialog.wav   对白
     原片_effect.wav   音效
     原片_music.wav    音乐
     原片_mix44k.wav   参考混音（44.1k 立体声）
     原片_report.json  报告

进度与断点：
- 脚本按窗口处理（默认 12 秒窗口、4 秒步进、8 秒重叠，每个采样点取 3 个
  窗口结果的平均，边界是平滑过渡，不是硬切）。
- 打印"对白/音效/音乐 第 i/N 段"进度；每个子模型算完立刻存盘。
- 想给模型更多上下文，可加参数：separate.py --input 原片.mp4 --chunk 20
  （窗口越大越慢；12/20/30 秒都是合理值）。

下载模型权重超时（国内网络常见）：
脚本已默认使用镜像 hf-mirror.com。如果还失败，手动执行：
  $env:HF_ENDPOINT = "https://hf-mirror.com"
  .venv\Scripts\python separate.py --input "原片.mp4" --device auto

常见问题：
- "NVIDIA driver is too old" 警告：可以忽略，脚本会自动改用 CPU。
- 想用 1060 的 GPU 加速、但更新驱动会死机：见下面"老驱动 GPU 加速"。
- 提示找不到 ffmpeg：装 ffmpeg 并加入 PATH，或先把原片转成 wav 再拖给脚本。
- 3G 显存完全够用（模型权重只有 17MB）。

老驱动 GPU 加速（可选，需要 Python 3.10）：
驱动只支持到 CUDA 11.4 时，能配合的最新 PyTorch 是 2022 年的 torch 1.12.1
（cu113）。步骤：
  1. 安装 Python 3.10（64 位）：https://www.python.org/downloads/
  2. 在命令行执行：
       py -3.10 -m venv .venv_gpu
       .venv_gpu\Scripts\python -m pip install --upgrade "pip<24"
       .venv_gpu\Scripts\python -m pip install torch==1.12.1+cu113 --index-url https://download.pytorch.org/whl/cu113
       .venv_gpu\Scripts\python -m pip install -r requirements_gpu_olddriver.txt
  3. 验证 GPU：
       .venv_gpu\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
     看到 True NVIDIA GeForce GTX 1060 就说明 GPU 可用。
  4. 跑：
       .venv_gpu\Scripts\python separate.py --input "原片.mp4" --device auto
  说明：这套是 2022 年的老工具链，能用但别乱升级依赖；装完第一次仍会从
  镜像下载 17MB 权重（如果之前 CPU 版已下载过，直接复用 models\hf，不用再下）。
