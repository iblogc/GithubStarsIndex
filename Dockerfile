# 使用轻量级 Python 镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# 安装系统依赖（如 git，如果以后需要的话，目前仅需基础工具）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 确保脚本具有执行权限
RUN chmod +x scripts/sync_stars.py
RUN chmod +x docker-entrypoint.sh

# 暴露端口（虽然 sync 主要是后台脚本，但为了统一性声明）
# HTML 展示由单独的 Nginx 处理，这里不强制暴露端口

ENTRYPOINT ["/app/docker-entrypoint.sh"]
