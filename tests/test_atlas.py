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


def check_published_artifacts() -> int:
    """发布产物：接口分片自洽、RSS 为合法 XML 且条目正确。

    这些文件是给 Agent 与订阅器消费的，格式错误不会在本站报错，
    但会让下游静默拿不到数据，因此在这里兜住。
    """
    import tempfile
    import xml.etree.ElementTree as ET
    from pathlib import Path as _P
    from atlas.publish import write_api, write_feeds

    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ds = SA.build_dataset({"last_updated": "test", "repos": {c["repo"]: c["record"] for c in raw["cases"]}})
    errors = []
    site = "https://example.test"

    with tempfile.TemporaryDirectory() as tmp:
        out = _P(tmp)
        stats = write_api(ds, out, site)
        write_feeds(ds, out, site)

        # 目录文件
        idx_path = out / "api/index.json"
        if not idx_path.exists():
            return _report(["api/index.json 未生成"])
        idx = json.loads(idx_path.read_text(encoding="utf-8"))

        for key in ("totals", "fields", "facets", "shards", "corpus", "index", "feeds"):
            if key not in idx:
                errors.append(f"index.json 缺少 {key}")

        n = len(ds["items"])
        if idx["totals"]["repos"] != n:
            errors.append(f"totals.repos={idx['totals']['repos']} 与实际 {n} 不符")

        # 分片：每条记录都能在某一领域分片中找到，且计数对得上
        seen = set()
        for sh in idx["shards"]:
            p = out / sh["path"]
            if not p.exists():
                errors.append(f"分片缺失 {sh['path']}")
                continue
            if p.stat().st_size != sh["bytes"]:
                errors.append(f"{sh['path']} 字节数声明与实际不符")
            blob = json.loads(p.read_text(encoding="utf-8"))
            if len(blob["repos"]) != sh["count"]:
                errors.append(f"{sh['path']} count 与实际条数不符")
            if blob["count"] != sh["count"]:
                errors.append(f"{sh['path']} 内层 count 与清单不符")
            if sh["scope"] == "domain":
                seen.update(r["full_name"] for r in blob["repos"])

        missing = {i["k"] for i in ds["items"]} - seen
        if missing:
            errors.append(f"{len(missing)} 个仓库未出现在任何领域分片中，例如 {list(missing)[:3]}")

        # 每条记录必备字段
        for sh in idx["shards"]:
            blob = json.loads((out / sh["path"]).read_text(encoding="utf-8"))
            for r in blob["repos"][:3]:
                for k in ("full_name", "url", "stars", "domain", "tags", "heat"):
                    if k not in r:
                        errors.append(f"{sh['path']} 的记录缺少字段 {k}")
                        break

        # JSONL 行数
        jsonl = (out / "api/repos.jsonl").read_text(encoding="utf-8").strip().splitlines()
        if len(jsonl) != n:
            errors.append(f"repos.jsonl 行数 {len(jsonl)} != {n}")
        idxl = (out / "api/index.jsonl").read_text(encoding="utf-8").strip().splitlines()
        if len(idxl) != n:
            errors.append(f"index.jsonl 行数 {len(idxl)} != {n}")
        if idx["index"]["bytes"] >= idx["corpus"]["bytes"]:
            errors.append("轻量索引并不比全量语料小，失去了存在意义")

        # RSS：XML 合法 + 必填元素
        for rel in ["feed.xml"] + [f["url"].split("/")[-1] for f in idx["feeds"]["by_domain"]]:
            p = out / rel
            if not p.exists():
                errors.append(f"订阅源缺失 {rel}")
                continue
            try:
                root = ET.parse(p).getroot()
            except ET.ParseError as e:
                errors.append(f"{rel} 不是合法 XML: {e}")
                continue
            if root.tag != "rss":
                errors.append(f"{rel} 根节点不是 rss")
                continue
            ch = root.find("channel")
            for tag in ("title", "link", "description", "lastBuildDate"):
                if ch.find(tag) is None:
                    errors.append(f"{rel} channel 缺少 {tag}")
            for it in ch.findall("item"):
                for tag in ("title", "link", "guid", "pubDate"):
                    if not (it.findtext(tag) or "").strip():
                        errors.append(f"{rel} 有 item 缺少 {tag}")
                        break

    return _report(errors)


def _report(errors) -> int:
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

    print("4) 接口与订阅产物")
    pub = check_published_artifacts()
    print(f"   {'ok' if pub == 0 else f'{pub} failed'}")

    total_fail = failed + inv + shape + pub
    print("\n" + ("✅ 全部通过" if total_fail == 0 else f"❌ {total_fail} 项失败"))
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
