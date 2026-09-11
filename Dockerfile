# SentinelNet 哨兵网络 — 校园网加密流量入侵检测系统 Docker 镜像
# 用法: docker compose up --build
# 多阶段构建：减少最终镜像体积

# ── 构建阶段 ──────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── 运行阶段 ──────────────────────────────────────────────────
FROM python:3.11-slim

# 系统依赖（Scapy 运行时需要 tcpdump；libpcap-dev 仅构建时需要，此处省略）
RUN apt-get update && apt-get install -y --no-install-recommends \
    tcpdump \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从构建阶段复制已安装的 Python 包
COPY --from=builder /install /usr/local

# 复制项目源码
COPY src/ src/
COPY pyproject.toml .

# 安装项目包（纯 Python，无需编译）
RUN pip install --no-cache-dir --no-deps -e .

# 暴露 Web 面板端口
EXPOSE 5000

# 数据持久化目录
VOLUME ["/app/data", "/app/logs"]

# 环境变量
ENV FLASK_APP=campus_ids.web.app
ENV PYTHONUNBUFFERED=1
ENV CAMPUS_IDS_DATA_DIR=/app/data
ENV CAMPUS_IDS_LOG_DIR=/app/logs
ENV CAMPUS_IDS_DEMO_MODE=1

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/traffic')" || exit 1

# 非 root 用户运行（安全加固）
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# 默认启动 Web 面板（演示模式，无需 Npcap）
CMD ["python", "-m", "campus_ids.web.app"]