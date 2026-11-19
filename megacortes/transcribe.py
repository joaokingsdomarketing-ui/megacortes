"""Transcrição com timestamps por palavra (faster-whisper) + utilidades.

Formato interno:
{"language": "pt", "duration": 123.4,
 "segments": [{"start": 0.0, "end": 3.2, "text": "...",
               "words": [{"start": 0.0, "end": 0.4, "word": "Olha"}, ...]}]}
"""
from __future__ import annotations

import json
from pathlib import Path

from .util import NO_CB, Callbacks, log


def transcribe(media: Path, model_size: str = "small", language: str | None = None,
               device: str = "auto", compute_type: str = "auto", cb: Callbacks = NO_CB) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("faster-whisper não instalado: pip install faster-whisper") from e

    log(f"Transcrevendo com faster-whisper ({model_size}, device={device})...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(media), language=language, word_timestamps=True, vad_filter=True, beam_size=5
    )
    out = {"language": info.language, "duration": float(info.duration), "segments": []}
    for seg in segments:
        cb.check()
        if info.duration:
            cb.progress(min(1.0, float(seg.end) / float(info.duration)))
        words = [
            {"start": float(w.start), "end": float(w.end), "word": w.word}
            for w in (seg.words or [])
            if w.start is not None and w.end is not None
        ]
        out["segments"].append(
            {"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip(), "words": words}
        )
    return ensure_words(out)


def ensure_words(transcript: dict) -> dict:
    """Garante timestamps por palavra: distribui uniformemente quando só há segmentos."""
    for seg in transcript.get("segments", []):
        if seg.get("words"):
            continue
        toks = seg.get("text", "").split()
        if not toks:
            seg["words"] = []
            continue
        step = (seg["end"] - seg["start"]) / len(toks)
        seg["words"] = [
            {"start": seg["start"] + i * step, "end": seg["start"] + (i + 1) * step, "word": t}
            for i, t in enumerate(toks)
        ]
    return transcript


def flat_words(transcript: dict) -> list[dict]:
    words: list[dict] = []
    for seg in transcript.get("segments", []):
        words.extend(seg.get("words", []))
    words.sort(key=lambda w: w["start"])
    return words


def save_transcript(transcript: dict, path: Path) -> None:
    path.write_text(json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")


def load_transcript(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "segments" not in data:
        raise ValueError(f"{path} não parece uma transcrição (falta 'segments')")
    data = ensure_words(data)
    if not data.get("duration"):
        words = flat_words(data)
        data["duration"] = words[-1]["end"] if words else 0.0
    return data
