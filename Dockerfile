FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# config.yaml is deliberately NOT baked into the image. It is the live config
# file holding real credentials, so it is gitignored and supplied at runtime by
# mounting it at /app/config.yaml (see docker-compose.yml). The example is
# copied in for reference only; the app never reads it.
COPY config.example.yaml ./config.example.yaml

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

