# Dockerfile — 把"Python 版本 + 依赖 + 代码 + 启动命令"打包成镜像，在任何装了 Docker 的机器上运行结果一致。
# 构建与运行见 docker-compose.yml 和 README 的"用 Docker 运行"。
#
# 镜像里只有代码和依赖。会变的东西都不在镜像里，运行时由 docker-compose.yml 挂进来：
#   docs/（资料 PDF）、index/（向量索引）、data/（CSV）、logs/（运行日志）、state/（记忆数据库）、嵌入模型缓存。
# .env 不进镜像（见 .dockerignore）：密钥在运行时通过环境变量传入。

FROM python:3.13-slim

# PyPI 上 Linux 版的 torch 会连带下载 NVIDIA CUDA 库（好几 GB）；容器里没有 GPU，用不上，
# 所以先从 PyTorch 官方的 CPU 索引装 torch，再装 requirements.txt（torch 已满足，不会重装）。
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/hf-cache \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# 先拷 requirements.txt 单独装依赖，再拷代码：改代码只重跑最后一层，依赖层走缓存。
COPY requirements.txt .
RUN pip install torch --index-url "$TORCH_INDEX_URL" \
 && pip install -r requirements.txt

COPY . .
# 运行时目录先建好：不用 docker-compose 单独 docker run 时也能启动（用 compose 时这些目录会被宿主机目录覆盖）。
RUN mkdir -p data docs index logs state

# 容器里要监听 0.0.0.0，宿主机才能把请求转发进来；对外仍只开放 127.0.0.1（见 docker-compose.yml 的 ports）。
# 记忆数据库放进 state/，这样能整个目录挂载出来；本机直接 python webapp.py 时这两个值不变（见 config.py）。
ENV WEB_HOST=0.0.0.0 \
    MEMORY_DB_PATH=state/memory.sqlite3

EXPOSE 8000
CMD ["python", "webapp.py"]
