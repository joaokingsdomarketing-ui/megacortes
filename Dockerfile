FROM python:3.11-slim

# nodejs: o yt-dlp precisa de um runtime JavaScript para baixar do YouTube
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fontconfig fonts-montserrat fonts-liberation libglib2.0-0 nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

# Tudo (vídeos, cortes, estado da interface) fica na pasta montada em /data
WORKDIR /data
EXPOSE 8000
ENTRYPOINT ["megacortes"]
CMD ["web", "--host", "0.0.0.0", "--data", "/data"]
# Interface web:  docker compose up        (abre em http://localhost:8000)
# Linha de comando: docker compose run --rm megacortes run /data/videos/meu-video.mp4 --profile gta6
