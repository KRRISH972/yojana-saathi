# Yojana Saathi - container image for Hugging Face Spaces (Docker SDK) or any Docker host.
#   docker build -t yojana-saathi .
#   docker run -p 7860:7860 -e GEMINI_API_KEY=... yojana-saathi
# GEMINI_API_KEY is never baked in: pass it at run time (on Hugging Face: a Space secret).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Hugging Face Spaces runs containers as user 1000; build as that user so the app can
# read (and Chroma can write to) everything it owns.
RUN useradd --create-home --uid 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface
WORKDIR /home/user/app

# CPU-only PyTorch first: the default Linux wheel pulls in gigabytes of CUDA libraries.
COPY --chown=user requirements.txt .
RUN pip install --user torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --user -r requirements.txt

COPY --chown=user . .

# Bake the embedding model and the search index into the image, so the Space starts
# quickly and never downloads the ~470 MB model at startup.
RUN python -c "from backend.services.embeddings import get_embedding_model; get_embedding_model()" \
 && python scripts/validate_data.py \
 && python scripts/ingest.py

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=4)"

# --proxy-headers: trust X-Forwarded-For from the Hugging Face proxy (used for rate limits).
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
