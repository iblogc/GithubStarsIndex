#!/bin/sh
# docker-entrypoint.sh

set -e

# 如果没有设置 SCHEDULE_HOURS，则只运行一次并退出
if [ -z "$SCHEDULE_HOURS" ]; then
    echo "--- 🚀 开始执行单次同步任务 ---"
    python scripts/sync_stars.py "$@"
    echo "--- ✅ 同步任务完成 ---"
    exit 0
fi

# 如果设置了 SCHEDULE_HOURS，则进入循环执行模式
echo "--- 🕒 进入周期运行模式，间隔时间: $SCHEDULE_HOURS 小时 ---"

while true; do
    echo "--- 🚀 $(date): 开始自动同步 ---"
    python scripts/sync_stars.py "$@"
    
    # 计算下一次运行时间
    SLEEP_SECONDS=$((SCHEDULE_HOURS * 3600))
    echo "--- 💤 $(date): 同步完成，等待 $SCHEDULE_HOURS 小时后再次运行 ---"
    sleep $SLEEP_SECONDS
done
