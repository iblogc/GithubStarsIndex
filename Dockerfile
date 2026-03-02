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

# 确保脚本具有执行权限，并消除可能的 CRLF 换行问题
RUN chmod +x /app/scripts/sync_stars.py \
    && sed -i 's/\r$//' /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh

# 暴露端口（虽然 sync 主要是后台脚本，但为了统一性声明）
# HTML 展示由单独的 Nginx 处理，这里不强制暴露端口

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
