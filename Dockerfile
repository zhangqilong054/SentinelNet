# SentinelNet 哨兵网络 — 校园网加密流量入侵检测系统 Docker 镜像
# 用法: docker compose up --build
# 多阶段构建：减少最终镜像体积
#
# ADR-0001 §4.1 约束：单 worker，违反即故障。
# 使用 Uvicorn 单 worker 运行，运行时状态全部驻留进程内。

# ── 前端构建阶段 ──────────────────────────────────────────────
FROM node:22-slim AS frontend-builder

WORKDIR /frontend

# 先复制依赖文件，利用 Docker 缓存层
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --prefer-offline --legacy-peer-deps

# 复制前端源码并构建
COPY frontend/ ./
RUN npm run build

# ── Python 依赖构建阶段 ──────────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /build

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── 运行阶段 ──────────────────────────────────────────────────
FROM python:3.13-slim

# 系统依赖（Scapy 运行时需要 tcpdump；libpcap-dev 仅构建时需要，此处省略）
RUN apt-get update && apt-get install -y --no-install-recommends \
    tcpdump \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从构建阶段复制已安装的 Python 包
COPY --from=builder /install /usr/local

# 从前端构建阶段复制构建产物
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

# 复制项目源码
COPY src/ src/
COPY pyproject.toml .

# 安装项目包（纯 Python，无需编译）
RUN pip install --no-cache-dir --no-deps -e .

# 暴露 Web 面板端口（默认 5000，可通过 CAMPUS_IDS_WEB_PORT 覆盖）
EXPOSE 5000

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV CAMPUS_IDS_DATA_DIR=/app/data
ENV CAMPUS_IDS_LOG_DIR=/app/logs
# 前端模式：new（Vue3 SPA）或 legacy（旧 Jinja2 模板）
ENV CAMPUS_IDS_FRONTEND=new

# 健康检查（/api/health 已免认证，无需额外配置）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/health')" || exit 1

# 非 root 用户运行（安全加固）
RUN useradd --create-home appuser && mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app

# 数据持久化目录（必须在 mkdir+chown 之后声明，卷初始化才继承 appuser 属主）
VOLUME ["/app/data", "/app/logs"]

USER appuser

# Uvicorn 单 worker 运行（ADR-0001 §4.1：多 worker 会导致状态分裂）
# WEB_CONCURRENCY>1 时 create_app() 会拒绝启动
CMD ["uvicorn", "campus_ids.web_new.app:create_app", "--host", "0.0.0.0", "--port", "5000", "--factory"]