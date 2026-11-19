import json

import numpy as np

from megacortes.audio import EnergyCurve
from megacortes.config import load_profile
from megacortes.highlights import (Clip, build_sentences, candidate_windows, chunk_sentences, heuristic_score,
                                   pick_non_overlapping, plan, risk_flags, snap)
from megacortes.llm import extract_json
from megacortes.transcribe import flat_words


def test_sentences_built_from_punctuation(transcript):
    s = build_sentences(flat_words(transcript))
    assert len(s) == 15
    assert s[4].text.startswith("Olha isso")
    assert abs(s[4].start - 16.0) < 0.01


def test_candidate_windows_respect_bounds(transcript):
    s = build_sentences(flat_words(transcript))
    wins = candidate_windows(s, 20, 30)
    assert wins
    for i, j in wins:
        assert 20 <= s[j].end - s[i].start <= 30


def test_candidate_windows_do_not_span_dead_air():
    from megacortes.highlights import Sentence
    s = [Sentence(0, 10, "a", 5), Sentence(10.5, 20, "b", 5), Sentence(40, 50, "c", 5)]
    assert (1, 2) not in candidate_windows(s, 5, 60)   # buraco de 20s no meio


def test_snap_cuts_on_word_boundaries(transcript):
    words = flat_words(transcript)
    s, e = snap(16.4, 40.2, words, 20, 55, 60.0)
    starts = [round(w["start"], 3) for w in words]
    ends = [round(w["end"], 3) for w in words]
    assert round(s + 0.15, 3) in starts       # início = início de uma palavra (menos o padding de 0.15s)
    assert round(e - 0.35, 3) in ends         # fim = fim de uma palavra (mais o padding de 0.35s)
    assert s < 16.4 < e and e > 39.0
    assert snap(0, 3, words, 20, 55, 60.0) is None   # curto demais


def test_snap_trims_to_max(transcript):
    words = flat_words(transcript)
    s, e = snap(0, 60, words, 20, 30, 60.0)
    assert e - s <= 30 + 0.5


def test_pick_non_overlapping_prefers_best():
    clips = [Clip(0, 30, score=90), Clip(10, 40, score=95), Clip(50, 80, score=50)]
    chosen = pick_non_overlapping(clips, 5)
    assert [c.score for c in chosen] == [95, 50]


def test_keywords_and_energy_raise_score():
    kw = {"gta 6": 2, "vice city": 2}
    low, _ = heuristic_score("bla bla bla", 3, 25, "bla bla", 0.1, 0.2, kw)
    high, parts = heuristic_score("Olha o GTA 6 em Vice City! Incrível!", 90, 25, "Olha o GTA 6", 0.9, 1.0, kw)
    assert high > low + 25 and parts["hook"] == 1.0


def test_chunking_covers_everything():
    from megacortes.highlights import Sentence
    s = [Sentence(i * 5, i * 5 + 4.5, f"s{i}", 5) for i in range(400)]  # ~2000s
    chunks = chunk_sentences(s, chunk_s=600, overlap_s=45)
    assert len(chunks) >= 3
    covered = {x.text for c in chunks for x in c}
    assert covered == {x.text for x in s}
    assert all(c[-1].end - c[0].start <= 600 for c in chunks)


def test_extract_json_variants():
    assert extract_json('[{"a":1}]') == [{"a": 1}]
    assert extract_json('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert extract_json('Claro! Aqui está:\n[{"a":1}]\nEspero ajudar') == [{"a": 1}]
    assert extract_json('{"clips":[1]}') == {"clips": [1]}


def test_risk_flags():
    assert risk_flags("isso é um SPOILER do final do jogo", ["spoiler", "final do jogo", "leak"]) == ["spoiler", "final do jogo"]


def test_plan_heuristic_finds_gta_section(transcript):
    profile = load_profile("gta6")
    # energia alta justamente em 20-24s e 40-44s, como no vídeo sintético
    vals = np.full(120, 0.15, dtype=np.float32)
    vals[40:48] = 1.0
    clips = plan(transcript, profile, EnergyCurve(0.5, vals), llm=None, n=2)
    assert clips
    best = clips[0]
    assert best.start < 28 and best.end > 16          # sobrepõe a parte do trailer
    assert "gta 6" in best.text.lower()
    assert 20 <= best.duration <= 55


def test_plan_flags_risky_text():
    from conftest import SENTENCES, make_transcript
    sentences = list(SENTENCES)
    sentences[7] = "Esse spoiler do final do jogo no GTA 6 é incrível, a Rockstar caprichou!"
    clips = plan(make_transcript(sentences), load_profile("gta6"), None, llm=None, n=1)
    assert "spoiler" in clips[0].risk and "final do jogo" in clips[0].risk


def test_heuristic_hook_is_short_complete_sentence(transcript):
    clip = plan(transcript, load_profile("gta6"), None, llm=None, n=1)[0]
    assert "…" not in clip.hook and 3 <= len(clip.hook.split()) <= 12
    assert clip.hook in clip.text                      # frase realmente falada no trecho


def test_hook_fallback_truncates_long_first_sentence():
    from megacortes.highlights import Sentence, pick_hook
    long = Sentence(0, 20, "uma duas três quatro cinco seis sete oito nove dez onze doze treze catorze", 14)
    assert pick_hook([long], {}) == "uma duas três quatro cinco seis…"


def test_plan_no_false_risk_on_clean_text(transcript):
    clips = plan(transcript, load_profile("gta6"), None, llm=None, n=1)
    assert clips[0].risk == []


class FakeLLM:
    """Responde como um LLM: devolve JSON com tempos aproximados (sem alinhar às palavras)."""
    provider, model = "fake", "fake"

    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def complete(self, system, user, max_tokens=0):
        self.calls += 1
        assert "[16.0]" in user                      # marcadores de tempo presentes
        return "```json\n" + json.dumps(self.payload) + "\n```"


def test_plan_with_llm_snaps_and_blends_scores(transcript):
    profile = load_profile("gta6")
    llm = FakeLLM([
        {"start": 16.3, "end": 41.0, "title": "GTA 6 muda tudo", "hook": "Olha essa física", "score": 92,
         "reason": "gancho + reação", "hashtags": ["gta6", "#rockstar"]},
        {"start": 1, "end": 5, "title": "curto", "score": 99},          # curto demais -> descartado
        {"start": "x"},                                                  # lixo -> ignorado
    ])
    clips = plan(transcript, profile, None, llm=llm, n=1)
    c = clips[0]
    assert llm.calls == 1 and c.source == "llm"
    assert c.title == "GTA 6 muda tudo" and c.hook == "Olha essa física"
    assert c.hashtags == ["#gta6", "#rockstar"]
    assert 15.9 < c.start < 16.3 and c.end > 40.0     # encaixado nas palavras (16.3 cai dentro da 2ª palavra)
    assert c.score > 60


def test_plan_falls_back_when_llm_returns_garbage(transcript):
    class Bad(FakeLLM):
        def complete(self, *a, **k):
            return "desculpe, não consigo"
    clips = plan(transcript, load_profile("gta6"), None, llm=Bad([]), n=2)
    assert clips and all(c.source == "heuristic" for c in clips)
