"""Perfis de nicho (YAML) -> dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
REFRAME_MODES = ("blur", "center", "face")


@dataclass
class CaptionStyle:
    font: str = "Montserrat"
    size: int = 80
    primary: str = "#FFFFFF"
    highlight: str = "#FFD400"
    outline: str = "#000000"
    outline_width: int = 7
    words_per_line: int = 3
    uppercase: bool = True
    margin_v_blur: int = 470   # px a partir da base (canvas 1080x1920) no layout com fundo desfocado
    margin_v_full: int = 420   # idem, quando o vídeo ocupa a tela toda
    hook_font_size: int = 70
    hook_box: str = "#E4002B"
    hook_text: str = "#FFFFFF"
    hook_seconds: float = 2.8
    hook_margin_v: int = 330


@dataclass
class Profile:
    name: str = "default"
    language: str | None = None
    niche_prompt: str = ""
    keywords: dict = field(default_factory=dict)
    risk_keywords: list = field(default_factory=list)
    hashtags: list = field(default_factory=list)
    min_seconds: float = 20.0
    max_seconds: float = 58.0
    clips: int = 8
    reframe: str = "blur"
    caption: CaptionStyle = field(default_factory=CaptionStyle)


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def load_profile(name_or_path: str) -> Profile:
    p = Path(name_or_path)
    if p.suffix not in (".yaml", ".yml"):
        p = PROFILES_DIR / f"{name_or_path}.yaml"
    if not p.exists():
        raise FileNotFoundError(
            f"Perfil '{name_or_path}' não encontrado. Disponíveis: {', '.join(list_profiles())}"
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cap_data = data.pop("caption", None) or {}

    known = {f.name for f in fields(Profile)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Campos desconhecidos no perfil {p.name}: {sorted(unknown)}")
    cap_known = {f.name for f in fields(CaptionStyle)}
    cap_unknown = set(cap_data) - cap_known
    if cap_unknown:
        raise ValueError(f"Campos desconhecidos em caption ({p.name}): {sorted(cap_unknown)}")

    data.setdefault("name", p.stem)
    profile = Profile(**data, caption=CaptionStyle(**cap_data))
    validate_profile(profile)
    return profile


def validate_profile(profile: Profile) -> None:
    if profile.reframe not in REFRAME_MODES:
        raise ValueError(f"reframe deve ser um de {REFRAME_MODES}, veio '{profile.reframe}'")
    if not 0 < profile.min_seconds < profile.max_seconds:
        raise ValueError("min_seconds deve ser > 0 e menor que max_seconds")
    if profile.clips < 1:
        raise ValueError("clips deve ser >= 1")
