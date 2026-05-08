FROM node:20-bookworm-slim AS frontend-builder
WORKDIR /frontend
COPY pwa-app/package*.json ./
RUN npm ci
COPY pwa-app/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=7860
ENV PWA_DIST_DIR=/app/pwa-app/dist

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . ./
COPY --from=frontend-builder /frontend/dist /app/pwa-app/dist

EXPOSE 7860
CMD ["python", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "7860"]
