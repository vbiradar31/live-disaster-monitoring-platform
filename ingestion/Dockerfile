FROM python:3.13-slim

WORKDIR /app

COPY ingestion/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Local .env files contain development settings; runtime configuration is injected.
# Migrations run through the host development workflow, so the image stays app-only.
COPY ingestion/app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
