"""Estilos prontos de legenda/gancho (cores pensadas para retenção em tela pequena)."""
from __future__ import annotations

# Campos = CaptionStyle. Só o que muda em relação ao padrão precisa estar aqui.
PRESETS = [
    {"id": "amarelo", "name": "Amarelo Viral",
     "caption": {"primary": "#FFFFFF", "highlight": "#FFD400", "outline": "#000000",
                 "hook_box": "#E4002B", "hook_text": "#FFFFFF"}},
    {"id": "neon", "name": "Neon Ciano",
     "caption": {"primary": "#FFFFFF", "highlight": "#00F0FF", "outline": "#14002E",
                 "hook_box": "#FF00C8", "hook_text": "#FFFFFF"}},
    {"id": "verde", "name": "Verde Grana",
     "caption": {"primary": "#FFFFFF", "highlight": "#39FF14", "outline": "#001A08",
                 "hook_box": "#0A8F3C", "hook_text": "#FFFFFF"}},
    {"id": "fogo", "name": "Fogo",
     "caption": {"primary": "#FFFFFF", "highlight": "#FF7A00", "outline": "#1A0500",
                 "hook_box": "#D80000", "hook_text": "#FFFFFF"}},
    {"id": "vicecity", "name": "Vice City",
     "caption": {"primary": "#FFFFFF", "highlight": "#FF4FA3", "outline": "#1B0A3A",
                 "hook_box": "#00C2D1", "hook_text": "#0B1030"}},
    {"id": "gelo", "name": "Gelo",
     "caption": {"primary": "#FFFFFF", "highlight": "#4DD2FF", "outline": "#001A33",
                 "hook_box": "#0A5CFF", "hook_text": "#FFFFFF"}},
    {"id": "roxo", "name": "Roxo Premium",
     "caption": {"primary": "#FFFFFF", "highlight": "#C77DFF", "outline": "#12002B",
                 "hook_box": "#6A00F4", "hook_text": "#FFFFFF"}},
    {"id": "limpo", "name": "Branco Limpo",
     "caption": {"primary": "#FFFFFF", "highlight": "#FFFFFF", "outline": "#000000",
                 "hook_box": "#FFFFFF", "hook_text": "#000000", "uppercase": False, "outline_width": 6}},
]

# Campos que a interface pode sobrescrever (nome -> tipo). Tudo é validado em jobs.build_profile.
CAPTION_FIELDS = {
    "size": int, "primary": "color", "highlight": "color", "outline": "color", "outline_width": int,
    "words_per_line": int, "uppercase": bool, "hook_font_size": int, "hook_box": "color",
    "hook_text": "color", "hook_seconds": float, "margin_v_blur": int, "margin_v_full": int,
}
