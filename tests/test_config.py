#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置加载回归测试（scripts/sync_stars.py 的 load_config）。

为什么必须用子进程 + 临时工作目录：
  sync_stars 在导入时执行 `load_dotenv(override=True)`。在仓库根目录直接导入，
  本地的 .env 会覆盖掉测试注入的环境变量，测出来的就不是 CI 的行为。
  把 cwd 换到没有 .env 的临时目录，才能还原 GitHub Actions 的真实环境。

覆盖的关键行为：**未配置的 GitHub Variable 会被渲染成空字符串**，
若直接采用就会覆盖默认值（AI_MODEL 变 ''、默认模型形同虚设）。

    python3 tests/test_config.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"

# 探针：把 load_config 的结果以 JSON 打印出来
PROBE = r"""
import json, sys
sys.path.insert(0, %r)
import sync_stars
try:
    cfg = sync_stars.load_config()
except SystemExit as e:
    print("EXIT:" + str(e.code))
else:
    print(json.dumps({
        "base_url": cfg["ai"]["base_url"],
        "model": cfg["ai"]["model"],
        "concurrency": cfg["ai"]["concurrency"],
        "filename": cfg["output"]["filename"],
        "pages": cfg["pages_sync"]["enabled"],
        "vault": cfg["vault_sync"]["enabled"],
        "vault_path": cfg["vault_sync"]["path"],
        "test_limit": cfg["test_limit"],
    }))
""" % (str(SCRIPTS),)


def run_load_config(env: dict) -> dict | int:
    """在干净目录（无 .env / 无 config.yml）里跑一次 load_config。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        proc = subprocess.run(
            [sys.executable, "-c", PROBE],
            cwd=tmp, env={**base, **env},
            capture_output=True, text=True, timeout=120,
        )
    out = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not out:
        raise AssertionError(f"没有输出；stderr={proc.stderr[-400:]}")
    last = out[-1]
    if last.startswith("EXIT:"):
        return int(last.split(":", 1)[1])
    return json.loads(last)


def main() -> int:
    fails = 0

    def check(label, cond, extra=""):
        nonlocal fails
        print(f"  {'ok  ' if cond else 'FAIL'} {label}{(' — ' + extra) if extra else ''}")
        if not cond:
            fails += 1

    print("1) 必填项缺失应明确失败，而不是继续跑")
    code = run_load_config({})
    check("什么都不配 → 以退出码 1 结束", code == 1, f"实际 {code!r}")

    code = run_load_config({"GH_USERNAME": "someone"})
    check("只配 GH_USERNAME → 仍以退出码 1 结束（缺 AI_API_KEY）", code == 1, f"实际 {code!r}")

    print("\n2) Variable 未配置（渲染成空字符串）时应回落到代码默认值")
    cfg = run_load_config({
        "GH_USERNAME": "someone", "AI_API_KEY": "k",
        # 这正是 GitHub Actions 里「Variable 没配」的样子：空字符串，而非不存在
        "AI_BASE_URL": "", "AI_MODEL": "", "MAX_CONCURRENCY": "",
        "OUTPUT_FILENAME": "", "VAULT_SYNC_ENABLED": "", "VAULT_REPO": "",
        "VAULT_SYNC_PATH": "", "TEST_LIMIT": "", "PAGES_SYNC_ENABLED": "",
    })
    check("返回的是配置字典", isinstance(cfg, dict), repr(cfg)[:80])
    if isinstance(cfg, dict):
        check("ai.base_url 回落默认", cfg["base_url"] == "https://api.openai.com/v1", cfg["base_url"])
        check("ai.model 回落默认", cfg["model"] == "gpt-4o-mini", repr(cfg["model"]))
        check("ai.concurrency 回落默认", cfg["concurrency"] == 5, str(cfg["concurrency"]))
        check("输出文件名回落默认", cfg["filename"] == "stars", repr(cfg["filename"]))
        check("vault 路径回落默认", cfg["vault_path"] == "GitHub-Stars/", repr(cfg["vault_path"]))
        check("vault 默认关闭", cfg["vault"] is False, str(cfg["vault"]))
        check("pages 默认关闭", cfg["pages"] is False, str(cfg["pages"]))
        check("test_limit 默认 None", cfg["test_limit"] is None, str(cfg["test_limit"]))

    print("\n3) 显式配置必须生效（不能被默认值吃掉）")
    cfg = run_load_config({
        "GH_USERNAME": "someone", "AI_API_KEY": "k",
        "AI_BASE_URL": "https://example.test/v1", "AI_MODEL": "my-model",
        "MAX_CONCURRENCY": "3", "OUTPUT_FILENAME": "mine",
        "PAGES_SYNC_ENABLED": "true", "VAULT_SYNC_ENABLED": "true",
        "VAULT_REPO": "me/vault", "VAULT_SYNC_PATH": "Notes/",
        "TEST_LIMIT": "7",
    })
    if isinstance(cfg, dict):
        check("自定义 base_url", cfg["base_url"] == "https://example.test/v1", cfg["base_url"])
        check("自定义 model", cfg["model"] == "my-model", cfg["model"])
        check("并发数为整数 3", cfg["concurrency"] == 3, repr(cfg["concurrency"]))
        check("自定义输出名", cfg["filename"] == "mine", cfg["filename"])
        check("pages 可开启", cfg["pages"] is True, str(cfg["pages"]))
        check("vault 可开启", cfg["vault"] is True, str(cfg["vault"]))
        check("test_limit 为整数 7", cfg["test_limit"] == 7, repr(cfg["test_limit"]))
    else:
        check("返回配置字典", False, repr(cfg))

    print("\n" + ("✅ 全部通过" if not fails else f"❌ {fails} 项失败"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
