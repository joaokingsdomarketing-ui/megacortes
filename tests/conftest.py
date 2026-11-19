import json
import subprocess
from pathlib import Path

import pytest

# Frases de ~4s. Trechos com palavras-chave de GTA 6 ficam em 20-40s, onde o áudio sintético é mais alto.
SENTENCES = [
    "Então pessoal hoje vamos falar de um assunto bem tranquilo aqui.",
    "Primeiro deixa eu agradecer a todo mundo que se inscreveu no canal.",
    "Não esqueçam de curtir o vídeo e ativar o sininho de notificações.",
    "Vamos começar falando um pouco sobre o que aconteceu durante a semana.",
    "Olha isso, o trailer do GTA 6 mostrou Vice City com uma física absurda!",
    "Gente, a polícia e os NPCs reagem de um jeito que eu não acredito!",
    "Você reparou nesse easter egg escondido no mapa de Leonida?",
    "A Rockstar caprichou demais na Lucia e no Jason, é incrível!",
    "Compara com GTA 5 e a diferença de gráficos e de física é insana!",
    "Calma, e o lançamento no PS5 e no Xbox, será que vem em 2026?",
    "Bom, isso foi tudo por hoje, então muito obrigado por assistir.",
    "Nos vemos no próximo vídeo, até mais e fiquem com Deus pessoal.",
    "Ah e antes de ir, passa lá no meu outro canal também por favor.",
    "Tem sorteio rolando e o link está na descrição do vídeo ali embaixo.",
    "Tchau tchau, um grande abraço para todos e até a próxima vez.",
]
SENT_DUR = 4.0


def make_transcript(sentences=SENTENCES, sent_dur=SENT_DUR) -> dict:
    segments = []
    for i, text in enumerate(sentences):
        t0 = i * sent_dur
        toks = text.split()
        step = (sent_dur - 0.3) / len(toks)
        words = [{"start": t0 + k * step, "end": t0 + (k + 1) * step - 0.02, "word": " " + tok}
                 for k, tok in enumerate(toks)]
        segments.append({"start": t0, "end": t0 + sent_dur - 0.3, "text": text, "words": words})
    return {"language": "pt", "duration": len(sentences) * sent_dur, "segments": segments}


@pytest.fixture(scope="session")
def transcript():
    return make_transcript()


@pytest.fixture(scope="session")
def video(tmp_path_factory) -> Path:
    """60s 1280x720 com áudio senoidal: rajadas ALTAS em 20-24s e 40-44s."""
    out = tmp_path_factory.mktemp("media") / "teste.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=60",
        "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*440*t)*(0.04+0.9*between(mod(t,20),0,4))*gt(t,19)+sin(2*PI*330*t)*0.04*lt(t,19)':s=16000:d=60",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


@pytest.fixture(scope="session")
def transcript_file(tmp_path_factory, transcript) -> Path:
    p = tmp_path_factory.mktemp("tr") / "transcript.json"
    p.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    return p
