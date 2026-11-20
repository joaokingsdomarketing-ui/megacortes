"""Entrada: arquivo local ou URL (yt-dlp), com progresso, cancelamento e expansão de playlists."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

from .util import NO_CB, Callbacks, log

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".m4a"}
_PCT = re.compile(r"\[download\]\s+([\d.]+)%")


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip(), re.IGNORECASE))


def _ytdlp_base() -> list[str]:
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--extractor-args", "youtube:player_client=android",
        "--compat-options", "no-youtube-unavailable-videos",
        "--no-check-certificates",
    ]

    cookies = os.getenv("MEGACORTES_COOKIES")
    if cookies and Path(cookies).is_file():
        try:
            tmp_cookies = Path("/tmp/yt_cookies.txt")
            shutil.copyfile(cookies, tmp_cookies)
            os.chmod(tmp_cookies, 0o666)
            cmd += ["--cookies", str(tmp_cookies)]
            log(f"Usando cookies autenticados: {cookies}")
        except Exception as e:
            log(f"Aviso ao preparar cookies: {e}")

    return cmd


def ingest(source: str, workdir: Path, cb: Callbacks = NO_CB) -> Path:
    """Devolve o caminho absoluto de um arquivo de mídia local."""
    if not is_url(source):
        p = Path(source).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {source}")
        return p.resolve()

    workdir.mkdir(parents=True, exist_ok=True)
    existing = _find_download(workdir)
    if existing:
        log(f"Download já existe, reutilizando: {existing.name}")
        return existing

    log("Baixando com yt-dlp (use apenas conteúdo seu ou com autorização)...")
    cmd = _ytdlp_base() + [
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b/best",
        "--merge-output-format", "mp4",
        "--no-playlist", "--newline", "--write-info-json",
        "-o", str(workdir / "source.%(ext)s"),
        source,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    tail: deque[str] = deque(maxlen=12)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            tail.append(line.rstrip())
            m = _PCT.search(line)
            if m:
                cb.progress(min(1.0, float(m.group(1)) / 100))
            if cb.cancelled():
                proc.terminate()
                cb.check()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        err_msg = "\n".join(tail)[-800:]
        raise RuntimeError(f"yt-dlp falhou:\n{err_msg}")
    out = _find_download(workdir)
    if not out:
        raise RuntimeError("yt-dlp terminou mas não encontrei o arquivo baixado")
    return out


def _find_download(workdir: Path) -> Path | None:
    for p in sorted(workdir.glob("source.*")):
        if p.suffix.lower() in VIDEO_EXTS:
            return p.resolve()
    return None


def video_title(workdir: Path, fallback: str = "") -> str:
    """Título do vídeo baixado (lido do .info.json do yt-dlp)."""
    info = workdir / "source.info.json"
    try:
        return str(json.loads(info.read_text(encoding="utf-8")).get("title") or fallback)
    except (OSError, ValueError):
        return fallback


def expand_playlist(url: str, max_items: int = 20, depth: int = 3) -> list[str]:
    """Se for playlist/canal, devolve as URLs dos vídeos (até max_items); senão, [url]."""
    cmd = _ytdlp_base() + ["--flat-playlist", "--dump-single-json", "--playlist-end", str(max_items), url]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if res.returncode != 0:
        err_msg = res.stderr[-500:]
        raise RuntimeError(f"yt-dlp não conseguiu listar o link:\n{err_msg}")
    try:
        data = json.loads(res.stdout)
    except ValueError as e:
        raise RuntimeError("Resposta inesperada do yt-dlp ao listar o link") from e
    urls = _flatten_entries(data, depth)
    uniq = list(dict.fromkeys(urls))[:max_items]
    return uniq or [url]


def _flatten_entries(node: dict, depth: int) -> list[str]:
    out: list[str] = []
    for e in node.get("entries") or []:
        if not e:
            continue
        if e.get("entries") and depth > 0:
            out.extend(_flatten_entries(e, depth - 1))
            continue
        if str(e.get("ie_key", "")).lower().endswith("tab"):
            continue
        u = e.get("webpage_url") or e.get("url")
        if not u and e.get("id") and "youtube" in str(e.get("ie_key", "")).lower():
            u = f"https://www.youtube.com/watch?v={e['id']}"
        if u and is_url(u):
            out.append(u)
    return out
