# 配音混音流水线：分离原片 → 排 Reaper 工程

把演员干声（已对好轨的整轨）和原片背景音一起排进 Reaper 工程做混音：

```
原片.mp4 + 5 条演员干声（test/）
        │
        ▼
① separate.py  ffmpeg 解音(44.1k/立体声) → TIGER-DnR 分离
        │        输出 work/separated/：对白 / 音效 / 音乐 + 参考混音 + report.json
        ▼
② assemble_rpp.py  5 条演员整轨(0 位) + 音乐/音效背景轨 + 静音参考对白轨
        │        输出 work/reaper/EP05_配音工程.rpp + manifest.json
        ▼
③ 用 Reaper 打开 .rpp 直接混音
```

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Windows ARM64 / CPU 机器（本机）：
pip install --index-url https://download.pytorch.org/whl/cpu torch
```

首次运行 `separate.py` 会从 Hugging Face 下载 TIGER-DnR 权重（约 17MB，
缓存到 `models/hf/`），之后离线可用。

## 用法

1. 分离原片（对白 / 音效 / 音乐）：
   ```bash
   python separate.py --input test/原片.mp4
   ```
   输出在 `work/separated/`。CPU 上 114 秒原片大约要跑 30~60 分钟，
   脚本会按 12 秒分块打印进度（对白/音效/音乐 第 i/N 段）。

2. 生成 Reaper 工程：
   ```bash
   python assemble_rpp.py --actors test
   ```
   自动把 `test/` 下所有 wav 当作演员整轨，48k/单声道之类的异格式轨会
   先转成 44.1k/16bit/立体声（存 `work/processed/`），其余原样引用；
   音乐、音效各占一条背景轨，分离出的对白作为静音参考轨方便对位。

3. Reaper 打开 `work/reaper/EP05_配音工程.rpp` 开始混音。

## 可选：逐句自动对轨（easyaligner）

如果以后拿到未对齐的干声，`run_pipeline.py` 保留原来的字幕逐句对齐链路：

```bash
pip install easyaligner
python run_pipeline.py --manifest manifest.json
```

流程：VAD → CTC 词级对齐（默认西语模型，中文自动切 mms-1b）→ 按字幕
in/out 生成摆放计划（±5% 内自动写 PLAYRATE）→ reathon 生成 .RPP 和
逐句报告。`--skip-align` 可跳过对齐直接出工程。测试见 `scripts/smoke_test.py`。

## 云端跑：GitHub Actions（推荐，不用本地装依赖）

本地机器装 PyTorch 太慢/太老显卡驱动不方便时，可以直接丢到 GitHub Actions
免费跑分离：

1. 把本目录推送到 GitHub（新建仓库，公开或私有均可）：
   ```bash
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<你的用户名>/<仓库名>.git
   git push -u origin main
   ```
2. 确保原片在仓库里（例如 `test/原片.mp4`，网页上传或 git push 都行）。
3. GitHub 网页打开仓库 → Actions → 左侧 "TIGER-DnR 分离" → Run workflow →
   填文件路径 → 跑。
4. 跑完在本次运行页面下载 `separated-stems` 工件，里面就是对白/音效/音乐
   三个 wav + report。

说明：公开仓库的标准 runner 免费不限量，私有仓库每月 2000 分钟免费，跑一次
CPU 分离约 30~60 分钟，足够用。公开仓库还可改用 GPU runner（几分钟跑完）。
模型权重每次由 runner 自动下载，不用提交进仓库。

## 低质量干声修复：NVIDIA RE-USE（需要 GPU）

演员干声如果来自手机/低质量麦克风，先修复再进工程：

```bash
python enhance.py --inputs test/*.wav --outdir work/enhanced
```

RE-USE 做降噪/去混响/带宽扩展/低质量麦克风修复。注意：
- 官方模型**只支持 CUDA**（拒绝 CPU），用 Google Colab/Kaggle 免费 T4 GPU 跑。
- 整条流水线的 Colab 一键版见 `colab_full_pipeline.ipynb`（GitHub 打开 →
  Open in Colab）：上传原片 + 干声 → 分离 → 修复 → 排工程 → 打包下载。
- 非商用许可（NSCLv1）：论文/非商业用途没问题。
- 修复后的文件放 `work/enhanced/`，`assemble_rpp.py` 会自动优先使用它们
  （有修复用修复，没有则用原始干声）。

## 已知边界

- 分离不是无损的，背景轨可能残留原片对白；正式混音时建议给背景做
  sidechain ducking，或把静音参考对白轨临时打开来核对口型。
- 本机是 Windows ARM64：没有 torchaudio 的 ARM 版，音频读写走 ffmpeg +
  soundfile，`separate.py` 不需要 torchaudio。
- 本次排工程不做响度统一；后续可接入 pyloudnorm 逐轨对齐响度。
