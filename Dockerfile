FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends libexpat1 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-plugins-no-deps.txt ./
RUN uv pip install --system --no-cache \
    --default-index https://mirrors.aliyun.com/pypi/simple \
    -r requirements.txt && \
    uv pip install --system --no-cache --no-deps \
    --default-index https://mirrors.aliyun.com/pypi/simple \
    -r requirements-plugins-no-deps.txt && \
    uv pip install --system --no-cache --no-deps \
    --default-index https://mirrors.aliyun.com/pypi/simple \
    rapidocr_onnxruntime==1.4.4

COPY main.py ./
COPY nao_bot/ ./nao_bot/

USER 10001:10001

CMD ["python", "main.py"]
