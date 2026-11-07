# synapse0g: the dashboard, the API, the payment desk and the optimizer agent, in one service.

# 1. the web app (landing page + console)
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# 2. the server
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PIP_NO_CACHE_DIR=1 HOST=0.0.0.0 PORT=8000
WORKDIR /app
COPY pyproject.toml ./
COPY synapse0g/ synapse0g/
RUN pip install -e ".[agents]"   # editable: the code finds models.yaml, reports and web/dist beside it in /app
COPY . .
COPY --from=web /web/dist web/dist
RUN chmod +x deploy/start.sh
EXPOSE 8000
CMD ["deploy/start.sh"]
