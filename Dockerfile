# ===== Stage 1: Build frontend =====
FROM node:20-alpine AS frontend-builder

ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable && corepack prepare pnpm@latest --activate
RUN apk add --no-cache git libc6-compat

WORKDIR /frontend

ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN git clone --depth 1 https://github.com/Maximax67/teleeye-web .
RUN pnpm install --frozen-lockfile && pnpm build

# ===== Stage 2: Build backend =====
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/root

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-builder /frontend/out ./static

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
