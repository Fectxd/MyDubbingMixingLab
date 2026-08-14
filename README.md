# 配音混音流水线：分离原片 → 响度/动态分析 → 排 Reaper 工程

把演员干声（已对好轨的整轨）和原片背景音一起排进 Reaper 工程做混音：

```
原片.mp4 + 5 条演员干声（test/）
        │
        ▼
① separate.py  ffmpeg 解音(44.1k/立体声) → TIGER-DnR 分离
        │        输出 work/separated/：对白 / 音效 / 音乐 + 参考混音 + report.json
        ▼
② enhance.py  可选：NVIDIA RE-USE 修复低质量干声（需要 GPU）
        │        输出 work/enhanced/
        ▼
③ master.py  对照原片对白测每轨“说话电平 + 动态范围”，低阈值压缩把
        │        安静台词拉起来，输出 work/mastered/master_report.json（增益/压缩参数）
        ▼
④ assemble_rpp.py（默认 raw 非破坏式）
        │        5 条演员整轨(0 位) + 轨道音量响度对齐
        │        + 音乐/音效背景轨(4 通道) + 静音参考对白轨 + 对白触发总线
        │        输出 work/reaper/EP05_配音工程.rpp + manifest + dynamics
        ▼
⑤ Reaper 打开 .rpp → 跑 scripts/apply_mix.lua 一键加
        压缩 + 增益补偿 + 限幅 + 对白触发侧链 ducking
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

2. 响度/动态分析（对照原片对白轨）：
   ```bash
   python master.py --actors test
   ```
   输出 `work/mastered/master_report.json`：每条轨的说话电平中位数、动态范围、
   压缩阈值/比例、限幅阈值和增益。策略是“电平拉平”而不是只压峰值：
   压缩阈值取在整段对白的较低分位（p15），中低比例（约 1.5~4:1），
   压缩后再用增益补偿把整轨抬到参照轨的响度——这样安静台词会被一起拉起来，
   Adrian 那种本身很大声的轨也不会再盖住别人。

3. 生成 Reaper 工程（默认非破坏式 raw 模式）：
   ```bash
   python assemble_rpp.py --actors test
   ```
   直接引用 `work/enhanced/` 的干声（不转码、不写回），把响度对齐的增益写到
   **轨道音量（VOLPAN）**，音乐、音效各占一条背景轨，分离出的对白作为静音
   参考轨方便对位。48k/单声道之类的异格式源交给 Reaper 自己重采样，源文件
   一个字节都不动。

   想要“确定性交付版”（压缩+限幅已渲染进 wav，任何机器打开声音都一样）：
   ```bash
   python assemble_rpp.py --actors test --mix-mode files
   ```
   它优先引用 `work/mastered/` 的渲染结果。

4. Reaper 里一键加压缩/限制/侧链（非破坏式，可随时调）：
   打开 `work/reaper/EP05_配音工程.rpp` → Actions（默认 `?` 键）→
   ReaScript: Load… → 选 `scripts/apply_mix.lua` → Run。它会做三件事：
   - 每条演员轨加 ReaComp（低阈值压缩）+ JS 增益补偿 + ReaLimit（-0.45 dB
     天花板），并把轨道音量归 0 dB——响度补偿放进效果器链里，限幅器才真正
     兜得住峰值（如果补偿在轨道音量上，会被限幅器之后再次抬过头）；
   - 建立「对白触发」总线：5 条演员轨送进去，总线再送到背景音乐/音效的
     3/4 通道，两条背景轨各加一个探测 Aux 输入的 ReaComp——只要有人开口，
     背景自动让路，对白一停就恢复；
   - 脚本可重复运行，不会重复插入效果器。之后直接在 Reaper 里拧参数即可。

   想单独只加压缩/限制（不要侧链），跑 `scripts/apply_dynamics.lua`；
   只想加侧链 ducking，跑 `scripts/apply_sidechain.lua`。

5. Reaper 打开 `work/reaper/EP05_配音工程.rpp` 开始混音。

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

- 分离不是无损的，背景轨可能残留原片对白；工程已内置对白触发侧链
  （`scripts/apply_sidechain.lua` / `apply_mix.lua`），必要时也可把静音参考
  对白轨临时打开来核对口型。
- 本机是 Windows ARM64：没有 torchaudio 的 ARM 版，音频读写走 ffmpeg +
  soundfile，`separate.py` 不需要 torchaudio。
- raw 模式下响度对齐在轨道音量上，压缩/限制靠 Reaper 原生 FX；如果
  想要离线渲染的最终版（比如要交付混音），用 `--mix-mode files` 或直接
  在 Reaper 里 File → Render。
