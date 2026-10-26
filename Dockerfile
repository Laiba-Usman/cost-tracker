FROM python:3.11-slim

WORKDIR /app

# Install system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Hugging Face runs with non-root user (uid 1000)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    MPLCONFIGDIR=/tmp/mpl \
    COOKIE_SECURE=1 \
    TRUST_PROXY=1

WORKDIR $HOME/app
COPY --chown=user . $HOME/app

EXPOSE 7860

CMD ["sh", "-c", "gunicorn app:app --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:7860"]
