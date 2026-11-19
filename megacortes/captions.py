"""Legendas estilo "palavra falada em destaque" (ASS) + faixa de gancho nos primeiros segundos."""
from __future__ import annotations

import textwrap

from .config import CaptionStyle

W, H = 1080, 1920


def _style_color(hex_rgb: str, alpha: str = "00") -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()


def _inline_color(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{b}{g}{r}&".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _clean(text: str, upper: bool) -> str:
    text = text.replace("{", "").replace("}", "").replace("\\", "").replace("\n", " ").strip()
    return text.upper() if upper else text


def group_words(words: list[dict], per_line: int, max_gap: float = 0.6) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (
            len(cur) >= per_line
            or w["start"] - cur[-1]["end"] > max_gap
            or cur[-1]["word"].strip().endswith((".", "?", "!"))
        ):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[dict], clip_start: float, clip_end: float, style: CaptionStyle,
              hook: str | None = None, layout: str = "blur") -> str:
    margin_v = style.margin_v_blur if layout == "blur" else style.margin_v_full
    dur = clip_end - clip_start

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Cap,{style.font},{style.size},{_style_color(style.primary)},{_style_color(style.primary)},{_style_color(style.outline)},&H80000000,-1,0,0,0,100,100,0,0,1,{style.outline_width},2,2,60,60,{margin_v},1
Style: Hook,{style.font},{style.hook_font_size},{_style_color(style.hook_text)},{_style_color(style.hook_text)},{_style_color(style.hook_box)},{_style_color(style.hook_box)},-1,0,0,0,100,100,0,0,3,16,0,8,70,70,{style.hook_margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines: list[str] = []

    if hook and style.hook_seconds > 0:
        rows = textwrap.wrap(_clean(hook, True), width=20)
        if len(rows) > 3:  # nunca descartar palavras em silêncio: corta com reticências
            rows = rows[:3]
            rows[2] = rows[2].rstrip(" .,;:!?") + "…"
        wrapped = "\\N".join(rows)
        end = min(style.hook_seconds, dur)
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(end)},Hook,,0,0,0,,{{\\fad(150,200)}}{wrapped}")

    sel = [w for w in words if w["end"] > clip_start and w["start"] < clip_end]
    rel = [
        {"start": max(0.0, w["start"] - clip_start), "end": min(dur, w["end"] - clip_start), "word": w["word"]}
        for w in sel
    ]
    groups = group_words(rel, style.words_per_line)
    hl, base = _inline_color(style.highlight), _inline_color(style.primary)

    for gi, g in enumerate(groups):
        tokens = [_clean(w["word"], style.uppercase) for w in g]
        for wi, w in enumerate(g):
            t0 = w["start"]
            if wi + 1 < len(g):
                t1 = g[wi + 1]["start"]
            else:
                nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else None
                t1 = min(w["end"] + 0.25, nxt) if nxt is not None else w["end"] + 0.25
            t1 = min(max(t1, t0 + 0.05), dur)
            if t1 <= t0:
                continue
            parts = []
            for k, tok in enumerate(tokens):
                parts.append(f"{{\\c{hl}}}{tok}{{\\c{base}}}" if k == wi else tok)
            pop = "{\\fscx88\\fscy88\\t(0,100,\\fscx100\\fscy100)}" if wi == 0 else ""
            lines.append(f"Dialogue: 0,{_ts(t0)},{_ts(t1)},Cap,,0,0,0,,{pop}{' '.join(parts)}")

    return header + "\n".join(lines) + "\n"
