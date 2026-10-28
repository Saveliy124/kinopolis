FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CINEMA_HOST=0.0.0.0 \
    CINEMA_PORT=8000 \
    CINEMA_DB=/data/cinema.db

WORKDIR /app

RUN addgroup --system cinema && adduser --system --ingroup cinema cinema \
    && mkdir -p /data && chown cinema:cinema /data

COPY --chown=cinema:cinema cinema /app/cinema
COPY --chown=cinema:cinema static /app/static
COPY --chown=cinema:cinema database /app/database
COPY --chown=cinema:cinema docs/openapi.yaml /app/docs/openapi.yaml

USER cinema
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).read()"

CMD ["python", "-m", "cinema"]
