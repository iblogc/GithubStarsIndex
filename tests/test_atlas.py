#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stars Atlas 分类回归测试。

fixture 里的样本全部来自真实 stars.json（`tests/fixtures/atlas_anchors.json`），
断言「主领域」是否落在期望集合内。分类规则一旦被改动而质量下滑，这里会立刻失败。

跑法：
    python3 tests/test_atlas.py            # 全部
    python3 tests/test_atlas.py -v         # 打印每个样本的判定结果
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sync_atlas as SA  # noqa: E402
from atlas.taxonomy import CATS, CAT_BY_ID, SUB_BY_ID, TAG_BY_ID  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "atlas_anchors.json"


def check_anchors(verbose: bool = False) -> tuple[int, int]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    passed = failed = 0
    for case in data["cases"]:
        repo = case["repo"]
        expect = set(case["expect"]) if isinstance(case["expect"], list) else {case["expect"]}
        got = SA.classify(case["record"])
        ok = bool(expect & ({got["cat"]} | set(got["cats"])))
        if verbose:
            flag = "ok  " if ok else "FAIL"
            subs = SUB_BY_ID[got["sub"]][1].zh if got["sub"] and got["sub"] in SUB_BY_ID else "-"
            print(f"  {flag} {repo:<46} → {got['cat']}/{subs}  (期望 {sorted(expect)})")
        if ok:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL {repo}: 得到 {got['cat']}，期望 {sorted(expect)}，次要 {got['cats'][1:]}")
    return passed, failed


def check_invariants() -> int:
    """结构不变量：分类结果必须可用、可渲染，不能出现悬空引用。"""
    errors = []
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in data["cases"]:
        got = SA.classify(case["record"])
        if got["cat"] not in CAT_BY_ID:
            errors.append(f"{case['repo']}: 未知领域 {got['cat']}")
        if got["sub"] and got["sub"] not in SUB_BY_ID:
            errors.append(f"{case['repo']}: 未知子类 {got['sub']}")
        if got["sub"] and not got["sub"].startswith(got["cat"] + "."):
            errors.append(f"{case['repo']}: 子类 {got['sub']} 不属于 {got['cat']}")
        if not got["tags"] and not got["forms"]:
            errors.append(f"{case['repo']}: 主题与形态同时为空")
        if not got["forms"]:
            errors.append(f"{case['repo']}: 形态标签为空")
        for t in got["tags"]:
            if t not in TAG_BY_ID:
                errors.append(f"{case['repo']}: 未知主题 {t}")
        if got["cat"] != "misc" and got["cat"] not in got["cats"]:
            errors.append(f"{case['repo']}: 主领域不在 cats 里")
    for e in errors:
        print(f"  FAIL {e}")
    return len(errors)


def check_dataset_shapes() -> int:
    """数据集构建：facets 计数自洽，无空字段导致页面崩溃。"""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ds = SA.build_dataset({"last_updated": "test", "repos": {c["repo"]: c["record"] for c in raw["cases"]}})
    errors = []
    n = len(ds["items"])
    for cat in ds["facets"]["cats"]:
        pass
    if ds["meta"]["count"] != n:
        errors.append("meta.count 与 items 长度不一致")
    # 主领域计数之和应等于项目数
    primary_only = sum(c["count"] for c in ds["facets"]["cats"])
    if primary_only < n:
        errors.append(f"领域计数 {primary_only} < 项目数 {n}")
    for item in ds["items"]:
        for key in ("k", "cat", "tags", "forms", "heat", "mom"):
            if key not in item:
                errors.append(f"{item.get('k')}: 缺少字段 {key}")
        if not isinstance(item["heat"], int) or not 0 <= item["heat"] <= 100:
            errors.append(f"{item['k']}: heat 越界 {item['heat']}")
        if not isinstance(item["mom"], int) or not 0 <= item["mom"] <= 100:
            errors.append(f"{item['k']}: mom 越界 {item['mom']}")
    for e in errors:
        print(f"  FAIL {e}")
    return len(errors)


def main() -> int:
    verbose = "-v" in sys.argv
    print("1) 锚点分类")
    passed, failed = check_anchors(verbose)
    print(f"   {passed} passed, {failed} failed")

    print("2) 结构不变量")
    inv = check_invariants()
    print(f"   {'ok' if inv == 0 else f'{inv} failed'}")

    print("3) 数据集形状")
    shape = check_dataset_shapes()
    print(f"   {'ok' if shape == 0 else f'{shape} failed'}")

    total_fail = failed + inv + shape
    print("\n" + ("✅ 全部通过" if total_fail == 0 else f"❌ {total_fail} 项失败"))
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
