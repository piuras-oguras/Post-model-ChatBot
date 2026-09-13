FROM python:3.12

WORKDIR /app

# Install deps first so this layer is cached when only app code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8011

# Fails the container if /health stops responding.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8011/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0", "--port", "8011"]