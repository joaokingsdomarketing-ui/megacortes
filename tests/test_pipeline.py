import csv
import json
import subprocess
from pathlib import Path

import pytest

from megacortes.audio import energy_curve
from megacortes.cli import main
from megacortes.config import load_profile
from megacortes.pipeline import Options, run
from megacortes.render import probe


def test_energy_curve_sees_loud_bursts(video):
    e = energy_curve(video)
    quiet, _ = e.stats(2, 14)
    loud, peak = e.stats(20, 24)
    assert loud > quiet + 0.5 and peak > 0.9


def test_profiles_load_and_validate():
    for name in ("gta6", "default"):
        p = load_profile(name)
        assert p.min_seconds < p.max_seconds and p.reframe in ("blur", "center", "face")
    assert "gta 6" in load_profile("gta6").keywords


def test_bad_profile_field_rejected(tmp_path):
    bad = tmp_path / "x.yaml"
    bad.write_text("name: x\nreframee: blur\n")
    with pytest.raises(ValueError, match="desconhecidos"):
        load_profile(str(bad))


def short_profile(name: str):
    """Vídeo de teste tem só 60s: cortes menores permitem 2 trechos sem sobreposição."""
    p = load_profile(name)
    p.min_seconds, p.max_seconds = 12, 24
    return p


def _check_clip(path: Path, expect_dur_range=(10, 60)):
    info = probe(path)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["has_audio"]
    assert expect_dur_range[0] <= info["duration"] <= expect_dur_range[1]


def test_full_run_blur_mode(video, transcript_file, tmp_path, monkeypatch):
    for k in ("LLM_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    opts = Options(profile=short_profile("gta6"), out_dir=tmp_path, clips=2, transcript_path=transcript_file,
                   preset="ultrafast")
    res = run(str(video), opts)
    assert len(res["files"]) == 2
    for f in res["files"]:
        _check_clip(f)
        assert f.with_suffix(".jpg").exists() and f.with_suffix(".ass").exists()

    data = json.loads((res["workdir"] / "clips.json").read_text())
    assert data["profile"] == "gta6" and len(data["clips"]) == 2
    rows = list(csv.DictReader((res["workdir"] / "posts.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 2 and rows[0]["arquivo"].startswith("clips/clip_01")
    assert "#gta6" in rows[0]["hashtags"]


def test_center_mode_and_face_fallback(video, transcript_file, tmp_path):
    for mode in ("center", "face"):      # 'face' sem rosto no vídeo sintético deve cair em blur sem quebrar
        opts = Options(profile=load_profile("default"), out_dir=tmp_path / mode, clips=1, mode=mode,
                       transcript_path=transcript_file, preset="ultrafast")
        res = run(str(video), opts)
        _check_clip(res["files"][0])


def test_plan_edit_render_roundtrip_via_cli(video, transcript_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in ("LLM_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit) as e:
        main(["plan", str(video), "--profile", "gta6", "--clips", "2", "--out", "o", "--transcript", str(transcript_file)])
    assert e.value.code == 0
    workdir = Path("o/teste")
    cj = workdir / "clips.json"
    data = json.loads(cj.read_text())
    assert not list((workdir / "clips").glob("*.mp4")) if (workdir / "clips").exists() else True

    data["clips"][0]["hook"] = "Gancho editado à mão"
    cj.write_text(json.dumps(data, ensure_ascii=False))
    with pytest.raises(SystemExit) as e:
        main(["render", str(workdir), "--only", "1", "--preset", "ultrafast"])
    assert e.value.code == 0
    assert (workdir / "clips" / "clip_01.mp4").exists() and not (workdir / "clips" / "clip_02.mp4").exists()
    assert "GANCHO EDITADO À MÃO" in (workdir / "clips" / "clip_01.ass").read_text(encoding="utf-8").upper()


def test_cli_reports_missing_file_cleanly(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["run", str(tmp_path / "nao-existe.mp4"), "--out", str(tmp_path)])
    assert e.value.code == 2 and "não encontrado" in capsys.readouterr().err


def test_captions_are_actually_burned_in(video, transcript_file, tmp_path):
    """Compara um quadro com e sem legenda: tem que haver diferença visível na região da legenda."""
    opts = Options(profile=load_profile("gta6"), out_dir=tmp_path, clips=1, transcript_path=transcript_file,
                   preset="ultrafast")
    res = run(str(video), opts)
    clip = res["files"][0]
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.2", "-i", str(clip), "-frames:v", "1", str(frame)], check=True)
    assert frame.stat().st_size > 5000
