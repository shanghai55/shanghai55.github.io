# Persona Studio — cloud deploy (runs 24/7, PC can be off)
# Deploy to Railway, Render, Fly.io, or any Docker host.
#
# Build:  docker build -t persona-studio .
# Run:    docker run -p 7860:7860 -e XAI_API_KEY=your-key -v persona-data:/app/data persona-studio

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PERSONA_CLOUD=true
ENV PERSONA_BIND_HOST=0.0.0.0
ENV PERSONA_REMOTE=true
ENV PERSONA_SHARE=false
ENV GRADIO_SERVER_PORT=7860
ENV PORT=7860

EXPOSE 7860

VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"7860\")}/', timeout=5)" || exit 1

CMD ["python", "app.py", "--no-browser", "--background"]