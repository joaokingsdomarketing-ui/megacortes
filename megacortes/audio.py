"""Curva de energia do áudio: gritos, risadas e reações costumam virar bons cortes."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np


@dataclass
class EnergyCurve:
    hop: float
    values: np.ndarray  # 0..1 por janela de `hop` segundos

    def stats(self, start: float, end: float) -> tuple[float, float]:
        """(média, pico) de energia no intervalo."""
        if self.values.size == 0:
            return 0.0, 0.0
        i = max(0, int(start / self.hop))
        j = min(self.values.size, max(i + 1, int(np.ceil(end / self.hop))))
        seg = self.values[i:j]
        if seg.size == 0:
            return 0.0, 0.0
        return float(seg.mean()), float(seg.max())


EMPTY = EnergyCurve(0.5, np.zeros(0, dtype=np.float32))


def energy_curve(media, hop: float = 0.5, sr: int = 16000) -> EnergyCurve:
    cmd = ["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = int(hop * sr) * 2
    vals: list[float] = []
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < 2:
            break
        x = np.frombuffer(buf[: len(buf) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        vals.append(float(np.sqrt(np.mean(x * x)) + 1e-9))
    proc.stdout.close()
    proc.wait()
    if not vals:
        return EnergyCurve(hop, np.zeros(0, dtype=np.float32))

    db = 20 * np.log10(np.asarray(vals, dtype=np.float32))
    lo, hi = np.percentile(db, 10), np.percentile(db, 98)
    norm = np.clip((db - lo) / max(hi - lo, 1e-6), 0.0, 1.0).astype(np.float32)
    return EnergyCurve(hop, norm)
