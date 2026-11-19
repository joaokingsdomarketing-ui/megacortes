"""Render 9:16 com FFmpeg: reenquadramento + legendas queimadas + áudio normalizado."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from .util import log

OUT_W, OUT_H = 1080, 1920


def probe(path: Path) -> dict:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(res.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise ValueError(f"{path} não tem faixa de vídeo")
    num, _, den = v.get("r_frame_rate", "30/1").partition("/")
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": float(num) / float(den or 1) if float(den or 1) else 30.0,
        "duration": float(info["format"].get("duration", 0) or 0),
        "has_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
    }


def detect_face_x(src: Path, start: float, end: float, samples: int = 10) -> float | None:
    """Posição horizontal mediana (0..1) do maior rosto no trecho; None se não achar / sem OpenCV."""
    try:
        import cv2
    except ImportError:
        log("  OpenCV ausente: modo 'face' indisponível")
        return None
    try:
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except Exception:  # versões do OpenCV sem cv2.data
        casc = None
    if casc is None or casc.empty():
        log("  detector de rosto do OpenCV indisponível nesta instalação; usando 'blur'")
        return None
    cap = cv2.VideoCapture(str(src))
    xs: list[float] = []
    for k in range(samples):
        t = start + (end - start) * (k + 0.5) / samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = casc.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
        if len(faces):
            x, _, w, h = max(faces, key=lambda f: f[2] * f[3])
            xs.append((x + w / 2) / frame.shape[1])
    cap.release()
    return float(np.median(xs)) if len(xs) >= max(2, samples // 4) else None


def resolve_mode(mode: str, info: dict, src: Path, start: float, end: float) -> tuple[str, float]:
    """Devolve (modo efetivo, face_x). Vídeo já vertical vira 'center'; 'face' sem rosto cai em 'blur'."""
    if info["height"] >= info["width"] * 16 / 9 * 0.98:
        return "center", 0.5
    if mode == "face":
        fx = detect_face_x(src, start, end)
        if fx is None:
            log("  nenhum rosto estável: usando 'blur'")
            return "blur", 0.5
        return "center", fx
    return mode, 0.5


def build_filter(mode: str, face_x: float, ass_name: str) -> str:
    if mode == "blur":
        return (
            "[0:v]split=2[a][b];"
            "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
            f"scale={OUT_W}:{OUT_H},eq=brightness=-0.06:saturation=1.1[bg];"
            f"[b]scale={OUT_W}:-2:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass={ass_name},format=yuv420p[v]"
        )
    return (
        f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H}:'clip({face_x:.4f}*in_w-{OUT_W // 2},0,in_w-{OUT_W})':'(in_h-{OUT_H})/2',"
        f"ass={ass_name},format=yuv420p[v]"
    )


def render_clip(src: Path, start: float, end: float, ass_path: Path, out_path: Path, mode: str = "blur",
                face_x: float = 0.5, has_audio: bool = True, loudnorm: bool = True,
                crf: int = 20, preset: str = "veryfast", font_dir: str | None = None) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    flt = build_filter(mode, face_x, ass_path.name)
    if font_dir:
        flt = flt.replace(f"ass={ass_path.name}", f"ass={ass_path.name}:fontsdir={font_dir}")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src.resolve()),
        "-filter_complex", flt, "-map", "[v]",
    ]
    if has_audio:
        cmd += ["-map", "0:a:0"]
        if loudnorm:
            cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
        "-movflags", "+faststart", str(out_path.resolve()),
    ]
    # cwd = pasta do .ass: evita escapar caminhos dentro do filtro
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ass_path.parent)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou em {out_path.name}:\n{res.stderr[-1200:]}")
    return out_path


def make_thumbnail(video: Path, out: Path, at: float = 1.0) -> Path | None:
    res = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at}", "-i", str(video),
         "-frames:v", "1", "-q:v", "3", str(out)],
        capture_output=True, text=True,
    )
    return out if res.returncode == 0 and out.exists() else None


def render_compilation(src: Path, clips: list, out_path: Path, max_total_sec: float = 660.0,
                       loudnorm: bool = True, crf: int = 21, preset: str = "veryfast") -> Path | None:
    """Junta os melhores trechos em um vídeo widescreen 16:9 contínuo (ideal para YouTube 10+ min)."""
    if not clips:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Ordena cronologicamente para manter o fluxo narrativo da gameplay
    sorted_clips = sorted(clips, key=lambda c: getattr(c, 'start', c.get('start', 0) if isinstance(c, dict) else 0))
    
    selected = []
    accum = 0.0
    for c in sorted_clips:
        s = getattr(c, 'start', c.get('start', 0) if isinstance(c, dict) else 0)
        e = getattr(c, 'end', c.get('end', 0) if isinstance(c, dict) else 0)
        dur = e - s
        if dur <= 0:
            continue
        selected.append((s, e))
        accum += dur
        if accum >= max_total_sec:
            break
            
    if not selected:
        return None

    filter_parts = []
    concat_inputs = []
    for i, (st, en) in enumerate(selected):
        filter_parts.append(f"[0:v]trim=start={st:.3f}:end={en:.3f},setpts=PTS-STARTPTS[v{i}];")
        filter_parts.append(f"[0:a]atrim=start={st:.3f}:end={en:.3f},asetpts=PTS-STARTPTS[a{i}];")
        concat_inputs.append(f"[v{i}][a{i}]")
        
    concat_str = "".join(concat_inputs) + f"concat=n={len(selected)}:v=1:a=1[outv][outa]"
    full_filter = "".join(filter_parts) + concat_str

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src.resolve()),
        "-filter_complex", full_filter,
        "-map", "[outv]", "-map", "[outa]",
    ]
    if loudnorm:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(out_path.resolve())
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"Aviso: falha ao gerar compilado de 10 min: {res.stderr[-500:]}")
        return None
    return out_path
