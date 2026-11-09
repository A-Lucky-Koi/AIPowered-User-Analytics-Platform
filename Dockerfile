FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8765 DB_PATH=/app/data/insight.db
COPY server.py data.py schema.sql ./
COPY web ./web
RUN useradd --uid 10001 --create-home insight && mkdir -p /app/data && chown -R insight:insight /app
USER insight
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=2)"
CMD ["python", "server.py"]
