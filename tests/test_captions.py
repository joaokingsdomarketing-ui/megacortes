from megacortes.captions import _inline_color, _style_color, _ts, build_ass, group_words
from megacortes.config import CaptionStyle


def W(start, end, word):
    return {"start": start, "end": end, "word": word}


def test_color_conversion_is_bgr():
    assert _style_color("#FFD400") == "&H0000D4FF"
    assert _inline_color("#FFD400") == "&H00D4FF&"
    assert _inline_color("#E4002B") == "&H2B00E4&"


def test_timestamp_format():
    assert _ts(0) == "0:00:00.00"
    assert _ts(61.5) == "0:01:01.50"
    assert _ts(3725.25) == "1:02:05.25"
    assert _ts(-3) == "0:00:00.00"


def test_group_words_by_count_gap_and_punctuation():
    words = [W(0, .3, "a"), W(.3, .6, "b"), W(.6, .9, "c"), W(.9, 1.2, "d"),
             W(3.0, 3.3, "e"), W(3.3, 3.6, "f."), W(3.6, 3.9, "g")]
    groups = group_words(words, per_line=3)
    assert [[w["word"] for w in g] for g in groups] == [["a", "b", "c"], ["d"], ["e", "f."], ["g"]]


def test_ass_events_are_relative_and_highlight_active_word():
    words = [W(10.0, 10.4, " Olha"), W(10.4, 10.8, " isso"), W(10.8, 11.2, " aqui"), W(30.0, 31.0, " fora")]
    style = CaptionStyle()
    ass = build_ass(words, clip_start=9.5, clip_end=12.0, style=style, hook="Isso é absurdo", layout="blur")
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    caps = [l for l in dialogues if ",Cap," in l]
    hooks = [l for l in dialogues if ",Hook," in l]
    assert len(caps) == 3 and len(hooks) == 1       # 3 palavras dentro do corte; "fora" excluída
    assert caps[0].split(",")[1] == "0:00:00.50"    # 10.0 - 9.5
    assert "{\\c&H00D4FF&}OLHA" in caps[0]           # palavra ativa em amarelo, caixa alta
    assert "{\\c&H00D4FF&}ISSO" in caps[1] and "OLHA" in caps[1]
    assert "FORA" not in ass
    assert "ISSO É ABSURDO" in hooks[0].upper()
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


def test_ass_strips_override_injection():
    words = [W(0, .5, " {\\fs200}evil\\N")]
    ass = build_ass(words, 0, 2, CaptionStyle(uppercase=False), hook=None)
    caption_line = [l for l in ass.splitlines() if ",Cap," in l][0]
    assert "\\fs200" not in caption_line and "{fs200}" not in caption_line


def test_long_hook_is_ellipsized_to_three_lines():
    hook = " ".join(["palavra"] * 40)
    ass = build_ass([W(0, .5, " oi")], 0, 5, CaptionStyle(), hook=hook)
    line = [l for l in ass.splitlines() if ",Hook," in l and l.startswith("Dialogue")][0]
    text = line.split(",,", 1)[1] if ",,{" not in line else line.split(",,{", 1)[1]
    assert text.count("\\N") == 2 and text.rstrip().endswith("…")


def test_no_hook_when_disabled():
    ass = build_ass([W(0, .5, " oi")], 0, 2, CaptionStyle(), hook=None)
    assert ",Hook," not in ass.split("[Events]")[1]
