# ===== Stage 1: Build frontend =====
FROM node:20-alpine AS frontend-builder

RUN apk add --no-cache git

WORKDIR /frontend

ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN git clone --depth 1 https://github.com/Maximax67/teleeye-web .
RUN npm install && npm run build

# ===== Stage 2: Build backend =====
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/root \
    ATTACH_FRONTEND=1 \
    FRONTEND_PATH=./static \
    API_PREFIX=/api

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-builder /frontend/out ./static

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
