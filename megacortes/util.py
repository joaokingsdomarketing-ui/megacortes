"""Utilidades pequenas compartilhadas pelo pipeline."""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

_T0 = time.time()


class Cancelled(Exception):
    """Lançada quando o usuário cancela um processamento em andamento."""


def _noop(*_a, **_k) -> None:
    return None


def _never() -> bool:
    return False


@dataclass
class Callbacks:
    """Ganchos opcionais para a interface web acompanhar e cancelar o pipeline."""
    stage: Callable[[str, str], None] = field(default=_noop)   # (etapa, mensagem)
    progress: Callable[[float], None] = field(default=_noop)   # 0..1 dentro da etapa atual
    cancelled: Callable[[], bool] = field(default=_never)

    def check(self) -> None:
        if self.cancelled():
            raise Cancelled()


NO_CB = Callbacks()


def log(msg: str) -> None:
    print(f"[{time.time() - _T0:6.1f}s] {msg}", file=sys.stderr, flush=True)


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text or "video")[:max_len].strip("-") or "video"


def fmt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:04.1f}" if h else f"{m:d}:{s:04.1f}"


def load_dotenv(path: str = ".env") -> None:
    """Leitor mínimo de .env (sem dependência extra). Não sobrescreve o ambiente."""
    import os
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            os.environ.setdefault(k.strip(), v)
