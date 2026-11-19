"""Orquestração: ingest -> transcrição -> plano de cortes -> render -> posts.csv."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .audio import energy_curve
from .captions import build_ass
from .config import Profile
from .highlights import Clip, plan
from .ingest import ingest, is_url
from .llm import LLM
from .render import make_thumbnail, probe, render_clip, resolve_mode, render_compilation
from .transcribe import flat_words, load_transcript, save_transcript, transcribe
from .util import NO_CB, Callbacks, fmt_ts, log, slugify


@dataclass
class Options:
    profile: Profile
    out_dir: Path = Path("out")
    clips: int | None = None
    mode: str | None = None
    whisper_model: str = "small"
    language: str | None = None
    device: str = "auto"
    llm_provider: str | None = None
    llm_model: str | None = None
    transcript_path: Path | None = None
    hook: bool = True
    loudnorm: bool = True
    crf: int = 20
    preset: str = "veryfast"
    min_score: float = 0.0   # descarta cortes abaixo desta nota antes de renderizar


def workdir_for(source: str, opts: Options) -> Path:
    if is_url(source):
        m = re.search(r"[?&]v=([\w-]{6,})", source) or re.search(r"/([\w-]{6,})/?(?:\?.*)?$", source)
        name = m.group(1) if m else source
    else:
        name = Path(source).stem
    return opts.out_dir / slugify(name)


def prepare(source: str, opts: Options, workdir: Path, cb: Callbacks = NO_CB) -> tuple[Path, dict]:
    workdir.mkdir(parents=True, exist_ok=True)
    cb.stage("downloading", "Baixando o vídeo" if is_url(source) else "Lendo o arquivo")
    media = ingest(source, workdir, cb)
    tpath = workdir / "transcript.json"
    if opts.transcript_path:
        transcript = load_transcript(opts.transcript_path)
        save_transcript(transcript, tpath)
    elif tpath.exists():
        log("Transcrição já existe, reutilizando")
        transcript = load_transcript(tpath)
    else:
        cb.stage("transcribing", "Transcrevendo a fala (Whisper)")
        transcript = transcribe(media, opts.whisper_model, opts.language or opts.profile.language, opts.device, cb=cb)
        save_transcript(transcript, tpath)
    return media, transcript


def make_plan(media: Path, transcript: dict, opts: Options, workdir: Path, cb: Callbacks = NO_CB) -> list[Clip]:
    cb.stage("planning", "Escolhendo os melhores momentos")
    llm = LLM.from_env(opts.llm_provider, opts.llm_model)
    log(f"Seleção de cortes: {llm!r}" if llm else "Seleção de cortes: heurística (sem LLM configurado)")
    energy = energy_curve(media)
    cb.check()
    clips = plan(transcript, opts.profile, energy, llm, opts.clips)
    if opts.min_score > 0:
        clips = [c for c in clips if c.score >= opts.min_score]
    if not clips:
        raise RuntimeError("Nenhum corte encontrado (vídeo curto demais ou sem fala?)")
    save_clips(workdir, media, opts.profile, clips)
    return clips


def save_clips(workdir: Path, media: Path, profile: Profile, clips: list[Clip]) -> Path:
    path = workdir / "clips.json"
    path.write_text(
        json.dumps({"source": str(media), "profile": profile.name, "clips": [c.to_dict() for c in clips]},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_clips(workdir: Path) -> tuple[Path, str, list[Clip]]:
    data = json.loads((workdir / "clips.json").read_text(encoding="utf-8"))
    return Path(data["source"]), data.get("profile", "default"), [Clip.from_dict(c) for c in data["clips"]]


def _tags(clip: Clip, profile: Profile) -> list[str]:
    seen: list[str] = []
    for t in list(clip.hashtags) + list(profile.hashtags):
        if t.lower() not in (x.lower() for x in seen):
            seen.append(t)
    return seen[:8]


def _signature(c: Clip, mode: str, opts: Options) -> str:
    """Impressão digital do que define o vídeo final; permite retomar sem re-renderizar."""
    raw = json.dumps([round(c.start, 2), round(c.end, 2), c.hook, opts.hook, mode, asdict(opts.profile.caption),
                      opts.loudnorm, opts.crf, opts.preset], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def render_all(workdir: Path, media: Path, transcript: dict, clips: list[Clip], opts: Options,
               only: set[int] | None = None, cb: Callbacks = NO_CB, reuse: bool = False) -> list[Path]:
    profile = opts.profile
    info = probe(media)
    words = flat_words(transcript)
    clips_dir = workdir / "clips"
    clips_dir.mkdir(exist_ok=True)
    rendered: list[Path] = []

    todo = [i for i in range(1, len(clips) + 1) if not only or i in only]
    done = 0
    for idx, c in enumerate(clips, 1):
        if only and idx not in only:
            continue
        cb.check()
        cb.stage("rendering", f"Renderizando corte {done + 1}/{len(todo)}")
        cb.progress(done / max(1, len(todo)))
        name = f"clip_{idx:02d}"
        out_path = clips_dir / f"{name}.mp4"
        sig_path = clips_dir / f"{name}.sig"
        mode0 = opts.mode or profile.reframe
        sig = _signature(c, mode0, opts)
        if reuse and out_path.exists() and sig_path.exists() and sig_path.read_text() == sig:
            rendered.append(out_path)
            done += 1
            continue
        log(f"Renderizando {name} [{fmt_ts(c.start)}–{fmt_ts(c.end)}] score {c.score:.0f}: {c.title[:50]}")
        mode, fx = resolve_mode(opts.mode or profile.reframe, info, media, c.start, c.end)
        ass = build_ass(words, c.start, c.end, profile.caption,
                        hook=c.hook if opts.hook else None, layout="blur" if mode == "blur" else "full")
        ass_path = clips_dir / f"{name}.ass"
        ass_path.write_text(ass, encoding="utf-8")
        out = render_clip(media, c.start, c.end, ass_path, clips_dir / f"{name}.mp4", mode=mode, face_x=fx,
                          has_audio=info["has_audio"], loudnorm=opts.loudnorm, crf=opts.crf, preset=opts.preset)
        make_thumbnail(out, clips_dir / f"{name}.jpg", at=min(1.0, c.duration / 2))
        sig_path.write_text(sig)
        rendered.append(out)
        done += 1

    try:
        comp_path = workdir / "compilado_10min.mp4"
        cb.stage("rendering", "Gerando compilado 16:9 de 10+ minutos...")
        res_comp = render_compilation(media, clips, comp_path, preset=opts.preset)
        if res_comp and res_comp.exists():
            rendered.append(res_comp)
    except Exception as e:
        log(f"Compilado longo ignorado: {e}")

    cb.progress(1.0)
    write_posts_csv(workdir, clips, profile)
    return rendered


def write_posts_csv(workdir: Path, clips: list[Clip], profile: Profile) -> Path:
    path = workdir / "posts.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: abre certo no Excel
        w = csv.writer(f)
        w.writerow(["arquivo", "inicio", "fim", "duracao_s", "score", "origem", "titulo", "gancho",
                    "legenda_pronta", "hashtags", "alerta_risco", "motivo"])
        for idx, c in enumerate(clips, 1):
            tags = _tags(c, profile)
            video = workdir / "clips" / f"clip_{idx:02d}.mp4"
            w.writerow([
                f"clips/{video.name}" if video.exists() else "(não renderizado)",
                fmt_ts(c.start), fmt_ts(c.end), round(c.duration, 1), round(c.score, 1), c.source,
                c.title, c.hook, f"{c.title} {' '.join(tags)}".strip(), " ".join(tags),
                "; ".join(c.risk), c.reason,
            ])
    return path


def run(source: str, opts: Options, render: bool = True) -> dict:
    workdir = workdir_for(source, opts)
    media, transcript = prepare(source, opts, workdir)
    clips = make_plan(media, transcript, opts, workdir)
    files: list[Path] = []
    if render:
        files = render_all(workdir, media, transcript, clips, opts)
    return {"workdir": workdir, "clips": clips, "files": files}
