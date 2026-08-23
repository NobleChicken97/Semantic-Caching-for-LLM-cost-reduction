FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

# CPU-only torch — avoids pulling ~2 GB of CUDA libs into the image.
# Install a PINNED +cpu build from the PyTorch CPU index BEFORE
# requirements.txt: pip then sees torch as already satisfied and cannot
# resolve a CUDA build from PyPI (--extra-index-url alone does not
# guarantee which wheel wins; an explicit pin does).
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.5.1+cpu \
        --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Bake the embedding model into the image so cold starts don't re-download
# ~130 MB from the HF Hub on every deploy/restart.
RUN python -c "import sys; sys.path.insert(0, 'src'); from proxy.embedding import embed_texts; embed_texts(['warmup'])"

EXPOSE 8000
CMD ["sh", "-c", "uvicorn src.proxy.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
