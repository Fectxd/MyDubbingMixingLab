"""easyaligner 强制对齐：干声 + 已知台词 → 句级/词级时间戳。

这一层是唯一依赖 torch / easyaligner / transformers 的地方；
其余模块保持轻量，方便在没有 GPU 的机器上只重建 Reaper 工程。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .manifest import Manifest, TrackSpec
from .transcript import SubtitleLine

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# 按语言给一个开箱即用的 CTC 模型，manifest 里可覆盖。
# 候选：jonatasgrosman/wav2vec2-large-xlsr-53-spanish（西语）、
# facebook/mms-1b-all（多语，含中文）、facebook/wav2vec2-base-960h（英语）。
DEFAULT_EMISSIONS_MODELS = {
    "es": "jonatasgrosman/wav2vec2-large-xlsr-53-spanish",
    "en": "facebook/wav2vec2-base-960h",
    "zh": "facebook/mms-1b-all",
    "ca": "facebook/mms-1b-all",
}


def resolve_device(requested: str | None) -> str:
    if requested in ("cpu", "cuda"):
        return requested
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _detect_word_boundary(processor) -> str | None:
    """部分 MMS 词表没有 '|' 边界符，按需关闭 word_boundary。"""
    vocab = processor.tokenizer.get_vocab()
    return "|" if "|" in vocab else None


def _build_speech(track: TrackSpec, lines_by_id: dict[int, SubtitleLine]):
    """把该文件按顺序包含的字幕行拼成一段文本，并记录每行的字符区间。"""
    from easyaligner.data.datamodel import SpeechSegment

    parts = [lines_by_id[i].text.strip() for i in track.line_ids]
    text = " ".join(parts)
    spans: list[tuple[int, int]] = []
    pos = 0
    for part in parts:
        start = pos
        pos += len(part)
        spans.append((start, pos))
        pos += 1  # 行之间的空格
    return SpeechSegment(
        speech_id=0,
        start=None,
        end=None,
        text=text,
        text_spans=spans,
        metadata={"actor": track.actor, "file": track.file},
    )


def run_alignment(
    manifest: Manifest, lines_by_id: dict[int, SubtitleLine]
) -> dict[str, Path]:
    """跑完整 easyaligner 流水线，返回 {干声相对路径: 对齐JSON路径}。"""
    from easyaligner.pipelines import pipeline as run_easyaligner
    from easyaligner.text.normalization import text_normalizer
    from easyaligner.vad.silero import load_vad_model
    from transformers import AutoModelForCTC, Wav2Vec2Processor

    device = resolve_device(manifest.device)
    half = manifest.half if manifest.half is not None else device == "cuda"
    logger.info("device=%s half=%s", device, half)

    model_name = manifest.emissions_model or DEFAULT_EMISSIONS_MODELS.get(
        manifest.language, DEFAULT_EMISSIONS_MODELS["es"]
    )

    logger.info("加载 VAD 模型…")
    vad_model = load_vad_model()
    logger.info("加载 CTC 模型 %s …", model_name)
    processor = Wav2Vec2Processor.from_pretrained(model_name)
    model = AutoModelForCTC.from_pretrained(model_name)
    model.to(device)
    if half and device == "cuda":
        model = model.half()

    audio_paths: list[str] = []
    speeches: list[list] = []
    for track in manifest.tracks:
        if not track.line_ids:
            raise ValueError(f"track {track.file}: 没有配置 line_ids，无法对齐")
        if not (manifest.audio_root / track.file).exists():
            raise FileNotFoundError(f"干声文件不存在：{manifest.audio_root / track.file}")
        audio_paths.append(track.file)
        speeches.append([_build_speech(track, lines_by_id)])

    blank_id = processor.tokenizer.pad_token_id or 0
    word_boundary = _detect_word_boundary(processor)
    logger.info("blank_id=%s word_boundary=%s", blank_id, word_boundary)

    run_easyaligner(
        vad_model=vad_model,
        emissions_model=model,
        processor=processor,
        audio_paths=audio_paths,
        audio_dir=str(manifest.audio_root),
        speeches=speeches,
        sample_rate=SAMPLE_RATE,
        chunk_size=manifest.chunk_size,
        alignment_strategy="speech",
        text_normalizer_fn=text_normalizer,
        tokenizer=None,  # 每行区间已由 text_spans 指定
        start_wildcard=manifest.start_wildcard,
        end_wildcard=manifest.end_wildcard,
        blank_id=blank_id,
        word_boundary=word_boundary,
        ndigits=5,
        num_workers_files=0,  # Windows 下 DataLoader 多进程容易出问题，先关掉
        prefetch_factor_files=2,
        batch_size_features=8,
        num_workers_features=0,
        streaming=False,
        save_json=True,
        save_msgpack=False,
        save_emissions=True,
        return_alignments=False,
        delete_emissions=True,  # 对齐完删掉中间特征，省磁盘
        output_vad_dir=str(manifest.work_dir / "vad"),
        output_emissions_dir=str(manifest.work_dir / "emissions"),
        output_alignments_dir=str(manifest.alignments_dir),
        device=device,
    )

    results: dict[str, Path] = {}
    for track in manifest.tracks:
        json_path = manifest.alignments_dir / Path(track.file).with_suffix(".json")
        if not json_path.exists():
            raise FileNotFoundError(f"对齐输出缺失：{json_path}")
        results[track.file] = json_path
    logger.info("对齐完成，共 %d 个文件", len(results))
    return results
