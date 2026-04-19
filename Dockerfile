FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY web ./web
COPY web_server.py video_generator.py README.md ./

ENV PYTHONUNBUFFERED=1 \
    GENERATOR_BACKEND=ffmpeg \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

CMD ["python3", "web_server.py"]
