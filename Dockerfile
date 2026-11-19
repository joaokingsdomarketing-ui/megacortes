FROM python:3.11-slim

# Dependências do sistema: ffmpeg, fontes, curl e unzip
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fontconfig fonts-montserrat fonts-liberation libglib2.0-0 curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno: runtime JavaScript oficial recomendado pelo yt-dlp para contornar desafios do YouTube (EJS)
RUN curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && rm -f /tmp/deno.zip \
    && chmod +x /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

WORKDIR /data
EXPOSE 8000
ENTRYPOINT ["megacortes"]
CMD ["web", "--host", "0.0.0.0", "--data", "/data"]
