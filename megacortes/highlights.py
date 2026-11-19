"""Escolha dos melhores cortes: LLM (opcional) + heurística (palavras-chave, energia de áudio, gancho)."""
from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field

import numpy as np

from .audio import EMPTY, EnergyCurve
from .config import Profile
from .llm import LLM, extract_json
from .transcribe import flat_words
from .util import fmt_ts, log

HOOK_STARTERS = (
    "olha", "olhem", "gente", "cara", "mano", "meu deus", "véi", "você sabia", "vocês viram",
    "imagina", "atenção", "calma", "espera", "espere", "ninguém", "pasmem", "olhe",
    "wait", "look", "guys", "did you", "you won't", "oh my", "holy", "listen", "imagine",
    "so apparently", "breaking", "nobody", "check this", "hold on",
)


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    n_words: int


@dataclass
class Clip:
    start: float
    end: float
    title: str = ""
    hook: str = ""
    score: float = 0.0
    reason: str = ""
    hashtags: list = field(default_factory=list)
    source: str = "heuristic"
    text: str = ""
    risk: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"], d["end"] = round(self.start, 2), round(self.end, 2)
        d["score"] = round(self.score, 1)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        keys = {"start", "end", "title", "hook", "score", "reason", "hashtags", "source", "text", "risk"}
        return cls(**{k: v for k, v in d.items() if k in keys})


# ----------------------------------------------------------------------
# Frases
# ----------------------------------------------------------------------
def build_sentences(words: list[dict], gap: float = 0.7, max_words: int = 40) -> list[Sentence]:
    out: list[Sentence] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        tok = w["word"].strip()
        ends = tok.endswith((".", "?", "!", "…"))
        big_gap = nxt is not None and nxt["start"] - w["end"] > gap
        if nxt is None or ends or big_gap or len(cur) >= max_words:
            text = " ".join(x["word"].strip() for x in cur)
            out.append(Sentence(cur[0]["start"], cur[-1]["end"], text, len(cur)))
            cur = []
    return out


def candidate_windows(sentences: list[Sentence], min_s: float, max_s: float, max_internal_gap: float = 3.0):
    """Pares (i, j) de frases consecutivas cuja duração fica entre min_s e max_s."""
    out = []
    n = len(sentences)
    for i in range(n):
        for j in range(i, n):
            if j > i and sentences[j].start - sentences[j - 1].end > max_internal_gap:
                break
            dur = sentences[j].end - sentences[i].start
            if dur > max_s:
                break
            if dur >= min_s:
                out.append((i, j))
    return out


# ----------------------------------------------------------------------
# Pontuação heurística (0..100)
# ----------------------------------------------------------------------
def keyword_raw(text: str, keywords: dict) -> float:
    t = text.lower()
    return sum(w * min(t.count(k.lower()), 3) for k, w in keywords.items())


def heuristic_score(text: str, n_words: int, duration: float, first_sentence: str,
                    e_mean: float, e_peak: float, keywords: dict) -> tuple[float, dict]:
    kw = min(1.0, keyword_raw(text, keywords) / 6.0)
    energy = 0.6 * e_mean + 0.4 * e_peak
    wps = n_words / max(duration, 1e-6)
    density = float(np.clip((wps - 1.2) / (3.8 - 1.2), 0.0, 1.0))
    punct = min(1.0, ((text.count("!") + text.count("?")) / max(1.0, n_words / 10.0)) / 2.0)
    fs = first_sentence.lower().strip()
    hook = 1.0 if fs.startswith(HOOK_STARTERS) else (0.6 if "?" in first_sentence else 0.0)
    parts = {"keywords": kw, "energy": energy, "density": density, "punct": punct, "hook": hook}
    total = 100 * (0.35 * kw + 0.30 * energy + 0.15 * density + 0.10 * punct + 0.10 * hook)
    return total, parts


def _short(text: str, n: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def pick_hook(sents: list[Sentence], keywords: dict, max_words: int = 12) -> str:
    """Frase curta e completa do trecho para a faixa de gancho (prefere palavras-chave, ! e ?)."""
    best, best_score = None, -1.0
    for s in sents:
        if not 3 <= s.n_words <= max_words:
            continue
        sc = keyword_raw(s.text, keywords) + (1.0 if s.text.rstrip().endswith(("!", "?")) else 0.0)
        if sc > best_score:
            best, best_score = s, sc
    if best:
        return best.text.strip()
    first = sents[0].text.split()
    return " ".join(first[:6]) + ("…" if len(first) > 6 else "")


def heuristic_clips(sentences: list[Sentence], profile: Profile, energy: EnergyCurve) -> list[Clip]:
    prefix = np.concatenate([[0], np.cumsum([s.n_words for s in sentences])])
    clips: list[Clip] = []
    for i, j in candidate_windows(sentences, profile.min_seconds, profile.max_seconds):
        start, end = sentences[i].start, sentences[j].end
        text = " ".join(s.text for s in sentences[i : j + 1])
        n_words = int(prefix[j + 1] - prefix[i])
        e_mean, e_peak = energy.stats(start, end)
        score, parts = heuristic_score(text, n_words, end - start, sentences[i].text, e_mean, e_peak, profile.keywords)
        top = max(parts, key=parts.get)
        clips.append(Clip(
            start=start, end=end, score=score, source="heuristic", text=text,
            title=_short(sentences[i].text), hook=pick_hook(sentences[i : j + 1], profile.keywords),
            reason=f"Heurística (destaque: {top}; energia {parts['energy']:.2f}, palavras-chave {parts['keywords']:.2f})",
        ))
    return clips


# ----------------------------------------------------------------------
# Ajuste fino nos limites (palavras inteiras)
# ----------------------------------------------------------------------
def snap(start: float, end: float, words: list[dict], min_s: float, max_s: float,
         media_dur: float, pad_before: float = 0.15, pad_after: float = 0.35) -> tuple[float, float] | None:
    if not words:
        return None
    starts = [w["start"] for w in words]
    ends = [w["end"] for w in words]
    i = bisect.bisect_right(ends, start)          # primeira palavra que termina depois de `start`
    j = bisect.bisect_left(starts, end) - 1       # última palavra que começa antes de `end`
    if i >= len(words) or j < i:
        return None
    s, e = words[i]["start"], words[j]["end"]
    if e - s > max_s:                             # corta na última palavra que cabe
        k = bisect.bisect_right(ends, s + max_s) - 1
        if k < i:
            return None
        e = words[k]["end"]
    if e - s < min_s * 0.7:
        return None
    s = max(0.0, s - pad_before)
    e = min(media_dur, e + pad_after) if media_dur else e + pad_after
    return s, e


def span_text(words: list[dict], start: float, end: float) -> tuple[str, int]:
    sel = [w["word"].strip() for w in words if w["end"] > start and w["start"] < end]
    return " ".join(sel), len(sel)


# ----------------------------------------------------------------------
# LLM
# ----------------------------------------------------------------------
SYSTEM = (
    "Você é um editor sênior de cortes virais para TikTok, Reels e YouTube Shorts. "
    "Escolhe trechos que prendem nos 3 primeiros segundos, têm começo, meio e fim, "
    "e provocam comentário, compartilhamento ou reação. Responde somente com JSON válido."
)

USER_TMPL = """{niche}

Regras:
- Escolha até {n} cortes, cada um entre {min_s:.0f}s e {max_s:.0f}s.
- Início e fim devem coincidir com início/fim de frases. Não sobreponha cortes.
- O trecho precisa abrir com um gancho forte (pergunta, afirmação ousada, reação, revelação).
- Use SOMENTE tempos em segundos que apareçam nos marcadores [t] da transcrição.
- "title" e "hook" no mesmo idioma do vídeo; hook com no máximo 8 palavras (aparece na tela).
- "score" de 0 a 100 = potencial de retenção e compartilhamento.
- "hashtags": até 4, relevantes ao trecho.

Responda APENAS com um array JSON:
[{{"start": 12.3, "end": 45.0, "title": "...", "hook": "...", "score": 87, "reason": "por que funciona", "hashtags": ["#..."]}}]

Transcrição:
{transcript}
"""


def chunk_sentences(sentences: list[Sentence], chunk_s: float = 600, overlap_s: float = 45) -> list[list[Sentence]]:
    chunks: list[list[Sentence]] = []
    i = 0
    while i < len(sentences):
        t0 = sentences[i].start
        j = i
        while j < len(sentences) and sentences[j].end - t0 <= chunk_s:
            j += 1
        j = max(j, i + 1)
        chunks.append(sentences[i:j])
        if j >= len(sentences):
            break
        k = j - 1
        while k > i and sentences[j - 1].end - sentences[k - 1].start <= overlap_s:
            k -= 1
        i = k if k > i else j
    return chunks


def llm_clips(sentences: list[Sentence], words: list[dict], profile: Profile, energy: EnergyCurve,
              llm: LLM, n: int, media_dur: float) -> list[Clip]:
    chunks = chunk_sentences(sentences)
    per_chunk = max(3, int(np.ceil(n * 1.5 / len(chunks)))) if len(chunks) > 1 else n + 2
    out: list[Clip] = []
    for ci, chunk in enumerate(chunks, 1):
        log(f"LLM: analisando trecho {ci}/{len(chunks)} ({fmt_ts(chunk[0].start)}–{fmt_ts(chunk[-1].end)})")
        lines = "\n".join(f"[{s.start:.1f}] {s.text}" for s in chunk)
        prompt = USER_TMPL.format(niche=profile.niche_prompt.strip(), n=per_chunk,
                                  min_s=profile.min_seconds, max_s=profile.max_seconds, transcript=lines)
        try:
            data = extract_json(llm.complete(SYSTEM, prompt))
            if isinstance(data, dict):
                data = data.get("clips") or data.get("cortes") or [data]
        except Exception as e:  # um trecho falho não derruba o vídeo
            log(f"  aviso: trecho {ci} falhou ({e}); seguirei com os demais")
            continue
        for item in data:
            try:
                s0, e0 = float(item["start"]), float(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            snapped = snap(s0, e0, words, profile.min_seconds, profile.max_seconds, media_dur)
            if not snapped:
                continue
            s, e = snapped
            text, n_words = span_text(words, s, e)
            first = text.split(".")[0] if text else ""
            e_mean, e_peak = energy.stats(s, e)
            h, _ = heuristic_score(text, n_words, e - s, first, e_mean, e_peak, profile.keywords)
            try:
                llm_score = float(item.get("score", 50))
            except (TypeError, ValueError):
                llm_score = 50.0
            tags = [t if str(t).startswith("#") else f"#{t}" for t in (item.get("hashtags") or [])][:4]
            out.append(Clip(
                start=s, end=e, score=0.7 * llm_score + 0.3 * h, source="llm", text=text,
                title=str(item.get("title") or _short(text))[:100],
                hook=str(item.get("hook") or _short(text, 60))[:80],
                reason=str(item.get("reason") or ""), hashtags=tags,
            ))
    return out


# ----------------------------------------------------------------------
# Seleção final
# ----------------------------------------------------------------------
def pick_non_overlapping(clips: list[Clip], n: int, min_gap: float = 1.0) -> list[Clip]:
    chosen: list[Clip] = []
    for c in sorted(clips, key=lambda c: c.score, reverse=True):
        if all(c.end + min_gap <= o.start or c.start >= o.end + min_gap for o in chosen):
            chosen.append(c)
        if len(chosen) >= n:
            break
    return chosen


def risk_flags(text: str, risk_keywords: list) -> list[str]:
    t = text.lower()
    return [k for k in risk_keywords if k.lower() in t]


def plan(transcript: dict, profile: Profile, energy: EnergyCurve | None = None,
         llm: LLM | None = None, n: int | None = None) -> list[Clip]:
    n = n or profile.clips
    energy = energy or EMPTY
    words = flat_words(transcript)
    if not words:
        raise ValueError("Transcrição vazia: não há fala para cortar")
    media_dur = float(transcript.get("duration") or words[-1]["end"])
    sentences = build_sentences(words)

    clips: list[Clip] = []
    if llm:
        clips = llm_clips(sentences, words, profile, energy, llm, n, media_dur)
        if not clips:
            log("LLM não retornou cortes válidos; usando a seleção heurística")
    chosen = pick_non_overlapping(clips, n) if clips else []
    if len(chosen) < n:
        pool = clips + heuristic_clips(sentences, profile, energy)
        chosen = pick_non_overlapping(pool, n)
    for c in chosen:
        c.risk = risk_flags(c.text, profile.risk_keywords)
    return chosen
