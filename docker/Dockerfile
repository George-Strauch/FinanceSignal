# ── Stage 1: Build React frontend ─────────────────────────────────────
FROM node:22-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python application ──────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy NER model
RUN python -m spacy download en_core_web_lg

# Copy application source
COPY src/ src/
COPY app/ app/
COPY bots/ bots/
COPY pyproject.toml .

# Install sentinel package in editable mode
RUN pip install --no-cache-dir -e .

# Copy built frontend from stage 1
COPY --from=frontend-build /build/dist frontend/dist

# Mutable data lives on a bind-mounted volume
ENV DATA_DIR=/app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
