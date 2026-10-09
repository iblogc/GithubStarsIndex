#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stars Atlas 版式探索：用同一份真实数据渲染多种风格的 demo 页面。

这是**设计探索**用的脚本，不参与线上构建、不写入 dist/atlas/。
产物在 dist/atlas-demos/，用浏览器直接打开对比即可。

注意：「数据网格」曾在此作为 demo 探索，现已采用为 /atlas/ 的正式版式，
因此不再保留其 demo 副本 —— 避免两套实现各自演化后互相漂移。

    python3 scripts/atlas_demos.py            # 生成全部 demo
    python3 scripts/atlas_demos.py --out DIR  # 指定输出目录

每个 demo 用**完全相同的数据切片**，只有版式与视觉不同，
这样对比时看到的是设计差异，而不是内容差异。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jinja2 import Environment, FileSystemLoader  # noqa: E402

ROOT = Path(__file__).parent.parent
DEFAULT_DATA = ROOT / "dist" / "atlas" / "atlas.json"
FALLBACK_DATA = ROOT / "data" / "stars.json"
TEMPLATE_DIR = ROOT / "templates" / "demos"
DEFAULT_OUT = ROOT / "dist" / "atlas-demos"

# 领域配色：为「纸质 / 印刷」取向选的低饱和色，避免彩虹感
from atlas.taxonomy import DOMAIN_COLOR  # 与页面共用同一份配色

DEMOS = [
    ("1-catalog", "索引卡片目录", "catalog.html.j2",
     "纸质档案卡片：按领域切成有色的索引标签，米色纸面、细描边、零阴影。适合「随手翻」的浏览方式。"),
    ("2-press", "技术评论报刊", "press.html.j2",
     "瑞士式编辑排版：头条 + 多栏简讯 + 底部密集索引，纯白纸面、纯黑油墨、一个专色。信息层级最强。"),
    ("3-terminal", "终端会话", "terminal.html.j2",
     "TUI 风格：等宽字体、制表线条、语法高亮式多色标记，带命令提示与键盘操作。开发者的母语界面。"),
    ("4-chronicle", "收藏编年史", "chronicle.html.j2",
     "时间轴：以收藏时间为脊椎，按年月向下流淌，年份是巨大的数字节点。适合回顾与考古。"),
]


def clip(text: str, limit: int) -> str:
    """压成单行并截断，和站内其它地方保持一致的规则。"""
    import re as _re
    t = _re.sub(r"\s+", " ", (text or "").strip())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def build_items(dataset: dict) -> list[dict]:
    """把数据集投影成「展示就绪」的结构，让模板里不含任何数据加工逻辑。"""
    cats = {c["id"]: c for c in dataset["facets"]["cats"]}
    subs = {s["id"]: s for c in dataset["facets"]["cats"] for s in c["subs"]}
    tags = {t["id"]: t for t in dataset["facets"]["tags"]}

    out = []
    for it in dataset["items"]:
        owner, _, repo = it["k"].partition("/")
        cat = cats.get(it["cat"], {})
        out.append({
            "name": it["k"],
            "owner": owner,
            "repo": repo,
            "url": it["u"],
            "homepage": it["h"] or "",
            "desc": it["d"] or "",
            "summary": clip(it["z"] or it["e"] or it["d"] or "", 200),
            "summary_long": clip(it["z"] or it["e"] or it["d"] or "", 420),
            "stars": it["s"],
            "stars_str": it["sc"],
            "lang": it["l"],
            "domain": it["cat"],
            "domain_zh": cat.get("zh", ""),
            "domain_en": cat.get("en", ""),
            "domain_code": cat.get("code", it["cat"][:2].upper()),
            "color": DOMAIN_COLOR.get(it["cat"], "#7B7F85"),
            "sub_zh": subs.get(it["sub"] or "", {}).get("zh", ""),
            "ai_tags": [tags[t]["zh"] for t in it["tags"] if t in tags],
            "repo_topics": it["t"],
            "forms": it["forms"],
            "tier": 1 if it["s"] >= 10000 else 2 if it["s"] >= 1000 else 3,
            "starred": it["st"] or "",
            "pushed": it["p"] or "",
            "year": it["y"] or 0,
            "month": (it["st"] or "")[5:7],
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Stars Atlas 版式探索 demo")
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--template-dir", default=str(TEMPLATE_DIR))
    args = ap.parse_args()

    dp = Path(args.data)
    if not dp.exists():
        if FALLBACK_DATA.exists():
            print(f"ℹ️ 未找到 {dp}，回落到 {FALLBACK_DATA}（先跑一次 sync_atlas.py 可获得完整数据）")
            import sync_atlas as SA
            raw = json.loads(FALLBACK_DATA.read_text(encoding="utf-8"))
            dataset = SA.build_dataset(raw)
        else:
            print(f"❌ 找不到数据文件: {dp}", file=sys.stderr)
            return 1
    else:
        dataset = json.loads(dp.read_text(encoding="utf-8"))

    items = build_items(dataset)
    meta = dataset["meta"]
    facets = dataset["facets"]

    # 各 demo 的信息规模：卡片/报刊/编年史看少而精，表格与终端要密
    top = items
    board = items[:36]          # 卡片、报刊、编年史
    dense = items[:90]          # 表格、终端

    years = {}
    for it in items:
        if it["year"]:
            years.setdefault(it["year"], []).append(it)

    env = Environment(loader=FileSystemLoader(args.template_dir),
                      autoescape=False, trim_blocks=True, lstrip_blocks=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 每个 demo 共用的上下文
    base_ctx = {
        "meta": meta,
        "facets": facets,
        "DOMAIN_COLOR": DOMAIN_COLOR,
        "updated": meta.get("sourceUpdated", ""),
    }

    pages = []
    for slug, title, tpl_name, blurb in DEMOS:
        tpl = env.get_template(tpl_name)
        ctx = {
            **base_ctx,
            "board": board,
            "dense": dense,
            "all": top,
            "years": sorted(years.items(), reverse=True),
            "year_counts": {y: len(v) for y, v in years.items()},
            "title": title,
            "slug": slug,
            "blurb": blurb,
        }
        html = tpl.render(**ctx)
        (out / f"{slug}.html").write_text(html, encoding="utf-8")
        size = len(html.encode("utf-8")) / 1024
        pages.append({**ctx, "slug": slug, "title": title, "blurb": blurb, "kb": round(size)})
        print(f"  ✓ {slug}.html  {title}  ({size:.0f} KB)")

    idx = env.get_template("index.html.j2").render(**{**base_ctx, "pages": pages})
    (out / "index.html").write_text(idx, encoding="utf-8")
    print(f"  ✓ index.html  风格总览")
    print(f"\n👉 打开: {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
