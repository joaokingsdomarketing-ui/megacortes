# MegaCortes

Gerador próprio de cortes verticais (9:16) a partir de vídeos longos — a mesma ideia do OpusClip, rodando na sua máquina, sem mensalidade e sem marca d'água. Vem com um perfil pronto para **GTA 6**.

```
vídeo/URL → transcrição (Whisper, palavra a palavra) → escolha dos melhores trechos
          → corte + reenquadramento 9:16 → legenda com palavra em destaque + gancho
          → clips/*.mp4 + posts.csv (título, legenda e hashtags prontos)
```

## Interface web (cole o link e pronto)

```bash
docker compose up            # primeira vez demora (baixa FFmpeg, Whisper...) — depois abra http://localhost:8000
# ou, sem Docker:  pip install -r requirements.txt && pip install -e . && megacortes web
```

1. Cole um ou **vários links** (um por linha) — YouTube, TikTok, Twitch etc. (tudo que o yt-dlp abre). Marque **Expandir playlists e canais** para transformar uma playlist em N vídeos. Também dá para **enviar arquivos** ou soltá-los em `data/videos/`.
2. Escolha o **visual**: 8 estilos de cor prontos + seletores de cor, tamanho, palavras por linha, altura da legenda, duração do gancho. A prévia do celular mostra a palavra em destaque como no vídeo final.
3. Defina **quantos cortes por vídeo**, duração mínima/máxima e **nota mínima** (só renderiza o que passa de X).
4. **Gerar cortes.** A fila baixa → transcreve → escolhe os momentos → renderiza 1080×1920 com gancho, legenda animada e título/hashtags. Dá para fechar a aba: o trabalho continua, e se o servidor reiniciar ele **retoma** de onde parou (downloads, transcrições e cortes já prontos são reaproveitados).
5. Em **Projetos**: assistir, ordenar por nota, filtrar, **editar título/gancho/início/fim e re-renderizar um corte**, copiar a legenda pronta, baixar um corte, um vídeo (ZIP) ou tudo (ZIP + `posts.csv`).

**Sobre "milhares de cortes":** um vídeo de 1 hora rende dezenas de bons momentos, não milhares. O volume vem da fila: muitos links, playlists e canais inteiros (ex.: 100 vídeos × 20 cortes = 2.000). O tempo depende da sua máquina — o gargalo é a transcrição (use `tiny`/`base` ou uma placa de vídeo para acelerar) e o render. Faça um teste com 1 vídeo antes de enfileirar centenas.

**Chaves de IA:** em *Configurações* (ficam só no seu computador) ou no `.env`. Sem chave, a escolha usa só a heurística.

**Segurança:** por padrão só `localhost` acessa. Para abrir na rede use `MEGACORTES_PASSWORD` (e `MEGACORTES_ALLOWED_HOSTS`). Arquivos locais só são aceitos de `data/videos/` ou do botão de upload.

## Como a escolha dos cortes funciona

1. **IA (opcional):** um LLM lê a transcrição e propõe trechos com título, gancho e nota de 0 a 100. Aceita Gemini, OpenAI, Anthropic, OpenRouter ou Ollama (local, grátis).
2. **Heurística (sempre ativa):** pontua cada janela por palavras-chave do nicho, energia do áudio (grito, risada, reação), densidade de fala, pontuação e gancho de abertura.
3. Nota final com IA = 70% LLM + 30% heurística. Sem chave de LLM, só a heurística decide (funciona, mas é menos esperta).
4. Os limites são ajustados para palavras inteiras, nunca cortando no meio de uma fala.

## Instalação

**Docker (mais fácil — já traz FFmpeg, Node e a fonte) — linha de comando:**

```bash
cp .env.example .env        # preencha GEMINI_API_KEY (ou outro provedor)
mkdir -p data/videos        # coloque seus vídeos aqui
docker compose run --rm megacortes run /data/videos/meu-video.mp4 --profile gta6
```

**Local:** precisa de Python 3.10+, FFmpeg e a fonte Montserrat instalada.

```bash
pip install -r requirements.txt && pip install -e .
cp .env.example .env
megacortes run meu-video.mp4 --profile gta6
```

## Uso

```bash
megacortes run VIDEO_OU_URL --profile gta6 --clips 8     # tudo de uma vez
megacortes plan VIDEO_OU_URL --profile gta6               # só escolhe (gera clips.json)
#   → edite out/<video>/clips.json (início, fim, título, gancho) se quiser
megacortes render out/<video> --only 1,3                  # renderiza só alguns
megacortes batch lista.txt --profile gta6                 # fila: um arquivo/URL por linha
```

Opções úteis: `--mode blur|center|face`, `--whisper-model small|medium|large-v3`, `--language pt`, `--llm gemini|openai|anthropic|ollama|none`, `--transcript arquivo.json` (pula o Whisper), `--no-hook`.

**Reenquadramento:** `blur` (padrão) mostra o vídeo inteiro no centro com fundo desfocado — ideal para gameplay e HUD. `center` preenche a tela toda. `face` centraliza no rosto (para vídeos de pessoa falando); se não achar rosto, cai em `blur`. O rosto é fixo por corte, não acompanha movimento.

## Saída (`out/<video>/`)

- `clips/clip_01.mp4 …` — vídeos prontos 1080×1920, áudio normalizado (-14 LUFS), com `.jpg` de capa e `.ass` das legendas
- `posts.csv` — título, gancho, legenda pronta, hashtags, nota, motivo e **alerta de risco** (abre no Excel)
- `clips.json` e `transcript.json` — editáveis e reaproveitados nas próximas execuções

## Perfil GTA 6 (`megacortes/profiles/gta6.yaml`)

Edite o texto do nicho (o que a IA deve procurar), as palavras-chave com pesos, as hashtags e as cores da legenda. Para um novo nicho, copie `default.yaml` e passe `--profile caminho/do/perfil.yaml`.

## ⚠️ Direitos e volume

Use apenas vídeos seus ou com autorização. Além disso, plataformas como o YouTube podem limitar monetização de conteúdo reaproveitado ou produzido em massa de forma repetitiva — nota alta e revisão manual dos melhores cortes costumam valer mais que volume. Confira as políticas vigentes.

## ⚠️ Antes de monetizar cortes de GTA 6

A política da Rockstar/Take-Two para vídeos com material do jogo (atualizada em 10/01/2025) define uso "não comercial" como **não ganhar dinheiro com o material do jogo publicado**, e também considera comercial usar o material em promoção de produto ou serviço. A Take-Two pode pedir remoção a qualquer momento. Também são removidos: **vazamentos e cópias antecipadas, spoilers do final, cutscenes isoladas e cheats/glitches de GTA Online**.

Por isso o perfil `gta6` marca no `posts.csv` (coluna `alerta_risco`) os cortes que citam esses temas. É apenas um aviso para revisão manual, não um filtro. Caminhos mais seguros: comentário e análise originais por cima do material, cortes de conteúdo de criadores **com autorização** (muitos pagam por visualização em programas de cortes), ou licenciamento (copyright@take2games.com). Confira a política vigente em support.rockstargames.com antes de publicar. Isto não é aconselhamento jurídico.

## O que foi testado

Testes automatizados (pytest), incluindo render real com FFmpeg em vídeo sintético: legendas, seleção, snap de palavras, parsing de JSON do LLM, requisições aos provedores (simuladas), **e a interface web** — API, proteção (senha, Host, CSRF, path traversal, upload), fila com vários vídeos, playlist expandida (simulada), cancelar, tentar de novo, retomar após reinício, Range de vídeo, ZIP, edição + re-render de um corte e filtro de nota. A página também foi percorrida no Chromium (colar → gerar → assistir → editar) sem erros de console.

**Não testado neste ambiente:** transcrição real com Whisper (modelos não baixam aqui — nos testes ela é simulada), download e listagem de playlists reais via yt-dlp, chamadas reais aos LLMs, detecção de rosto positiva e o build do Docker. Faça um primeiro teste com um vídeo curto antes de rodar em lote.

## Próximos passos possíveis

Publicação automática (TikTok/Reels/Shorts), legendas do YouTube como atalho para pular o Whisper, rastreamento de rosto dinâmico, mais fontes e estilos de legenda, variação de cores por corte para testar o que retém mais.

## Licença das referências

Esta base é código novo e próprio. A lógica foi inspirada em projetos open source (OpenShorts, MIT; AI-Youtube-Shorts-Generator, MIT; SupoClip, AGPL-3.0 — nenhum código foi copiado).
