"""Servidor HTTP do MegaCortes (só biblioteca padrão): API JSON + interface estática + vídeos com Range."""
from __future__ import annotations

import base64
import csv
import hmac
import io
import json
import mimetypes
import os
import re
import shutil
import sys
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__
from ..config import list_profiles, load_profile
from ..ingest import VIDEO_EXTS
from ..llm import PRESETS as LLM_PRESETS
from ..util import log, slugify
from .jobs import ID_RE, WHISPER_MODELS, JobError, Manager
from .presets import PRESETS

STATIC = Path(__file__).resolve().parent / "static"
MAX_JSON = 1_000_000


class Settings:
    """Chaves de IA digitadas na interface. Ficam em <data>/settings.json (permissão 600) e viram variáveis de ambiente."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "settings.json"
        self.d: dict = {"provider": "", "model": "", "keys": {}}
        try:
            self.d.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self.apply()

    def apply(self) -> None:
        for prov, pr in LLM_PRESETS.items():
            if pr["key_env"] and self.d["keys"].get(prov):
                os.environ[pr["key_env"]] = self.d["keys"][prov]
        for var, val in (("LLM_PROVIDER", self.d.get("provider")), ("LLM_MODEL", self.d.get("model"))):
            if val:
                os.environ[var] = val

    def update(self, patch: dict) -> None:
        if "provider" in patch:
            self.d["provider"] = str(patch["provider"] or "")
            if not self.d["provider"]:
                os.environ.pop("LLM_PROVIDER", None)
        if "model" in patch:
            self.d["model"] = str(patch["model"] or "").strip()
            if not self.d["model"]:
                os.environ.pop("LLM_MODEL", None)
        for prov, key in (patch.get("keys") or {}).items():
            if prov not in LLM_PRESETS or not LLM_PRESETS[prov]["key_env"]:
                continue
            key = str(key or "").strip()
            if key:
                self.d["keys"][prov] = key
            else:
                self.d["keys"].pop(prov, None)
                os.environ.pop(LLM_PRESETS[prov]["key_env"], None)
        self.apply()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def public(self) -> dict:
        keys = {}
        for prov, pr in LLM_PRESETS.items():
            if not pr["key_env"]:
                continue
            val = os.environ.get(pr["key_env"], "")
            keys[prov] = {"set": bool(val), "hint": ("••••" + val[-4:]) if len(val) > 8 else ("••••" if val else "")}
        return {"provider": self.d.get("provider", ""), "model": self.d.get("model", ""), "keys": keys,
                "default_models": {k: v["model"] for k, v in LLM_PRESETS.items()}}


class App:
    def __init__(self, data_dir: Path, workers: int = 1, password: str | None = None, allowed_hosts: set | None = None):
        self.mgr = Manager(data_dir, workers)
        self.settings = Settings(self.mgr.data)
        self.password = password
        self.allowed_hosts = {"localhost", "127.0.0.1", "[::1]", "::1"} | (allowed_hosts or set())
        self.max_upload = int(float(os.getenv("MEGACORTES_MAX_UPLOAD_MB", "8192")) * 1024 * 1024)

    def config(self) -> dict:
        profiles = []
        for pid in list_profiles():
            p = load_profile(pid)
            from dataclasses import asdict
            profiles.append({"id": pid, "name": p.name, "clips": p.clips, "min_seconds": p.min_seconds,
                             "max_seconds": p.max_seconds, "reframe": p.reframe, "niche_prompt": p.niche_prompt,
                             "hashtags": p.hashtags, "caption": asdict(p.caption),
                             "keywords": "\n".join(f"{k}: {v:g}" for k, v in p.keywords.items())})
        return {
            "version": __version__, "profiles": profiles, "presets": PRESETS, "whisper_models": list(WHISPER_MODELS),
            "llm_providers": [{"id": k, "needs_key": bool(v["key_env"])} for k, v in LLM_PRESETS.items()],
            "capabilities": {
                "ffmpeg": bool(shutil.which("ffmpeg")), "whisper": find_spec("faster_whisper") is not None,
                "yt_dlp": find_spec("yt_dlp") is not None, "opencv": find_spec("cv2") is not None,
            },
            "videos_dir": str(self.mgr.videos),
        }


def make_handler(app: App):
    mgr = app.mgr

    class H(BaseHTTPRequestHandler):
        server_version = "MegaCortes"
        protocol_version = "HTTP/1.0"   # fecha a conexão ao fim: simples e ok p/ streaming de zip

        def log_message(self, fmt, *args):  # silencioso; erros vão por log()
            pass

        # ---------- util ----------
        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _err(self, code, msg):
            self._json({"error": msg}, code)

        def _body_json(self) -> dict:
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                raise JobError("Content-Type deve ser application/json")
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_JSON:
                raise JobError("Corpo grande demais")
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise JobError("JSON inválido")
            if not isinstance(d, dict):
                raise JobError("JSON deve ser um objeto")
            return d

        def _guard(self) -> bool:
            """Senha (se configurada) e proteção contra DNS rebinding (se não houver senha)."""
            if app.password:
                hdr = self.headers.get("Authorization", "")
                ok = False
                if hdr.startswith("Basic "):
                    try:
                        _, _, pw = base64.b64decode(hdr[6:]).decode().partition(":")
                        ok = hmac.compare_digest(pw.encode(), app.password.encode())
                    except Exception:
                        ok = False
                if not ok:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="MegaCortes"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
            else:
                raw = self.headers.get("Host") or ""
                host = raw[: raw.index("]") + 1] if raw.startswith("[") else raw.split(":")[0]
                if "*" not in app.allowed_hosts and host not in app.allowed_hosts:
                    self._err(403, f"Host '{host}' não permitido. Acesse por localhost, ou defina MEGACORTES_PASSWORD "
                                   "(e MEGACORTES_ALLOWED_HOSTS) para abrir na rede.")
                    return False
            if self.command in ("POST", "PATCH", "DELETE") and self.headers.get("X-Requested-With") != "megacortes":
                self._err(403, "Requisição não autorizada")
                return False
            return True

        # ---------- roteamento ----------
        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _dispatch(self, method):
            try:
                if not self._guard():
                    return
                u = urlparse(self.path)
                parts = [unquote(x) for x in u.path.strip("/").split("/") if x]
                q = parse_qs(u.query)
                self._route(method, parts, q)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except JobError as e:
                self._err(400, str(e))
            except KeyError:
                self._err(404, "Não encontrado")
            except Exception as e:
                log(f"erro {method} {self.path}: {e!r}")
                self._err(500, f"Erro interno: {e}")

        def _route(self, m, p, q):
            if m == "GET" and (not p or p[0] in ("static",)):
                return self._static(p)
            if not p:
                raise KeyError
            if p[0] == "api":
                return self._api(m, p[1:], q)
            if p[0] == "files" and m == "GET" and len(p) >= 4:
                return self._file(p[1], p[2], "/".join(p[3:]))
            raise KeyError

        def _api(self, m, p, q):
            if p == ["config"] and m == "GET":
                return self._json(app.config())
            if p == ["settings"]:
                if m == "GET":
                    return self._json(app.settings.public())
                if m == "POST":
                    app.settings.update(self._body_json())
                    return self._json(app.settings.public())
            if p == ["upload"] and m == "POST":
                return self._upload(q)
            if p == ["jobs"]:
                if m == "GET":
                    return self._json(mgr.list_jobs())
                if m == "POST":
                    b = self._body_json()
                    srcs = b.get("sources") or []
                    if isinstance(srcs, str):
                        srcs = srcs.splitlines()
                    job = mgr.create(list(map(str, srcs)), b.get("options") or {}, bool(b.get("expand")),
                                     b.get("max_videos", 20), str(b.get("name") or ""))
                    return self._json(job.public(), 201)
            if len(p) >= 2 and p[0] == "jobs":
                jid = p[1]
                if not ID_RE.match(jid):
                    raise KeyError
                if len(p) == 2:
                    if m == "GET":
                        return self._json(mgr.get_public(jid))
                    if m == "DELETE":
                        mgr.delete(jid)
                        return self._json({"ok": True})
                if len(p) == 3 and m == "POST" and p[2] == "cancel":
                    mgr.cancel(jid)
                    return self._json({"ok": True})
                if len(p) == 3 and m == "POST" and p[2] == "retry":
                    mgr.retry_failed(jid)
                    return self._json({"ok": True})
                if len(p) == 3 and m == "GET" and p[2] == "download.zip":
                    return self._zip(jid, None)
                if len(p) >= 4 and p[2] == "items":
                    iid = int(p[3])
                    mgr.get_item(jid, iid)
                    if len(p) == 5 and p[4] == "clips" and m == "GET":
                        return self._json(mgr.clips_view(jid, iid))
                    if len(p) == 5 and p[4] == "download.zip" and m == "GET":
                        return self._zip(jid, iid)
                    if len(p) == 6 and p[4] == "clips" and m == "PATCH":
                        mgr.edit_clip(jid, iid, int(p[5]), self._body_json())
                        return self._json({"ok": True})
            raise KeyError

        # ---------- estáticos e arquivos ----------
        def _static(self, p):
            rel = "index.html" if not p else "/".join(p[1:])
            f = (STATIC / rel).resolve()
            if not f.is_relative_to(STATIC.resolve()) or not f.is_file():
                raise KeyError
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", (mimetypes.guess_type(f.name)[0] or "application/octet-stream") + "; charset=utf-8"
                             if f.suffix in (".html", ".js", ".css") else (mimetypes.guess_type(f.name)[0] or "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def _file(self, jid, iid, rel):
            if not ID_RE.match(jid) or not iid.isdigit():
                raise KeyError
            base = mgr.item_dir(jid, int(iid)).resolve()
            f = (base / rel).resolve()
            if not f.is_relative_to(base) or not f.is_file() or f.suffix.lower() not in (".mp4", ".jpg", ".csv", ".ass", ".json"):
                raise KeyError
            size = f.stat().st_size
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            start, end, code = 0, size - 1, 200
            rng = self.headers.get("Range")
            if rng:
                mm = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
                if mm and (mm.group(1) or mm.group(2)):
                    if mm.group(1):
                        start = int(mm.group(1))
                        end = int(mm.group(2)) if mm.group(2) else size - 1
                    else:
                        start = max(0, size - int(mm.group(2)))
                    end = min(end, size - 1)
                    if start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    code = 206
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if q_dl := parse_qs(urlparse(self.path).query).get("dl"):
                name = re.sub(r'[^\w.\- ]', "_", q_dl[0])[:120] or f.name
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "private, max-age=60")
            self.end_headers()
            with f.open("rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def _zip(self, jid, only_item):
            job = mgr.get(jid)
            items = [i for i in job.items if i.status == "done" and (only_item is None or i.id == only_item)]
            if not items:
                raise JobError("Nenhum vídeo pronto para baixar ainda")
            fname = f"megacortes-{jid}" + (f"-{only_item}" if only_item is not None else "") + ".zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.end_headers()

            class Out:  # saída sem seek: o zipfile usa "data descriptors"
                def __init__(s, w):
                    s.w = w
                def write(s, b):
                    s.w.write(b)
                    return len(b)
                def flush(s):
                    s.w.flush()

            rows, header = [], None
            with zipfile.ZipFile(Out(self.wfile), "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                for n, it in enumerate(items, 1):
                    folder = f"{n:02d}-{slugify(it.title, 40)}"
                    wd = mgr.item_dir(jid, it.id)
                    for c in mgr.clips_view(jid, it.id):
                        if not c["ready"]:
                            continue
                        base = f"{c['index']:02d}-{slugify(c['title'], 50)}"
                        for ext in (".mp4", ".jpg"):
                            src = wd / "clips" / f"clip_{c['index']:02d}{ext}"
                            if src.exists():
                                z.write(src, f"{folder}/{base}{ext}")
                    csvp = wd / "posts.csv"
                    if csvp.exists():
                        rd = csv.DictReader(io.StringIO(csvp.read_text(encoding="utf-8-sig")))
                        header = header or ["video"] + list(rd.fieldnames or [])
                        for r in rd:
                            r["video"] = it.title
                            r["arquivo"] = f"{folder}/{Path(r['arquivo']).name}" if r.get("arquivo", "").startswith("clips/") else r.get("arquivo", "")
                            rows.append(r)
                if header:
                    buf = io.StringIO()
                    w = csv.DictWriter(buf, fieldnames=header, extrasaction="ignore")
                    w.writeheader()
                    w.writerows(rows)
                    z.writestr("posts.csv", ("﻿" + buf.getvalue()).encode("utf-8"))

        def _upload(self, q):
            name = Path((q.get("name") or ["video.mp4"])[0]).name
            ext = Path(name).suffix.lower()
            if ext not in VIDEO_EXTS:
                raise JobError(f"Formato não suportado ({ext or 'sem extensão'}). Use: {', '.join(sorted(VIDEO_EXTS))}")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                raise JobError("Arquivo vazio")
            if n > app.max_upload:
                raise JobError(f"Arquivo maior que o limite de {app.max_upload // 2**20} MB (MEGACORTES_MAX_UPLOAD_MB)")
            dest = mgr.uploads / f"{uuid.uuid4().hex[:8]}-{slugify(Path(name).stem, 50)}{ext}"
            left = n
            with dest.open("wb") as fh:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    fh.write(chunk)
                    left -= len(chunk)
            if left:
                dest.unlink(missing_ok=True)
                raise JobError("Upload interrompido")
            self._json({"path": str(dest), "name": name, "size": n}, 201)

    return H


def serve(host: str = "127.0.0.1", port: int = 8000, data_dir: str = "data", workers: int = 1) -> None:
    password = os.getenv("MEGACORTES_PASSWORD") or None
    extra = {h.strip() for h in os.getenv("MEGACORTES_ALLOWED_HOSTS", "").split(",") if h.strip()}
    app = App(Path(data_dir), workers, password, extra)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    caps = app.config()["capabilities"]
    print(f"MegaCortes {__version__} em http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}  (dados: {app.mgr.data})")
    for k, label in (("ffmpeg", "FFmpeg"), ("whisper", "faster-whisper"), ("yt_dlp", "yt-dlp")):
        if not caps[k]:
            print(f"  ⚠ {label} não encontrado — veja o README", file=sys.stderr)
    if host not in ("127.0.0.1", "localhost") and not password:
        print("  ⚠ Escutando fora do localhost sem MEGACORTES_PASSWORD. Defina uma senha antes de expor na rede.", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando...")
