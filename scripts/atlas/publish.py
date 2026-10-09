# -*- coding: utf-8 -*-
"""
Stars Atlas 的对外接口层：给 Agent 调用的静态 API，以及 RSS 订阅源。

设计前提
--------
本站是**纯静态**站点，没有服务端可供查询。因此「API」的形态是
**预先切好的分片文件**：Agent 先取一份体积很小的目录（index.json），
看清楚有哪些领域、每个分片多大，再按需拉取自己关心的那一两片。

为什么不直接给一个大 JSON：Agent 的上下文窗口是稀缺资源。1.4 MB 的全量
数据塞进上下文既昂贵又无用；按领域切成 16 片后，单片通常只有几十 KB。

产物（发布到 <站点>/atlas/）
--------------------------
    api/index.json         调用入口：元信息 + 全部 facet + 分片清单(含字节数)
    api/repos.jsonl        全量语料，一行一个仓库，便于 grep / 流式读取
    api/c/<领域>.json       16 个领域分片
    api/t/<主题>.json       主题分片（条目数 >= TAG_FEED_MIN 才生成）
    api/README.md          给人和 Agent 看的用法说明
    llms.txt               站点对外自述（llms.txt 约定）
    feed.xml               全站 RSS 2.0
    feed-<领域>.xml         各领域 RSS 2.0

注意：API 里的字段用**可读的长键名**（full_name / summary_zh），而不是页面
内部的短键。数据是给模型读的，可读性比那点体积更重要。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

# 只有条目数达到该值的主题才单独出分片 / 订阅源，避免生成上百个空文件
TAG_SHARD_MIN = 3

# RSS 每条最多带多少字摘要，避免订阅源过大
FEED_SUMMARY_CHARS = 320
FEED_MAX_ITEMS = 60

# 站点根地址：写进 RSS 的 <link> / <guid>，必须绝对地址
DEFAULT_SITE = "https://stars.iblogc.com"


# ════════════════════════════════════════════════════════════
# 条目投影：把页面用的短键展开成可读键
# ════════════════════════════════════════════════════════════


def api_item(it: dict, cats: dict, subs: dict, tags: dict, forms: dict) -> dict:
    """把内部紧凑条目转成对外可读结构。"""
    cat = cats.get(it["cat"], {})
    sub = subs.get(it["sub"] or "", {})
    return {
        "full_name": it["k"],
        "url": it["u"],
        "homepage": it["h"] or None,
        "description": it["d"] or None,
        "summary_zh": it["z"] or None,
        "summary_en": it["e"] or None,
        "stars": it["s"],
        "language": it["l"],
        "domain": cat.get("id"),
        "domain_zh": cat.get("zh"),
        "subcategory": sub.get("id"),
        "subcategory_zh": sub.get("zh"),
        "secondary_domains": [c for c in it["cats"][1:]],
        # 两种标签来源不同，字段名必须能区分
        "ai_tags": [{"id": t, "zh": tags.get(t, {}).get("zh"), "en": tags.get(t, {}).get("en")}
                    for t in it["tags"]],
        "repo_topics": it["t"],
        "forms": [{"id": f, "zh": forms.get(f, {}).get("zh"), "en": forms.get(f, {}).get("en")}
                  for f in it["forms"]],
        "pushed_at": it["p"] or None,
        "starred_at": it["st"] or None,
    }


def build_scope(dataset: dict, items: list[dict]) -> dict:
    """一个分片的内容：作用域说明 + 条目。"""
    cats = {c["id"]: c for c in dataset["facets"]["cats"]}
    subs = {s["id"]: s for c in dataset["facets"]["cats"] for s in c["subs"]}
    tags = {t["id"]: t for t in dataset["facets"]["tags"]}
    forms = {f["id"]: f for f in dataset["facets"]["forms"]}
    return [api_item(it, cats, subs, tags, forms) for it in items]


# ════════════════════════════════════════════════════════════
# API 分片
# ════════════════════════════════════════════════════════════


def _dump(path: Path, payload) -> int:
    """写入 JSON，返回字节数。对外数据一律不转义非 ASCII，便于模型直接读中文。"""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def _dump_jsonl(path: Path, rows: list[dict]) -> int:
    lines = ["\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)]
    text = lines[0] + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def write_api(dataset: dict, out_dir: Path, site: str) -> dict:
    """生成 Agent 可调用的静态 API。返回统计，供构建日志与 index 使用。"""
    api = out_dir / "api"
    items = dataset["items"]
    meta = dataset["meta"]
    facets = dataset["facets"]

    cats = facets["cats"]
    cat_by = {c["id"]: c for c in cats}
    subs = {s["id"]: s for c in cats for s in c["subs"]}
    tags = {t["id"]: t for t in facets["tags"]}
    forms = {f["id"]: f for f in facets["forms"]}

    # ── 领域分片 ──────────────────────────────────────────
    shards = []
    for c in cats:
        if not c["count"]:
            continue
        rows = [i for i in items if i["cat"] == c["id"]]
        rel = f"api/c/{c['id']}.json"
        size = _dump(out_dir / rel, {
            "scope": "domain",
            "id": c["id"],
            "name_zh": c["zh"],
            "name_en": c["en"],
            "about_zh": c["blurbZh"],
            "count": len(rows),
            "repos": build_scope(dataset, rows),
        })
        shards.append({"path": rel, "scope": "domain", "id": c["id"],
                       "name_zh": c["zh"], "name_en": c["en"],
                       "count": len(rows), "bytes": size,
                       "url": f"{site}/atlas/{rel}"})

    # ── 主题分片（只出够大的） ────────────────────────────
    for t in facets["tags"]:
        if t["count"] < TAG_SHARD_MIN:
            continue
        rows = [i for i in items if t["id"] in i["tags"]]
        rel = f"api/t/{t['id']}.json"
        size = _dump(out_dir / rel, {
            "scope": "tag",
            "id": t["id"],
            "name_zh": t["zh"],
            "name_en": t["en"],
            "count": len(rows),
            "repos": build_scope(dataset, rows),
        })
        shards.append({"path": rel, "scope": "tag", "id": t["id"],
                       "name_zh": t["zh"], "name_en": t["en"],
                       "count": len(rows), "bytes": size,
                       "url": f"{site}/atlas/{rel}"})

    # ── 全量语料（JSONL，便于 grep / 流式处理） ───────────
    all_rows = build_scope(dataset, items)
    jsonl_rel = "api/repos.jsonl"
    jsonl_bytes = _dump_jsonl(out_dir / jsonl_rel, all_rows)

    # ── 轻量索引（只保留判断相关性所需的最小字段） ────────
    # 用途：Agent 想「先扫一遍全部候选、再决定深挖谁」时，不该为了这点事
    # 去解析 1.2 MB 的全量语料。这里每行只有一个仓库的精简画像。
    idx_rel = "api/index.jsonl"
    idx_rows = [{
        "full_name": i["k"], "url": i["u"], "description": _plain(i["d"] or i["z"], 160) or None,
        "stars": i["s"], "language": i["l"], "domain": i["cat"], "subcategory": i["sub"],
        "ai_tags": i["tags"], "forms": i["forms"],
        "pushed_at": i["p"] or None, "starred_at": i["st"] or None,
    } for i in items]
    idx_bytes = _dump_jsonl(out_dir / idx_rel, idx_rows)

    # ── 目录：调用入口 ────────────────────────────────────
    catalog = {
        "_about": "Stars Atlas 静态数据集：一份按领域/主题预先切好分片的 GitHub Star 语料，供人或 Agent 按需取用。",
        "_how_to_use": [
            "1. 先读本文件。facets 给出了所有分类与计数，可据此判断哪个分片与你的任务相关。",
            "2. 只想先粗筛全部候选时，用 api/index.jsonl（每行一个仓库的精简画像，体积约为全量语料的 1/3）。",
            "3. 已经知道要哪一类，直接拉分片：shards[].path 相对站点 /atlas/ 目录，shards[].bytes 可预估上下文开销。",
            "4. 分片内每条数据的字段含义见 fields 字段。",
            "5. 需要完整摘要与 topics 时取 api/repos.jsonl（一行一条，可用 grep 过滤），请先确认体积可接受。",
            "6. 每个仓库都带 url，可直接打开其 GitHub 页面阅读源码与 README。",
        ],
        "generated_at": meta["generatedAt"],
        "source_updated": meta["sourceUpdated"],
        "site": site,
        "totals": {
            "repos": meta["count"],
            "stars": meta["stars"],
            "classified": meta["classified"],
            "classified_pct": meta["classifiedPct"],
            "domains": len([c for c in cats if c["count"]]),
            "tags": len([t for t in facets["tags"] if t["count"]]),
            "subcategories": len([s for s in subs.values() if s["count"]]),
            "languages": len(facets["languages"]),
        },
        "fields": {
            "full_name": "owner/repo",
            "url": "GitHub 地址",
            "homepage": "项目主页，可能为 null",
            "description": "仓库自带的项目描述（作者撰写，GitHub 上的原文）",
            "summary_zh": "AI 生成的中文摘要（可能为 null）",
            "summary_en": "AI 生成的英文摘要（可能为 null）",
            "stars": "Star 数（GitHub 原始值）",
            "language": "主要语言",
            "domain": "主领域 id，取值见 facets.domains[].id",
            "domain_zh": "主领域中文名",
            "subcategory": "子类 id",
            "subcategory_zh": "子类中文名",
            "secondary_domains": "次要领域 id 列表（一个项目可跨领域）",
            "ai_tags": "AI 生成的标签列表 [{id,zh,en}]",
            "repo_topics": "仓库自带的 GitHub topics（作者填写，非 AI 生成）",
            "forms": "形态列表：app/cli/ext/lib/theme/list/asset/doc",
            "pushed_at": "最近提交日期（GitHub 原始值）",
            "starred_at": "被收藏的日期（GitHub 原始值，RSS 以此为发布时间的依据）",
        },
        "facets": {
            "domains": [{"id": c["id"], "code": c["code"], "zh": c["zh"], "en": c["en"],
                         "count": c["count"], "about_zh": c["blurbZh"]} for c in cats if c["count"]],
            "subcategories": [{"id": s["id"], "zh": s["zh"], "en": s["en"], "domain": s["cat"], "count": s["count"]}
                              for s in sorted(subs.values(), key=lambda x: -x["count"]) if s["count"]],
            "tags": [{"id": t["id"], "zh": t["zh"], "en": t["en"], "count": t["count"]}
                     for t in facets["tags"] if t["count"]],
            "forms": [{"id": f["id"], "zh": f["zh"], "en": f["en"], "count": f["count"]}
                      for f in facets["forms"] if f["count"]],
            "languages": facets["languages"],
            "starred_years": facets["years"],
        },
        "shards": shards,
        "corpus": {"path": jsonl_rel, "rows": len(all_rows), "bytes": jsonl_bytes,
                   "url": f"{site}/atlas/{jsonl_rel}",
                   "note": "全量字段，体积较大；先看 index.jsonl 再决定是否取这一份。"},
        "index": {"path": idx_rel, "rows": len(idx_rows), "bytes": idx_bytes,
                  "url": f"{site}/atlas/{idx_rel}",
                  "note": "轻量索引：每个仓库仅保留判断相关性所需字段，适合先整体扫一遍。"},
        "feeds": {
            "all": f"{site}/atlas/feed.xml",
            "by_domain": [{"id": c["id"], "zh": c["zh"], "url": f"{site}/atlas/feed-{c['id']}.xml"}
                          for c in cats if c["count"]],
        },
    }
    idx_size = _dump(out_dir / "api/index.json", catalog)

    (out_dir / "api/README.md").write_text(
        render_api_readme(catalog), encoding="utf-8")
    (out_dir / "llms.txt").write_text(render_llms_txt(catalog), encoding="utf-8")

    return {"index_bytes": idx_size, "jsonl_bytes": jsonl_bytes,
            "indexl_bytes": idx_bytes, "shards": len(shards), "rows": len(all_rows),
            "index_url": f"{site}/atlas/api/index.json"}


# ════════════════════════════════════════════════════════════
# 文档：api/README.md 与 llms.txt
# ════════════════════════════════════════════════════════════


def _kb(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n >= 1024 else f"{n} B"


def render_api_readme(cat: dict) -> str:
    tot = cat["totals"]
    domains = "\n".join(
        f"| `{d['id']}` | {d['zh']} | {d['count']} |" for d in cat["facets"]["domains"])
    return f"""# Stars Atlas 数据接口

面向 Agent 与人查阅的静态数据集。**没有服务端**：所有内容都是预先切好的文件，
用普通 GET 即可获取。

- 数据更新：{cat['source_updated']}
- 规模：{tot['repos']} 个仓库 / {tot['stars']:,} stars / {tot['domains']} 个领域 / {tot['tags']} 个主题
- 入口：`{cat['site']}/atlas/api/index.json`

## 建议的调用顺序

1. 取 `api/index.json`（几十 KB）——里面列出全部领域、子类、主题、形态及
   各自条目数，还有分片清单与每个分片的字节数。
2. 判断哪个领域/主题与当前任务相关，只拉取对应的那一两片，例如
   `api/c/ai.json`、`api/t/net.proxy.json`。
3. 想先整体粗筛一遍：用 `api/index.jsonl`（每行一个精简画像），比全量语料小得多。
4. 需要完整摘要与 topics：取 `api/repos.jsonl`，可 `grep`。

若你的宿主支持 MCP，可改用 `scripts/atlas_mcp.py`，直接以工具形式调用
（search / recommend / get_repo / list_facets），无需自己拼接 HTTP 请求。

## 为什么切片

全量数据一次性塞进上下文既贵又没必要。按领域切开后，单片通常只有几十 KB，
足以覆盖一类任务；`shards[].bytes` 让你在拉取前就知道要花多少上下文。

## 分片清单

| 领域 id | 名称 | 条目数 |
| :--- | :--- | ---: |
{domains}

完整清单（含主题分片与字节数）见 `api/index.json` 的 `shards` 字段。

## 字段

每个仓库对象的字段含义，见 `api/index.json` 的 `fields` 字段（含中文说明）。

## 订阅

- 全站：`{cat['feeds']['all']}`
- 分领域：`feed-<领域 id>.xml`，例如 `feed-ai.xml`

## 许可与出处

数据来自 GitHub 公开仓库，摘要由 AI 生成、分类由规则引擎生成，仅供参考；
每条记录都带 `url`，请以原仓库为准。
"""


def render_llms_txt(cat: dict) -> str:
    tot = cat["totals"]
    return f"""# Stars Atlas

> 一个开发者 Star 过的 GitHub 项目库，按 {tot['domains']} 个领域 / {tot['tags']} 个主题
> 分类整理成可直接查阅的数据集，供人或 Agent 检索、推荐与借鉴。

站点：{cat['site']}/atlas/
数据更新：{cat['source_updated']}
规模：{tot['repos']} 个仓库 / {tot['stars']:,} stars

## 给 Agent 的接口

- [调用入口 {cat['site']}/atlas/api/index.json]({cat['site']}/atlas/api/index.json)：
  元信息、全部分类与计数、分片清单（含字节数）。**从这里开始。**
- [用法说明 {cat['site']}/atlas/api/README.md]({cat['site']}/atlas/api/README.md)
- [轻量索引 {cat['site']}/atlas/api/index.jsonl]({cat['site']}/atlas/api/index.jsonl)：
  每行一个仓库的精简画像，适合先粗筛。
- [全量语料 {cat['site']}/atlas/api/repos.jsonl]({cat['site']}/atlas/api/repos.jsonl)：
  一行一个仓库（含完整摘要与 topics），便于 grep / 流式读取。

若宿主支持 MCP，可直接挂载 `scripts/atlas_mcp.py` 以获得 search / recommend /
get_repo / list_facets 四个工具。

分片位于 `{cat['site']}/atlas/api/c/<领域>.json` 与
`{cat['site']}/atlas/api/t/<主题>.json`。

## 订阅

- [全部更新 RSS]({cat['feeds']['all']})
- 分领域 RSS：`{cat['site']}/atlas/feed-<领域 id>.xml`

## 每一条记录包含

仓库名、地址、主页、描述、中英文 AI 摘要、Star 数、语言、所属领域与子类、
AI 标签与仓库自带 topics、形态、最近提交与收藏日期。
每个仓库都带 GitHub 地址，可直接打开阅读源码与文档。
"""


# ════════════════════════════════════════════════════════════
# RSS 2.0
# ════════════════════════════════════════════════════════════


def _rfc822(date_str: str) -> str:
    """把 `2026-01-02` 或 ISO 时间串转成 RFC 822（RSS 要求）。"""
    raw = (date_str or "").strip()
    if not raw:
        return format_datetime(datetime.now(timezone.utc))
    try:
        if len(raw) == 10:
            dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        dt = datetime.now(timezone.utc)
    return format_datetime(dt)


def _plain(text: str, limit: int) -> str:
    """压成单行纯文本，供 RSS description 使用。"""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t


def _feed_xml(site: str, feed_path: str, title: str, desc: str, items: list[dict],
              cat_by: dict, sub_by: dict, tag_by: dict, lang: str = "zh-cn") -> str:
    rss = ET.Element("rss", {"version": "2.0",
                             "xmlns:atom": "http://www.w3.org/2005/Atom"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = title
    ET.SubElement(ch, "link").text = f"{site}/atlas/"
    ET.SubElement(ch, "description").text = desc
    ET.SubElement(ch, "language").text = lang
    ET.SubElement(ch, "lastBuildDate").text = _rfc822(items[0]["st"] if items else "")
    ET.SubElement(ch, "generator").text = "Stars Atlas (scripts/sync_atlas.py)"
    ET.SubElement(ch, "atom:link", {
        "href": f"{site}/atlas/{feed_path}", "rel": "self", "type": "application/rss+xml"})

    for it in items[:FEED_MAX_ITEMS]:
        e = ET.SubElement(ch, "item")
        ET.SubElement(e, "title").text = it["k"]
        ET.SubElement(e, "link").text = it["u"]
        guid = ET.SubElement(e, "guid", {"isPermaLink": "false"})
        guid.text = f"stars-atlas:{it['k']}"
        ET.SubElement(e, "pubDate").text = _rfc822(it["st"])
        cats = [cat_by.get(it["cat"], {}).get("zh", ""),
                sub_by.get(it["sub"] or "", {}).get("zh", "")]
        for c in [x for x in cats if x]:
            ET.SubElement(e, "category").text = c
        for t in it["tags"][:4]:
            name = tag_by.get(t, {}).get("zh")
            if name:
                ET.SubElement(e, "category").text = name

        body = _plain(it["z"] or it["e"] or it["d"], FEED_SUMMARY_CHARS)
        meta_line = f"★ {it['sc']} · {it['l']}" + (f" · {'/'.join([x for x in cats if x])}" if any(cats) else "")
        ET.SubElement(e, "description").text = f"{body}\n\n{meta_line}"
        if it["h"]:
            ET.SubElement(e, "comments").text = it["h"]
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode")


def write_feeds(dataset: dict, out_dir: Path, site: str) -> dict:
    items = dataset["items"]
    facets = dataset["facets"]
    cats = facets["cats"]
    cat_by = {c["id"]: c for c in cats}
    sub_by = {s["id"]: s for c in cats for s in c["subs"]}
    tag_by = {t["id"]: t for t in facets["tags"]}

    def by_starred(rows):
        return sorted(rows, key=lambda x: x["st"] or "", reverse=True)

    written = []

    all_rows = by_starred(items)   # _feed_xml 内部按 FEED_MAX_ITEMS 截断
    (out_dir / "feed.xml").write_text(_feed_xml(
        site, "feed.xml", "Stars Atlas · 全部新收藏",
        f"开发者在 GitHub 上收藏的项目，共 {dataset['meta']['count']} 个，按收藏时间倒序。",
        all_rows, cat_by, sub_by, tag_by), encoding="utf-8")
    written.append(("feed.xml", len(all_rows)))

    for c in cats:
        if not c["count"]:
            continue
        rows = by_starred([i for i in items if i["cat"] == c["id"]])
        name = f"feed-{c['id']}.xml"
        (out_dir / name).write_text(_feed_xml(
            site, name, f"Stars Atlas · {c['zh']}",
            f"{c['zh']}（{c['en']}）：{c['blurbZh']}", rows, cat_by, sub_by, tag_by),
            encoding="utf-8")
        written.append((name, len(rows)))

    return {"feeds": len(written), "files": [w[0] for w in written]}
