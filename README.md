# 配音混音流水线：分离原片 → 响度/动态分析 → 排 Reaper 工程 → 合并成片

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
④ assemble_rpp.py（默认 files：引用 work/mastered 成品）
        │        压缩 + 增益补偿 + 限幅已烧进 wav，打开工程就是混好的声音
        │        + 音乐/音效背景轨(4 通道) + 静音参考对白轨 + 对白触发总线
        │        输出 work/reaper/EP05_配音工程.rpp + manifest
        ▼
⑤ merge_video.py  混音（演员成品 + 音乐/音效）+ 原视频 → 成片
        │        音频窗口取原音（背景轨）的时间范围，前导/尾随内容裁掉，
        │        成片时长与视频严格一致
        │        输出 work/final/EP05_配音成片.mp4
        ▼
⑥ Reaper 直接打开 .rpp 即可试听/微调轨道音量；
         想要可调的效果器链（非破坏式）则用 --mix-mode raw 重新生成，
         再跑 scripts/apply_mix.lua 一键加压缩 + 增益补偿 + 限幅 + 侧链 ducking
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

   对“动态损失”较大的录音（整条电平悬在高位、几乎没有安静段落，如手机/
   直播麦录的 Adrian），策略是**连续的、全组相对的**而不是一刀切：每条轨
   算一个 hotness 指数（安静地板相对全组中位数高多少 × 说话密度），越“热”
   的轨压缩越轻（比例从 3.2:1 连续降到 1.3:1、阈值从 p15 向 p50 靠拢），
   保住仅剩的动态；响度控制改由**音量**承担——presence trim 最多从目标响度
   里扣掉 `--presence-trim-max`（默认 4.0）dB，再用参照轨 p85 做顶部锚点，
   顶部仍然过热就继续转成音量衰减。全组都正常时 hotness≈0，退化为普通响度
   匹配；多条轨异常时各自按自己的 hotness 调整。

   已渲染过的轨默认跳过（沿用上次测量）；改了策略参数后要重测重渲染请加
   `--force`：
   ```bash
   python master.py --actors test --force --presence-trim-max 4.0
   ```

3. 生成 Reaper 工程（默认 files 模式 = 自动调音量 + 压缩/限制已生效）：
   ```bash
   python assemble_rpp.py --actors test
   ```
   5 条演员轨直接引用 `work/mastered/` 的渲染成品——`master.py` 算好的
   **压缩（阈值/比例）+ 增益补偿 + 限幅（-0.45 dB）已经烧进 wav**，
   打开工程听到的就是自动调整后的混音，任何机器声音都一样。
   音乐、音效各占一条背景轨（4 通道），分离出的对白作为静音参考轨方便对位。
   人声组整体电平默认 **+1 dB**（`--voice-gain-db` 调整，0 关闭），
   files 模式写到轨道音量（VOLPAN），成片合并时用同一参数保持一致。

   工程始终输出到同一个文件 `work/reaper/EP05_配音工程.rpp`（重复运行会
   直接覆盖，不会累积）。只有手动传 `--out` 才会生成带后缀的变体文件；
   交付前请只保留要用的那一份，其余删除，避免 Reaper 里打开错文件。

   想要"非破坏式、可在 Reaper 里随时拧参数"的效果器链（轨道音量对齐 +
   手动加压缩/限幅/侧链），用 raw 模式：
   ```bash
   python assemble_rpp.py --actors test --mix-mode raw
   ```
   它引用 `work/enhanced/` 的干声，把响度增益写到轨道音量（VOLPAN），
   48k/单声道之类的异格式源交给 Reaper 自己重采样，源文件一个字节都不动。

4. 合并成片（混音 + 原视频，时长严格一致）：
   ```bash
   python merge_video.py
   ```
   自动把 `work/mastered/` 的演员成品 + `work/separated/` 的音乐/音效按
   0 dB 混成一条（与 files 模式工程听到的一致，静音参考轨和触发总线除外），
   再与 `test/原片.mp4` 合并输出 `work/final/EP05_配音成片.mp4`。关键点：
   **不按混音本身的长度裁头尾**，而是取原音（背景轨）的时间范围作为窗口
   ——对轨导致的人声前导（人声轨靠前）和尾随多余时长会被一并裁掉，成片
   时长与视频严格一致（视频流原样复制，音频转 AAC 48k 立体声）。
   人声组增益默认与工程一致（读 manifest 的 `voice_gain_db`，+1 dB）；
   想临时只改成片试听用 `--voice-gain-db 3`，定了之后用同值重跑 assemble
   写入工程，两边保持一致。

5. （仅 raw 模式）Reaper 里一键加压缩/限制/侧链（非破坏式，可随时调）：
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

6. Reaper 打开 `work/reaper/EP05_配音工程.rpp` 开始混音；交付成片在
   `work/final/EP05_配音成片.mp4`。

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
