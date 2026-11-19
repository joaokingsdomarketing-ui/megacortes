"""Interface de linha de comando: megacortes run | plan | render | batch | profiles."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import list_profiles, load_profile
from .llm import LLMError
from .pipeline import Options, load_clips, render_all, run, workdir_for
from .transcribe import load_transcript
from .util import fmt_ts, load_dotenv, log


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profile", default="gta6", help="nome (gta6, default) ou caminho de um .yaml")
    p.add_argument("--out", default="out", help="pasta de saída (padrão: out)")
    p.add_argument("--clips", type=int, help="quantos cortes gerar (padrão: do perfil)")
    p.add_argument("--mode", choices=["blur", "center", "face"], help="reenquadramento (padrão: do perfil)")
    p.add_argument("--whisper-model", default="small", help="tiny|base|small|medium|large-v3")
    p.add_argument("--language", help="idioma da fala (pt, en...) — padrão: detectar")
    p.add_argument("--device", default="auto", help="auto|cpu|cuda")
    p.add_argument("--llm", dest="llm_provider",
                   help="gemini|openai|anthropic|openrouter|ollama|none (padrão: detecta pela chave no .env)")
    p.add_argument("--llm-model", help="sobrescreve o modelo padrão do provedor")
    p.add_argument("--transcript", type=Path, help="usar transcrição .json pronta (pula o Whisper)")
    p.add_argument("--no-hook", action="store_true", help="sem faixa de gancho no início")
    p.add_argument("--no-loudnorm", action="store_true", help="não normalizar o volume")
    p.add_argument("--crf", type=int, default=20, help="qualidade x264 (menor = melhor; padrão 20)")
    p.add_argument("--preset", default="veryfast", help="preset x264 (veryfast|medium|slow)")


def _options(a: argparse.Namespace) -> Options:
    return Options(
        profile=load_profile(a.profile), out_dir=Path(a.out), clips=a.clips, mode=a.mode,
        whisper_model=a.whisper_model, language=a.language, device=a.device,
        llm_provider=a.llm_provider, llm_model=a.llm_model, transcript_path=a.transcript,
        hook=not a.no_hook, loudnorm=not a.no_loudnorm, crf=a.crf, preset=a.preset,
    )


def _print_summary(result: dict) -> None:
    print(f"\nPasta: {result['workdir']}")
    for i, c in enumerate(result["clips"], 1):
        risk = f"  ⚠ revisar: {', '.join(c.risk)}" if c.risk else ""
        print(f"  {i:02d}  {fmt_ts(c.start):>8}–{fmt_ts(c.end):<8} score {c.score:5.1f}  {c.title[:60]}{risk}")
    if result["files"]:
        print(f"\n{len(result['files'])} vídeos em {result['workdir'] / 'clips'}  |  planilha: {result['workdir'] / 'posts.csv'}")
    else:
        print(f"\nPlano salvo em {result['workdir'] / 'clips.json'} (edite e rode: megacortes render {result['workdir']})")


def cmd_run(a: argparse.Namespace) -> int:
    _print_summary(run(a.source, _options(a), render=True))
    return 0


def cmd_plan(a: argparse.Namespace) -> int:
    _print_summary(run(a.source, _options(a), render=False))
    return 0


def cmd_batch(a: argparse.Namespace) -> int:
    sources = [ln.strip() for ln in Path(a.list).read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.startswith("#")]
    failures = 0
    for n, src in enumerate(sources, 1):
        log(f"=== {n}/{len(sources)}: {src}")
        try:
            _print_summary(run(src, _options(a), render=True))
        except Exception as e:  # um vídeo ruim não para a fila
            failures += 1
            log(f"FALHOU: {src}: {e}")
    return 1 if failures else 0


def cmd_render(a: argparse.Namespace) -> int:
    workdir = Path(a.workdir)
    media, prof_name, clips = load_clips(workdir)
    if a.profile == "gta6" and prof_name != "gta6":  # --profile não informado explicitamente
        a.profile = prof_name
    opts = _options(a)
    transcript = load_transcript(workdir / "transcript.json")
    only = {int(x) for x in a.only.split(",")} if a.only else None
    files = render_all(workdir, media, transcript, clips, opts, only)
    print(f"{len(files)} vídeos renderizados em {workdir / 'clips'}")
    return 0


def cmd_profiles(_: argparse.Namespace) -> int:
    print("Perfis disponíveis:", ", ".join(list_profiles()))
    return 0


def cmd_web(a: argparse.Namespace) -> int:
    from .web.server import serve
    serve(a.host, a.port, a.data, a.workers)
    return 0


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="megacortes", description="Cortes verticais virais a partir de vídeos longos.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="transcreve, escolhe os melhores trechos e renderiza")
    p.add_argument("source", help="arquivo local ou URL (yt-dlp)")
    _common(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("plan", help="só transcreve e escolhe (gera clips.json editável)")
    p.add_argument("source")
    _common(p)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("render", help="renderiza a partir de um clips.json (editado ou não)")
    p.add_argument("workdir", help="pasta gerada por run/plan")
    p.add_argument("--only", help="índices separados por vírgula, ex.: 1,3")
    _common(p)
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("batch", help="processa uma lista (um arquivo/URL por linha)")
    p.add_argument("list", help="arquivo .txt")
    _common(p)
    p.set_defaults(fn=cmd_batch)

    p = sub.add_parser("profiles", help="lista os perfis de nicho")
    p.set_defaults(fn=cmd_profiles)

    p = sub.add_parser("web", help="abre a interface web (cole links e gere cortes pelo navegador)")
    p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 só com MEGACORTES_PASSWORD definida")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--data", default="data", help="pasta de dados (jobs, vídeos, cortes)")
    p.add_argument("--workers", type=int, default=1, help="vídeos processados em paralelo (padrão 1)")
    p.set_defaults(fn=cmd_web)

    args = ap.parse_args(argv)
    try:
        sys.exit(args.fn(args))
    except (LLMError, FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Erro: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
