"""Fila de processamento: jobs com vários vídeos, progresso, cancelamento, retomada e re-render de um corte."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from ..config import Profile, list_profiles, load_profile, validate_profile
from ..highlights import Clip, risk_flags, span_text
from ..ingest import expand_playlist, is_url, video_title
from ..pipeline import Options, _tags, load_clips, make_plan, prepare, render_all, save_clips
from ..transcribe import flat_words, load_transcript
from ..util import Callbacks, Cancelled, log, slugify
from .presets import CAPTION_FIELDS

ID_RE = re.compile(r"^[a-f0-9]{8,32}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")
STAGE_RANGE = {  # fatia da barra de progresso de cada etapa
    "queued": (0.0, 0.0), "expanding": (0.0, 0.02), "downloading": (0.02, 0.15),
    "transcribing": (0.15, 0.45), "planning": (0.45, 0.50), "rendering": (0.50, 1.0),
}
ACTIVE = ("expanding", "downloading", "transcribing", "planning", "rendering")


class JobError(ValueError):
    """Pedido inválido (vira HTTP 400 com a mensagem)."""


def _num(v: Any, lo: float, hi: float, default: float, name: str) -> float:
    if v in (None, ""):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise JobError(f"'{name}' deve ser um número")
    if not lo <= f <= hi:
        raise JobError(f"'{name}' deve ficar entre {lo:g} e {hi:g}")
    return f


def parse_keywords(text: str) -> dict:
    """'palavra: peso' por linha (peso opcional = 1)."""
    out: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, w = line.rpartition(":") if ":" in line else (line, "", "")
        if not k:
            k, w = w, ""
        try:
            out[k.strip().strip('"')] = float(w) if w.strip() else 1.0
        except ValueError:
            out[line.strip('"')] = 1.0
    return out


def build_profile(o: dict) -> Profile:
    """Perfil base + ajustes feitos na interface (cores, tamanho, duração, nicho...)."""
    pid = str(o.get("profile") or "default")
    if pid not in list_profiles():
        raise JobError(f"Perfil desconhecido: {pid}")
    p = load_profile(pid)
    cap = replace(p.caption)
    for k, v in (o.get("caption") or {}).items():
        kind = CAPTION_FIELDS.get(k)
        if kind is None or v in (None, ""):
            continue
        if kind == "color":
            if not COLOR_RE.match(str(v)):
                raise JobError(f"Cor inválida em '{k}': {v}")
            setattr(cap, k, str(v).upper())
        elif kind is bool:
            setattr(cap, k, bool(v))
        elif kind is int:
            setattr(cap, k, int(_num(v, 0, 2000, 0, k)))
        else:
            setattr(cap, k, float(_num(v, 0, 30, 0, k)))
    p.caption = cap
    p.min_seconds = _num(o.get("min_seconds"), 5, 170, p.min_seconds, "duração mínima")
    p.max_seconds = _num(o.get("max_seconds"), 6, 180, p.max_seconds, "duração máxima")
    p.clips = int(_num(o.get("clips"), 1, 500, p.clips, "cortes por vídeo"))
    if o.get("niche_prompt"):
        p.niche_prompt = str(o["niche_prompt"])[:4000]
    if o.get("keywords"):
        p.keywords = {**p.keywords, **parse_keywords(str(o["keywords"])[:4000])}
    tags = [t if t.startswith("#") else f"#{t}" for t in re.split(r"[\s,]+", str(o.get("hashtags") or "")) if t.strip("#")]
    if tags:
        p.hashtags = tags[:12]
    if o.get("reframe") in ("blur", "center", "face"):
        p.reframe = o["reframe"]
    validate_profile(p)
    return p


def build_options(o: dict, out_dir: Path) -> Options:
    wm = str(o.get("whisper_model") or "small")
    if wm not in WHISPER_MODELS:
        raise JobError(f"Modelo Whisper inválido: {wm}")
    dev = str(o.get("device") or "auto")
    if dev not in ("auto", "cpu", "cuda"):
        raise JobError("device deve ser auto, cpu ou cuda")
    prov = str(o.get("llm_provider") or "") or None
    if prov == "auto":
        prov = None
    if prov is None:  # automático: usa a IA se houver chave; se a configuração estiver incompleta, segue sem IA
        from ..llm import LLM, LLMError
        try:
            LLM.from_env(None, None)
        except LLMError:
            prov = "none"
    return Options(
        profile=build_profile(o), out_dir=out_dir, clips=None, mode=o.get("reframe") or None,
        whisper_model=wm, language=(str(o.get("language") or "").strip() or None), device=dev,
        llm_provider=prov, llm_model=(str(o.get("llm_model") or "").strip() or None),
        hook=bool(o.get("hook", True)), min_score=_num(o.get("min_score"), 0, 100, 0, "nota mínima"),
        preset=str(o.get("render_preset") or "veryfast") if o.get("render_preset") in ("ultrafast", "veryfast", "medium") else "veryfast",
    )


@dataclass
class Item:
    id: int
    source: str
    expand: bool = False
    title: str = ""
    key: str = ""
    status: str = "queued"      # queued|expanding|downloading|transcribing|planning|rendering|done|error|canceled|expanded
    message: str = ""
    progress: float = 0.0
    clips_total: int = 0
    error: str = ""
    seconds: float = 0.0


@dataclass
class Job:
    id: str
    created: float
    options: dict
    items: list = field(default_factory=list)
    status: str = "queued"      # queued|running|done|error|canceled
    name: str = ""
    max_videos: int = 20
    cancel: bool = False

    def public(self) -> dict:
        its = [i for i in self.items if i.status != "expanded"]
        prog = sum(1.0 if i.status in ("done", "error", "canceled") else i.progress for i in its) / max(1, len(its))
        d = {"id": self.id, "created": self.created, "name": self.name, "status": self.status,
             "progress": round(prog, 3), "options": self.options,
             "counts": {s: sum(1 for i in its if i.status == s) for s in {i.status for i in its}},
             "clips_total": sum(i.clips_total for i in its),
             "items": [asdict(i) for i in its]}
        return d


class Manager:
    def __init__(self, data_dir: Path, workers: int = 1):
        self.data = Path(data_dir).resolve()
        self.out = self.data / "out"
        self.library = self.data / "library"       # downloads + transcrições reaproveitados entre jobs
        self.uploads = self.data / "uploads"
        self.videos = self.data / "videos"         # pasta para soltar arquivos locais
        self.jobs_dir = self.data / "jobs"
        for d in (self.out, self.library, self.uploads, self.videos, self.jobs_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self.busy: set[tuple[str, int, int]] = set()   # cortes em re-render
        self.cv = threading.Condition(self.lock)
        self.queue: list[tuple] = []
        self._load()
        self.threads = [threading.Thread(target=self._worker, daemon=True, name=f"worker-{n}") for n in range(max(1, workers))]
        for t in self.threads:
            t.start()

    # ---------- persistência ----------
    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _save(self, job: Job) -> None:
        data = {"id": job.id, "created": job.created, "options": job.options, "status": job.status, "name": job.name,
                "max_videos": job.max_videos, "items": [asdict(i) for i in job.items]}
        tmp = self._path(job.id).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(job.id))

    def _load(self) -> None:
        for p in sorted(self.jobs_dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                job = Job(id=d["id"], created=d["created"], options=d["options"], status=d["status"],
                          name=d.get("name", ""), max_videos=d.get("max_videos", 20),
                          items=[Item(**i) for i in d["items"]])
            except (OSError, ValueError, KeyError, TypeError) as e:
                log(f"job ilegível {p.name}: {e}")
                continue
            self.jobs[job.id] = job
            if job.status in ("queued", "running"):   # servidor reiniciou no meio: retoma de onde parou
                job.status = "queued"
                for it in job.items:
                    if it.status in ACTIVE or it.status == "queued":
                        it.status, it.progress, it.message = "queued", 0.0, "Retomando após reinício"
                        self.queue.append(("item", job.id, it.id))

    # ---------- API ----------
    def list_jobs(self) -> list[dict]:
        with self.lock:
            js = sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)
            out = []
            for j in js:
                d = j.public()
                d.pop("items")
                d["videos"] = len([i for i in j.items if i.status != "expanded"])
                out.append(d)
            return out

    def get(self, job_id: str) -> Job:
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            return self.jobs[job_id]

    def get_public(self, job_id: str) -> dict:
        with self.lock:
            return self.get(job_id).public()

    def _check_source(self, src: str, expand: bool) -> str:
        src = src.strip()
        if is_url(src):
            return src
        p = Path(src).expanduser()
        if not p.is_absolute():
            p = self.videos / p
        p = p.resolve()
        for base in (self.uploads.resolve(), self.videos.resolve()):
            if p.is_relative_to(base):
                if not p.is_file():
                    raise JobError(f"Arquivo não encontrado: {src}")
                return str(p)
        raise JobError(f"Por segurança, arquivos locais precisam estar em {self.videos} (ou ser enviados pelo botão de upload): {src}")

    def create(self, sources: list[str], options: dict, expand: bool = False, max_videos: int = 20, name: str = "") -> Job:
        srcs = [s.strip() for s in sources if s and s.strip() and not s.strip().startswith("#")]
        srcs = list(dict.fromkeys(srcs))
        if not srcs:
            raise JobError("Cole pelo menos um link de vídeo")
        if len(srcs) > 500:
            raise JobError("Máximo de 500 links por projeto")
        max_videos = int(_num(max_videos, 1, 500, 20, "máximo de vídeos"))
        build_options(options, self.out)  # valida tudo antes de enfileirar
        prov = options.get("llm_provider") or None
        if prov not in (None, "", "auto", "none"):
            from ..llm import LLM, LLMError
            try:
                LLM.from_env(prov, options.get("llm_model") or None)
            except LLMError as e:
                raise JobError(f"IA indisponível: {e}. Informe a chave em Configurações ou escolha 'sem IA'.")
        items = []
        for n, s in enumerate(srcs):
            s = self._check_source(s, expand)
            items.append(Item(id=n, source=s, expand=expand and is_url(s)))
        job = Job(id=uuid.uuid4().hex[:12], created=time.time(), options=options, items=items, max_videos=max_videos,
                  name=name.strip()[:80] or (f"{len(items)} vídeos" if len(items) > 1 else ""))
        with self.lock:
            self.jobs[job.id] = job
            for it in items:
                self.queue.append(("item", job.id, it.id))
            self._save(job)
            self.cv.notify_all()
        return job

    def cancel(self, job_id: str) -> None:
        with self.lock:
            job = self.get(job_id)
            job.cancel = True
            for it in job.items:
                if it.status == "queued":
                    it.status, it.message = "canceled", "Cancelado"
            self.queue = [q for q in self.queue if not (q[0] == "item" and q[1] == job_id)]
            self._finish_if_idle(job)
            self._save(job)

    def delete(self, job_id: str) -> None:
        import shutil
        with self.lock:
            job = self.get(job_id)
            if job.status in ("queued", "running") and any(i.status in ACTIVE for i in job.items):
                raise JobError("Cancele o projeto antes de excluir")
            self.queue = [q for q in self.queue if q[1] != job_id]
            del self.jobs[job_id]
            self._path(job_id).unlink(missing_ok=True)
        shutil.rmtree(self.out / job_id, ignore_errors=True)

    def retry_failed(self, job_id: str) -> None:
        with self.lock:
            job = self.get(job_id)
            job.cancel = False
            for it in job.items:
                if it.status in ("error", "canceled"):
                    it.status, it.error, it.message, it.progress = "queued", "", "", 0.0
                    self.queue.append(("item", job_id, it.id))
            job.status = "queued"
            self._save(job)
            self.cv.notify_all()

    # ---------- caminhos ----------
    def item_dir(self, job_id: str, item_id: int) -> Path:
        return self.out / job_id / f"{item_id:03d}"

    def get_item(self, job_id: str, item_id: int) -> Item:
        job = self.get(job_id)
        for it in job.items:
            if it.id == item_id:
                return it
        raise KeyError(item_id)

    def clips_view(self, job_id: str, item_id: int) -> list[dict]:
        wd = self.item_dir(job_id, item_id)
        try:
            _, _, clips = load_clips(wd)
        except (OSError, ValueError, KeyError):
            return []
        out = []
        profile = build_profile(self.get(job_id).options)
        for i, c in enumerate(clips, 1):
            mp4 = wd / "clips" / f"clip_{i:02d}.mp4"
            d = c.to_dict()
            d["post"] = f"{c.title} {' '.join(_tags(c, profile))}".strip()
            d.update({"index": i, "ready": mp4.exists(), "v": int(mp4.stat().st_mtime) if mp4.exists() else 0,
                      "busy": (job_id, item_id, i) in self.busy, "duration": round(c.duration, 1)})
            out.append(d)
        return out

    # ---------- edição de um corte ----------
    def edit_clip(self, job_id: str, item_id: int, idx: int, patch: dict) -> None:
        job = self.get(job_id)
        it = self.get_item(job_id, item_id)
        if it.status != "done":
            raise JobError("Espere o vídeo terminar de processar para editar")
        wd = self.item_dir(job_id, item_id)
        media, prof, clips = load_clips(wd)
        if not 1 <= idx <= len(clips):
            raise KeyError(idx)
        c = clips[idx - 1]
        transcript = load_transcript(wd / "transcript.json") if (wd / "transcript.json").exists() else None
        dur = float(transcript["duration"]) if transcript else None
        if "title" in patch:
            c.title = str(patch["title"])[:120]
        if "hook" in patch:
            c.hook = str(patch["hook"])[:90]
        s = _num(patch.get("start"), 0, 86400, c.start, "início")
        e = _num(patch.get("end"), 0, 86400, c.end, "fim")
        if e - s < 3 or e - s > 180:
            raise JobError("O corte precisa ter entre 3s e 180s")
        if dur and e > dur + 0.5:
            raise JobError(f"O fim passa da duração do vídeo ({dur:.0f}s)")
        c.start, c.end = s, e
        if transcript:
            c.text, _ = span_text(flat_words(transcript), s, e)
            c.risk = risk_flags(c.text, build_profile(job.options).risk_keywords)
        c.source = c.source if c.source.endswith("+editado") else c.source + "+editado"
        save_clips(wd, media, build_profile(job.options), clips)
        with self.lock:
            self.busy.add((job_id, item_id, idx))
            self.queue.append(("clip", job_id, item_id, idx))
            self.cv.notify_all()

    # ---------- execução ----------
    def _worker(self) -> None:
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                task = self.queue.pop(0)
            try:
                if task[0] == "item":
                    self._run_item(task[1], task[2])
                else:
                    self._run_clip(*task[1:])
            except Exception as e:  # nunca derruba o worker
                log(f"erro inesperado no worker: {e!r}")

    def _set(self, job: Job, it: Item, **kw) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(it, k, v)

    def _finish_if_idle(self, job: Job) -> None:
        live = [i for i in job.items if i.status in ("queued",) + ACTIVE]
        if live:
            job.status = "running" if any(i.status in ACTIVE for i in live) else "queued"
            return
        sts = {i.status for i in job.items if i.status != "expanded"}
        job.status = "canceled" if job.cancel and "done" not in sts else ("error" if sts == {"error"} else "done")

    def _callbacks(self, job: Job, it: Item) -> Callbacks:
        def stage(name: str, msg: str = "") -> None:
            lo, _ = STAGE_RANGE[name]
            self._set(job, it, status=name, message=msg, progress=lo)
            with self.lock:
                self._save(job)

        def progress(f: float) -> None:
            lo, hi = STAGE_RANGE.get(it.status, (0, 0))
            it.progress = round(lo + (hi - lo) * max(0.0, min(1.0, f)), 3)

        return Callbacks(stage=stage, progress=progress, cancelled=lambda: job.cancel)

    def _key(self, source: str) -> str:
        h = hashlib.sha1(source.encode()).hexdigest()[:8]
        if is_url(source):
            m = re.search(r"[?&]v=([\w-]{6,})", source) or re.search(r"/([\w-]{6,})/?(?:\?.*)?$", source)
            return f"{slugify(m.group(1) if m else source, 30)}-{h}"
        p = Path(source)
        return f"{slugify(p.stem, 30)}-{h}-{p.stat().st_size}"

    def _run_item(self, job_id: str, item_id: int) -> None:
        try:
            job, it = self.get(job_id), self.get_item(job_id, item_id)
        except KeyError:
            return
        if job.cancel or it.status != "queued":
            return
        t0 = time.time()
        with self.lock:
            job.status = "running"
        cb = self._callbacks(job, it)
        try:
            if it.expand:
                cb.stage("expanding", "Listando vídeos da playlist/canal")
                urls = expand_playlist(it.source, job.max_videos)
                if not (len(urls) == 1 and urls[0] == it.source):
                    self._expand(job, it, urls)
                    return
                self._set(job, it, expand=False)
            it.key = self._key(it.source)
            it.title = it.title or (Path(it.source).stem if not is_url(it.source) else it.source)
            lib = self.library / it.key
            opts = build_options(job.options, self.out)
            wd = self.item_dir(job_id, item_id)
            wd.mkdir(parents=True, exist_ok=True)
            media, transcript = prepare(it.source, opts, lib, cb)
            self._set(job, it, title=video_title(lib, it.title))
            clips = make_plan(media, transcript, opts, wd, cb)
            self._set(job, it, clips_total=len(clips))
            render_all(wd, media, transcript, clips, opts, cb=cb, reuse=True)
            self._set(job, it, status="done", progress=1.0, message="Pronto", seconds=round(time.time() - t0, 1))
        except Cancelled:
            self._set(job, it, status="canceled", message="Cancelado")
        except Exception as e:
            log(f"FALHOU {it.source}: {e}")
            self._set(job, it, status="error", error=str(e)[-600:], message="Falhou")
        finally:
            with self.lock:
                self._finish_if_idle(job)
                self._save(job)

    def _expand(self, job: Job, it: Item, urls: list[str]) -> None:
        with self.lock:
            have = {i.source for i in job.items}
            nxt = max(i.id for i in job.items) + 1
            for u in urls:
                if u in have:
                    continue
                new = Item(id=nxt, source=u)
                nxt += 1
                job.items.append(new)
                self.queue.append(("item", job.id, new.id))
            it.status, it.message, it.progress = "expanded", f"{len(urls)} vídeos encontrados", 1.0
            self._finish_if_idle(job)
            self._save(job)
            self.cv.notify_all()

    def _run_clip(self, job_id: str, item_id: int, idx: int) -> None:
        try:
            job, it = self.get(job_id), self.get_item(job_id, item_id)
            wd = self.item_dir(job_id, item_id)
            opts = build_options(job.options, self.out)
            media, _, clips = load_clips(wd)
            transcript = load_transcript(self.library / it.key / "transcript.json")
            render_all(wd, media, transcript, clips, opts, only={idx})
        except Exception as e:
            log(f"re-render falhou: {e}")
        finally:
            with self.lock:
                self.busy.discard((job_id, item_id, idx))
