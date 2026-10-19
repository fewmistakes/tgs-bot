FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg: video backgrounds and H.264 output
# libcairo/libpango: python-lottie fallback renderer
# libgomp1: OpenMP runtime for rlottie
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgomp1 \
        ca-certificates \
        curl \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

ARG GIFSKI_VERSION=1.32.0
RUN curl -fsSL "https://github.com/ImageOptim/gifski/releases/download/${GIFSKI_VERSION}/gifski-${GIFSKI_VERSION}.tar.xz" \
        -o /tmp/gifski.tar.xz \
    && tar -C /tmp -xf /tmp/gifski.tar.xz \
    && install -m 0755 /tmp/gifski-${GIFSKI_VERSION}/linux/gifski /usr/local/bin/gifski \
    && rm -rf /tmp/gifski* \
    && gifski --version

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd -m -u 10001 bot \
    && mkdir -p /app/data/tmp \
    && chown -R bot:bot /app
USER bot

EXPOSE 8080
CMD ["python", "bot.py"]
