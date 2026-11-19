"""Testes da interface web: API, segurança, fila, retomada e render real com FFmpeg (Whisper simulado)."""
import base64
import csv
import http.client
import io
import json
import shutil
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import megacortes.pipeline as pl
from megacortes.render import probe
from megacortes.web import jobs as jobs_mod
from megacortes.web.jobs import JobError, Manager, build_profile, parse_keywords
from megacortes.web.server import App, make_handler

from conftest import make_transcript

H = {"X-Requested-With": "megacortes"}
OPTS = {"profile": "gta6", "clips": 2, "min_seconds": 12, "max_seconds": 24, "render_preset": "ultrafast",
        "llm_provider": "none", "caption": {"highlight": "#FF4FA3", "hook_box": "#00C2D1"}}


@pytest.fixture(autouse=True)
def fake_whisper(monkeypatch):
    monkeypatch.setattr(pl, "transcribe", lambda *a, **k: make_transcript())
    for k in ("LLM_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)


class Srv:
    def __init__(self, data: Path, password=None):
        self.app = App(data, workers=1, password=password)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def call(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hd = dict(headers if headers is not None else H)
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            hd["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=hd)
        r = c.getresponse()
        payload = r.read()
        c.close()
        return r.status, dict(r.getheaders()), payload

    def json(self, method, path, body=None, **kw):
        st, _, p = self.call(method, path, body, **kw)
        return st, (json.loads(p) if p and p[:1] in b"{[" else p)

    def wait_job(self, jid, timeout=180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            _, j = self.json("GET", f"/api/jobs/{jid}")
            if j["status"] in ("done", "error", "canceled"):
                return j
            time.sleep(0.5)
        raise AssertionError("job não terminou")


@pytest.fixture(scope="module")
def srv(tmp_path_factory, video):
    s = Srv(tmp_path_factory.mktemp("data"))
    shutil.copy(video, s.app.mgr.videos / "teste.mp4")
    return s


# ---------------------------------------------------------------- unidades
def test_parse_keywords():
    assert parse_keywords('gta 6: 2\n"vice city": 1.5\nsolta\n# comentário') == {"gta 6": 2.0, "vice city": 1.5, "solta": 1.0}


def test_build_profile_overrides_and_validation():
    p = build_profile({"profile": "gta6", "clips": 30, "min_seconds": 15, "max_seconds": 40,
                       "caption": {"highlight": "#00ff00", "size": 90, "uppercase": False}, "hashtags": "foo, #bar"})
    assert (p.clips, p.min_seconds, p.max_seconds) == (30, 15, 40)
    assert p.caption.highlight == "#00FF00" and p.caption.size == 90 and p.caption.uppercase is False
    assert p.hashtags == ["#foo", "#bar"]
    with pytest.raises(JobError, match="Cor inválida"):
        build_profile({"profile": "gta6", "caption": {"highlight": "red; drop"}})
    with pytest.raises(JobError):
        build_profile({"profile": "../../etc/passwd"})
    with pytest.raises(ValueError):
        build_profile({"profile": "gta6", "min_seconds": 50, "max_seconds": 20})


# ---------------------------------------------------------------- API e segurança
def test_config_and_static(srv):
    st, cfg = srv.json("GET", "/api/config")
    assert st == 200 and {p["id"] for p in cfg["profiles"]} >= {"gta6", "default"} and cfg["presets"]
    st, hd, body = srv.call("GET", "/")
    assert st == 200 and b"MegaCortes" in body and hd["Content-Type"].startswith("text/html")
    assert srv.call("GET", "/static/app.js")[0] == 200
    assert srv.call("GET", "/static/%2e%2e/server.py")[0] == 404


def test_csrf_and_host_guard(srv):
    assert srv.call("POST", "/api/jobs", {"sources": ["teste.mp4"]}, headers={})[0] == 403
    st, _, _ = srv.call("GET", "/api/config", headers={"Host": "evil.example.com"})
    assert st == 403
    st, _, _ = srv.call("POST", "/api/jobs", raw=b'{"sources":["x"]}', headers={**H, "Content-Type": "text/plain"})
    assert st == 400


def test_sources_validation(srv):
    st, j = srv.json("POST", "/api/jobs", {"sources": ["/etc/passwd"], "options": OPTS})
    assert st == 400 and "segurança" in j["error"]
    st, j = srv.json("POST", "/api/jobs", {"sources": ["../../etc/passwd"], "options": OPTS})
    assert st == 400
    st, j = srv.json("POST", "/api/jobs", {"sources": [], "options": OPTS})
    assert st == 400
    st, j = srv.json("POST", "/api/jobs", {"sources": ["teste.mp4"], "options": {**OPTS, "caption": {"highlight": "zzz"}}})
    assert st == 400 and "Cor" in j["error"]
    st, j = srv.json("POST", "/api/jobs", {"sources": ["teste.mp4"], "options": {**OPTS, "llm_provider": "gemini"}})
    assert st == 400 and "IA indisponível" in j["error"]


def test_upload_and_traversal(srv):
    st, j = srv.json("POST", "/api/upload?name=..%2F..%2Fevil.mp4", raw=b"x" * 10)
    assert st == 201 and Path(j["path"]).parent == srv.app.mgr.uploads and Path(j["path"]).name.endswith("evil.mp4")
    assert srv.json("POST", "/api/upload?name=a.exe", raw=b"x")[0] == 400
    assert srv.call("GET", "/files/abcdef123456/0/%2e%2e/%2e%2e/%2e%2e/etc/passwd")[0] == 404
    assert srv.call("GET", "/files/abcdef123456/0/clips/clip_01.mp4")[0] == 404


def test_password_protection(tmp_path):
    s = Srv(tmp_path, password="segredo")
    assert s.call("GET", "/api/config")[0] == 401
    good = {"Authorization": "Basic " + base64.b64encode(b"u:segredo").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"u:errada").decode()}
    assert s.call("GET", "/api/config", headers=good)[0] == 200
    assert s.call("GET", "/api/config", headers=bad)[0] == 401


def test_settings_roundtrip_hides_key(tmp_path, monkeypatch):
    s = Srv(tmp_path)
    st, j = s.json("POST", "/api/settings", {"provider": "gemini", "keys": {"gemini": "AIza-super-secreta-1234"}})
    assert st == 200 and j["keys"]["gemini"]["set"] and "secreta" not in json.dumps(j)
    assert (tmp_path / "settings.json").stat().st_mode & 0o077 == 0
    s.json("POST", "/api/settings", {"keys": {"gemini": ""}, "provider": ""})
    assert not s.json("GET", "/api/settings")[1]["keys"]["gemini"]["set"]


# ---------------------------------------------------------------- fluxo completo
def test_full_flow_range_zip_edit_delete(srv):
    st, job = srv.json("POST", "/api/jobs", {"sources": ["teste.mp4"], "options": OPTS})
    assert st == 201
    j = srv.wait_job(job["id"])
    assert j["status"] == "done", j
    it = j["items"][0]
    assert it["status"] == "done" and it["clips_total"] == 2

    st, clips = srv.json("GET", f"/api/jobs/{job['id']}/items/0/clips")
    assert st == 200 and len(clips) == 2 and all(c["ready"] for c in clips)
    assert "#gta6" in clips[0]["post"]

    base = f"/files/{job['id']}/0/clips/clip_01.mp4"
    st, hd, body = srv.call("GET", base)
    assert st == 200 and hd["Accept-Ranges"] == "bytes" and len(body) == int(hd["Content-Length"])
    st, hd, part = srv.call("GET", base, headers={**H, "Range": "bytes=0-99"})
    assert st == 206 and len(part) == 100 and hd["Content-Range"].startswith("bytes 0-99/")
    assert srv.call("GET", base, headers={**H, "Range": "bytes=99999999999-"})[0] == 416

    st, _, z = srv.call("GET", f"/api/jobs/{job['id']}/download.zip")
    assert st == 200
    zf = zipfile.ZipFile(io.BytesIO(z))
    names = zf.namelist()
    assert sum(n.endswith(".mp4") for n in names) == 2 and "posts.csv" in names and zf.testzip() is None
    rows = list(csv.DictReader(io.StringIO(zf.read("posts.csv").decode("utf-8-sig"))))
    assert len(rows) == 2 and rows[0]["video"] and rows[0]["arquivo"].endswith(".mp4") and "/" in rows[0]["arquivo"]

    mp4 = srv.app.mgr.item_dir(job["id"], 0) / "clips" / "clip_01.mp4"
    assert probe(mp4)["width"] == 1080
    before = probe(mp4)["duration"]
    c0 = clips[0]
    st, r = srv.json("PATCH", f"/api/jobs/{job['id']}/items/0/clips/1",
                     {"title": "Novo <título>", "start": c0["start"], "end": c0["start"] + 14})
    assert st == 200
    for _ in range(120):
        _, cl = srv.json("GET", f"/api/jobs/{job['id']}/items/0/clips")
        if not cl[0]["busy"]:
            break
        time.sleep(0.5)
    assert cl[0]["title"] == "Novo <título>" and "+editado" in cl[0]["source"]
    assert abs(probe(mp4)["duration"] - 14) < 0.6 and probe(mp4)["duration"] < before
    st, r = srv.json("PATCH", f"/api/jobs/{job['id']}/items/0/clips/1", {"start": 5, "end": 6})
    assert st == 400
    st, r = srv.json("PATCH", f"/api/jobs/{job['id']}/items/0/clips/1", {"start": 0, "end": 9999})
    assert st == 400

    assert srv.json("DELETE", f"/api/jobs/{job['id']}")[0] == 200
    assert srv.json("GET", f"/api/jobs/{job['id']}")[0] == 404
    assert not (srv.app.mgr.out / job["id"]).exists()


def test_min_score_filter_and_style_applied(srv):
    opts = {**OPTS, "clips": 6, "min_score": 70, "caption": {"highlight": "#ABCDEF", "uppercase": False}}
    st, job = srv.json("POST", "/api/jobs", {"sources": ["teste.mp4"], "options": opts})
    j = srv.wait_job(job["id"])
    assert j["status"] == "done"
    _, clips = srv.json("GET", f"/api/jobs/{job['id']}/items/0/clips")
    assert clips and all(c["score"] >= 70 for c in clips)
    ass = (srv.app.mgr.item_dir(job["id"], 0) / "clips" / "clip_01.ass").read_text()
    assert "&HEFCDAB&" in ass.upper() or "&HEFCDAB&" in ass       # cor ABCDEF em BGR no ASS
    srv.json("DELETE", f"/api/jobs/{job['id']}")


def test_expand_playlist_creates_items(srv, monkeypatch, video):
    monkeypatch.setattr(jobs_mod, "expand_playlist", lambda url, n: ["https://ex.com/v/aaaaaa1", "https://ex.com/v/bbbbbb2"][:n])
    def fake_prepare(source, opts, workdir, cb=None):
        return video, make_transcript()
    monkeypatch.setattr(jobs_mod, "prepare", fake_prepare)
    monkeypatch.setattr(jobs_mod, "video_title", lambda wd, fb="": "Vídeo " + fb[-7:])
    st, job = srv.json("POST", "/api/jobs", {"sources": ["https://ex.com/playlist"], "expand": True, "max_videos": 5,
                                             "options": {**OPTS, "clips": 1}})
    assert st == 201
    j = srv.wait_job(job["id"])
    assert j["status"] == "done" and len(j["items"]) == 2          # item "expandido" fica oculto
    assert {i["status"] for i in j["items"]} == {"done"} and all(i["clips_total"] == 1 for i in j["items"])
    st, _, z = srv.call("GET", f"/api/jobs/{job['id']}/download.zip")
    assert sum(n.endswith(".mp4") for n in zipfile.ZipFile(io.BytesIO(z)).namelist()) == 2
    srv.json("DELETE", f"/api/jobs/{job['id']}")


def test_failed_item_reports_error_and_retry(srv, monkeypatch):
    calls = {"n": 0}
    def flaky(source, opts, workdir, cb=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("yt-dlp falhou: vídeo privado")
        return srv.app.mgr.videos / "teste.mp4", make_transcript()
    monkeypatch.setattr(jobs_mod, "prepare", flaky)
    st, job = srv.json("POST", "/api/jobs", {"sources": ["https://ex.com/privado"], "options": {**OPTS, "clips": 1}})
    j = srv.wait_job(job["id"])
    assert j["status"] == "error" and "vídeo privado" in j["items"][0]["error"]
    assert srv.json("POST", f"/api/jobs/{job['id']}/retry", {})[0] == 200
    j = srv.wait_job(job["id"])
    assert j["status"] == "done"
    srv.json("DELETE", f"/api/jobs/{job['id']}")


def test_cancel_queued_job(tmp_path, video, monkeypatch):
    gate = threading.Event()
    def slow(source, opts, workdir, cb=None):
        while not gate.is_set():
            cb.check()
            time.sleep(0.05)
        return video, make_transcript()
    monkeypatch.setattr(jobs_mod, "prepare", slow)
    s = Srv(tmp_path)
    st, job = s.json("POST", "/api/jobs", {"sources": ["https://ex.com/a", "https://ex.com/b"], "options": OPTS})
    time.sleep(0.5)
    assert s.json("POST", f"/api/jobs/{job['id']}/cancel", {})[0] == 200
    j = s.wait_job(job["id"], 20)
    assert j["status"] == "canceled" and {i["status"] for i in j["items"]} == {"canceled"}
    gate.set()


def test_resume_after_restart(tmp_path, video):
    s = Srv(tmp_path)
    shutil.copy(video, s.app.mgr.videos / "teste.mp4")
    _, job = s.json("POST", "/api/jobs", {"sources": ["teste.mp4"], "options": {**OPTS, "clips": 1}})
    assert s.wait_job(job["id"])["status"] == "done"
    # simula queda no meio do render: o arquivo de estado diz "rodando"
    p = tmp_path / "jobs" / f"{job['id']}.json"
    d = json.loads(p.read_text())
    d["status"] = "running"
    d["items"][0]["status"] = "rendering"
    p.write_text(json.dumps(d))
    s2 = Srv(tmp_path)                      # "reinício"
    j = s2.wait_job(job["id"], 60)
    assert j["status"] == "done" and j["items"][0]["clips_total"] == 1
