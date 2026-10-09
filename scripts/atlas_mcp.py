#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stars Atlas MCP 服务器（零依赖，stdio）。

为什么不用官方 mcp SDK：这个文件要能在一个只有 Python 标准库的环境里直接跑起来，
不需要任何 pip 安装。MCP 的 stdio 传输就是「一行一个 JSON-RPC 消息」，
握手、tools/list、tools/call 的报文都很小，自己实现反而更好部署、更少出错。

提供的工具
----------
    list_facets          列出领域 / 子类 / 主题 / 形态 / 语言及条目数
    search_repos         按关键词与过滤条件检索仓库
    recommend_for_task   用一段自然语言描述任务，推荐合适的项目
    get_repo             取单个仓库的完整档案
    get_domain_digest    取某个领域的代表作速览

数据来源：优先本地 dist/atlas/atlas.json（或 data/stars.json），
没有则回落到从站点拉取已发布的 atlas.json。

接入示例（Claude Desktop / 任何 MCP 宿主）
------------------------------------------
    {
      "mcpServers": {
        "stars-atlas": {
          "command": "python3",
          "args": ["/绝对路径/scripts/atlas_mcp.py"]
        }
      }
    }

自检（不经过 MCP，直接调用工具函数）：
    python3 scripts/atlas_mcp.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "stars-atlas"
SERVER_VERSION = "1.0.0"

ROOT = Path(__file__).parent.parent
DEFAULT_SITE = "https://stars.iblogc.com"

# 候选数据位置，按优先级尝试
LOCAL_CANDIDATES = [
    ROOT / "dist" / "atlas" / "atlas.json",
    ROOT / "data" / "stars.json",
]

# 检索时的字段权重：标签/topics 是作者与分类器的明确结论，权重最高；
# 摘要是有用的长文本但噪声大，权重最低。
FIELD_WEIGHT = {
    "name": 3.0,
    "tags": 3.0,
    "topics": 2.6,
    "desc": 2.0,
    "summary": 1.2,
    "domain": 1.4,
}

def star_bias(stars: int) -> float:
    """把 Star 数折成 0..1，用作排序时的轻微偏好。

    这里刻意不用「热度」之类的自创指标 —— 只用 GitHub 给的原始星数，
    对数缩放后作为次要因子，避免冷门但精准的项目被高星项目挤掉。
    """
    import math
    if stars <= 0:
        return 0.0
    return min(1.0, math.log10(1 + stars) / math.log10(1 + 500_000))


# 停用词：任务描述里的常见虚词，避免它们把分数拉平
STOPWORDS = {
    "the", "a", "an", "to", "for", "with", "of", "in", "on", "and", "or", "how",
    "i", "we", "my", "me", "is", "are", "be", "it", "this", "that", "can", "want",
    "need", "using", "use", "make", "build", "get", "some", "best", "good", "tool",
    "tools", "way", "help", "please", "find", "looking",
    "的", "了", "我", "要", "想", "找", "一个", "一些", "这", "那", "和", "与",
    "怎么", "如何", "可以", "需要", "帮忙", "推荐", "哪些", "什么", "这个", "工具",
}


# ════════════════════════════════════════════════════════════
# 数据装载
# ════════════════════════════════════════════════════════════


class Corpus:
    """统一承载两种来源：Atlas 数据集（已分类）或原始 stars.json（现场分类）。"""

    def __init__(self, items: list[dict], facets: dict | None, meta: dict):
        self.items = items
        self.facets = facets or {}
        self.meta = meta
        self.cat_by = {}
        self.sub_by = {}
        self.tag_by = {}
        self.form_by = {}
        for c in self.facets.get("cats", []):
            self.cat_by[c["id"]] = c
            for s in c.get("subs", []):
                self.sub_by[s["id"]] = s
        for t in self.facets.get("tags", []):
            self.tag_by[t["id"]] = t
        for f in self.facets.get("forms", []):
            self.form_by[f["id"]] = f

        # 检索用的拼装文本
        for it in self.items:
            parts = [it.get("k", ""), it.get("d", ""), it.get("z", ""), it.get("e", "")]
            parts += it.get("t") or []
            parts += [self.tag_by.get(x, {}).get("zh", "") + " " + self.tag_by.get(x, {}).get("en", "")
                      for x in (it.get("tags") or [])]
            parts += [it.get("cat", ""), self.cat_by.get(it.get("cat", ""), {}).get("zh", ""),
                      self.cat_by.get(it.get("cat", ""), {}).get("en", "")]
            parts += [it.get("sub") or "", self.sub_by.get(it.get("sub") or "", {}).get("zh", "")]
            it["_hay"] = " ".join(p for p in parts if p).lower()

    # ── 装载入口 ──────────────────────────────────────────

    @classmethod
    def load(cls, path: Path | None = None, url: str | None = None) -> "Corpus":
        if path is None:
            for cand in LOCAL_CANDIDATES:
                if cand.exists():
                    path = cand
                    break
        if path and Path(path).exists():
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if "items" in raw and "facets" in raw:      # atlas.json
                return cls(raw["items"], raw["facets"], raw.get("meta") or {})
            return cls._from_stars_json(raw)            # stars.json
        if url:
            with urllib.request.urlopen(url, timeout=30) as r:
                raw = json.loads(r.read().decode("utf-8"))
            return cls(raw["items"], raw.get("facets") or {}, raw.get("meta") or {})
        raise FileNotFoundError(
            "找不到本地数据。请先运行 `python3 scripts/sync_atlas.py` 生成 "
            "dist/atlas/atlas.json，或用 --url 指定线上地址。")

    @classmethod
    def _from_stars_json(cls, raw: dict) -> "Corpus":
        """回落到原始 stars.json 时，现场调用分类器。"""
        sys.path.insert(0, str(ROOT / "scripts"))
        import sync_atlas as SA  # 延迟导入，避免非必要依赖

        items = [SA.build_item(r) for r in raw.get("repos", {}).values()
                 if (r.get("metadata") or {}).get("full_name")]
        items.sort(key=lambda x: (-x["s"], x["k"]))
        facets = SA.build_facets(items)
        meta = {"count": len(items), "sourceUpdated": raw.get("last_updated", ""),
                "starsCompact": SA.compact(sum(i["s"] for i in items))}
        return cls(items, {**facets, "cats": facets["cats"],
                           "tags": facets["tags"], "forms": facets["forms"]}, meta)


# ════════════════════════════════════════════════════════════
# 检索
# ════════════════════════════════════════════════════════════


def tokenize(text: str) -> list[str]:
    """拆出检索用的词元。

    中文没有分词器时，滑窗 n-gram 会产生大量互相重叠的碎片
    （「反向代理」→ 反向/向代/代理/反向代/向代理），若按碎片逐个加分，
    长摘要里偶然出现的两个词就能把无关项目推到前面。因此这里只负责
    **把查询拆成「概念」**，每个概念内部再生成 n-gram 候选，评分时
    一个概念只取其最高分，见 `concept_score`。
    """
    parts = re.split(r"[\s,，、+;/|]+", (text or "").strip().lower())
    return [p for p in (x.strip() for x in parts) if p]


def _grams(concept: str) -> list[str]:
    """一个概念内部的候选匹配片段。"""
    out = [concept]
    for w in re.findall(r"[a-z0-9][a-z0-9+.#_-]*", concept):
        if w not in STOPWORDS and len(w) > 1:
            out.append(w)
    for chunk in re.findall(r"[\u4e00-\u9fff]+", concept):
        if len(chunk) <= 3:
            out.append(chunk)
        else:
            out.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
            out.extend(chunk[i:i + 3] for i in range(len(chunk) - 2))
    return [g for g in dict.fromkeys(out) if g and g not in STOPWORDS]


def _field_weights(it: dict) -> dict[str, str]:
    """预先拼好各字段文本，供匹配使用。"""
    return {
        "name": it.get("k", "").lower(),
        "tags": " ".join((it.get("tags") or []) + (it.get("t") or [])).lower(),
        "desc": (it.get("d") or "").lower(),
        "summary": ((it.get("z") or "") + " " + (it.get("e") or "")).lower(),
        "domain": (it.get("cat", "") + " " + it.get("_hay", "")).lower(),
    }


def concept_score(it: dict, concept: str, fields: dict[str, str] | None = None) -> float:
    """一个概念在单个项目上的得分：概念内各字段取最高，不叠加。"""
    f = fields or _field_weights(it)
    best = 0.0
    for g in _grams(concept):
        for fname, weight in FIELD_WEIGHT.items():
            text = f.get(fname)
            if text and g in text:
                # 片段越接近完整概念，越可信（避免用 2 字碎片拿满分）
                ratio = min(1.0, len(g) / max(len(concept), 1))
                best = max(best, weight * (0.55 + 0.45 * ratio))
    return best


def relevance(it: dict, concepts: list[str]) -> float:
    """多概念查询：要求「覆盖度」，而不是命中次数。

    只匹配到查询里一两个词的项目，即便那些词落在长摘要中，也应该排在
    覆盖了大部分概念的项目之后。因此总分乘以覆盖比例。
    """
    if not concepts:
        return star_bias(it.get("s", 0))
    f = _field_weights(it)
    scores = [concept_score(it, c, f) for c in concepts]
    hit = [s for s in scores if s > 0]
    if not hit:
        return 0.0
    coverage = len(hit) / len(concepts)
    total = sum(hit) / len(concepts)          # 未覆盖的概念按 0 参与平均
    # Star 数只做轻微偏好，避免冷门但精准的项目被埋没
    return total * (0.35 + 0.65 * coverage) * (0.8 + 0.2 * star_bias(it.get("s", 0)))


def filter_items(c: Corpus, args: dict) -> list[dict]:
    out = c.items
    if args.get("domain"):
        d = str(args["domain"]).lower()
        out = [i for i in out if i["cat"] == d or d in i["cats"]]
    if args.get("subcategory"):
        s = args["subcategory"]
        out = [i for i in out if i.get("sub") == s]
    if args.get("tag"):
        t = args["tag"]
        out = [i for i in out if t in (i.get("tags") or [])]
    if args.get("language"):
        lang = str(args["language"]).lower()
        out = [i for i in out if (i.get("l") or "").lower() == lang]
    if args.get("form"):
        f = args["form"]
        out = [i for i in out if f in (i.get("forms") or [])]
    if args.get("min_stars") is not None:
        out = [i for i in out if i.get("s", 0) >= int(args["min_stars"])]
    if args.get("pushed_since"):
        since = str(args["pushed_since"])
        out = [i for i in out if (i.get("p") or "") >= since]
    return out


def clip(text: str, n: int) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def brief(c: Corpus, it: dict, summary_chars: int = 200) -> dict:
    return {
        "full_name": it["k"],
        "url": it["u"],
        "stars": it["s"],
        "language": it["l"],
        "domain": it["cat"],
        "domain_zh": c.cat_by.get(it["cat"], {}).get("zh"),
        "subcategory_zh": c.sub_by.get(it.get("sub") or "", {}).get("zh"),
        "ai_tags_zh": [c.tag_by[t]["zh"] for t in (it.get("tags") or []) if t in c.tag_by],
        "repo_topics": it.get("t") or [],
        "pushed_at": it.get("p") or None,
        "summary": clip(it.get("z") or it.get("e") or it.get("d") or "", summary_chars) or None,
    }


# ════════════════════════════════════════════════════════════
# 工具实现
# ════════════════════════════════════════════════════════════


def tool_list_facets(c: Corpus, args: dict) -> dict:
    kind = (args.get("kind") or "all").lower()
    f = c.facets
    out: dict = {"totals": {
        "repos": len(c.items),
        "domains": len([x for x in f.get("cats", []) if x.get("count")]),
        "tags": len([x for x in f.get("tags", []) if x.get("count")]),
        "languages": len(f.get("languages", [])),
    }}
    if kind in ("all", "domains", "domain"):
        out["domains"] = [{"id": x["id"], "zh": x["zh"], "en": x["en"], "count": x["count"],
                           "about": x.get("blurbZh")}
                          for x in f.get("cats", []) if x.get("count")]
    if kind in ("all", "subcategories", "sub"):
        out["subcategories"] = [{"id": s["id"], "zh": s["zh"], "domain": s.get("cat"), "count": s["count"]}
                                for s in c.sub_by.values() if s.get("count")]
        out["subcategories"].sort(key=lambda x: -x["count"])
    if kind in ("all", "tags", "tag"):
        out["tags"] = [{"id": x["id"], "zh": x["zh"], "en": x["en"], "count": x["count"]}
                       for x in f.get("tags", []) if x.get("count")]
    if kind in ("all", "forms", "form"):
        out["forms"] = [{"id": x["id"], "zh": x["zh"], "en": x["en"], "count": x["count"]}
                        for x in f.get("forms", []) if x.get("count")]
    if kind in ("all", "languages", "language"):
        out["languages"] = f.get("languages", [])
        out["starred_years"] = f.get("years", [])
    return out


def tool_search_repos(c: Corpus, args: dict) -> dict:
    limit = min(int(args.get("limit") or 8), 50)
    pool = filter_items(c, args)
    concepts = tokenize(args.get("query") or "")
    if concepts:
        scored = [(relevance(it, concepts), it) for it in pool]
        scored = [(s, it) for s, it in scored if s > 0]
        scored.sort(key=lambda x: (-x[0], -x[1].get("s", 0)))
        pool = [it for _, it in scored]
    else:
        pool = sorted(pool, key=lambda x: (-x.get("s", 0), x.get("k", "")))
    return {
        "matched": len(pool),
        "returned": min(limit, len(pool)),
        "repos": [brief(c, it) for it in pool[:limit]],
        "_next": "用 get_repo(full_name) 取完整档案；每个 url 可直接打开阅读源码。",
    }


def tool_recommend_for_task(c: Corpus, args: dict) -> dict:
    task = args.get("task") or args.get("query") or ""
    if not task.strip():
        return {"error": "需要提供 task：一段描述你正在做什么的自然语言。"}
    limit = min(int(args.get("limit") or 6), 25)
    pool = filter_items(c, args)

    # 任务描述按权重放大：领域/主题词命中比泛泛的描述词更有价值
    concepts = tokenize(task)
    scored = []
    for it in pool:
        s = relevance(it, concepts)
        if s > 0:
            scored.append((s, it))
    if not scored:                       # 没命中就把该范围的热门项目给出去
        fallback = sorted(pool, key=lambda x: (-x.get("s", 0), x.get("k", "")))[:limit]
        return {
            "matched": 0,
            "interpreted_query": concepts[:12],
            "note": "任务描述未与任何项目的标签/摘要匹配，以下为该范围 Star 数最高的项目。",
            "repos": [brief(c, it) for it in fallback],
        }
    scored.sort(key=lambda x: (-x[0], -x[1].get("s", 0)))
    return {
        "matched": len(scored),
        "interpreted_query": concepts[:12],
        "repos": [brief(c, it) for it in [x[1] for x in scored[:limit]]],
        "_next": "确定候选后用 get_repo 取完整摘要与技术细节；url 可直接打开源码借鉴实现。",
    }


def tool_get_repo(c: Corpus, args: dict) -> dict:
    name = str(args.get("full_name") or "").strip().lower()
    if not name:
        return {"error": "需要提供 full_name，例如 'fatedier/frp'。"}
    for it in c.items:
        if it["k"].lower() == name:
            return {
                "full_name": it["k"],
                "url": it["u"],
                "homepage": it["h"] or None,
                "description": it["d"] or None,
                "summary_zh": it["z"] or None,
                "summary_en": it["e"] or None,
                "stars": it["s"],
                "language": it["l"],
                "domain": it["cat"],
                "domain_zh": c.cat_by.get(it["cat"], {}).get("zh"),
                "subcategory": it.get("sub"),
                "subcategory_zh": c.sub_by.get(it.get("sub") or "", {}).get("zh"),
                "secondary_domains": it["cats"][1:],
                "ai_tags": [{"id": t, "zh": c.tag_by.get(t, {}).get("zh")}
                            for t in (it.get("tags") or [])],
                "repo_topics": it.get("t") or [],
                "forms": it.get("forms") or [],
                "pushed_at": it.get("p") or None,
                "starred_at": it.get("st") or None,
            }
    # 放宽为模糊匹配，方便 agent 用部分名字查询
    partial = [i for i in c.items if name in i["k"].lower()]
    partial.sort(key=lambda x: -x.get("s", 0))
    return {"error": f"未找到 {args.get('full_name')!r}",
            "did_you_mean": [i["k"] for i in partial[:5]]}


def tool_get_domain_digest(c: Corpus, args: dict) -> dict:
    limit = min(int(args.get("limit") or 12), 40)
    tag = args.get("tag")
    domain = args.get("domain")
    pool = filter_items(c, {"domain": domain, "tag": tag})
    if not pool:
        return {"error": f"没有匹配的范围 domain={domain!r} tag={tag!r}",
                "available_domains": [x["id"] for x in c.facets.get("cats", []) if x.get("count")][:20]}
    pool = sorted(pool, key=lambda x: (-x.get("s", 0), x.get("k", "")))
    label = c.cat_by.get(domain, {}).get("zh") if domain else (
        c.tag_by.get(tag, {}).get("zh") if tag else "全部")
    return {
        "scope": {"domain": domain, "tag": tag, "label_zh": label},
        "count": len(pool),
        "repos": [brief(c, it, summary_chars=240) for it in pool[:limit]],
        "_next": "把这里的高星项目当作该领域的入口；需要细节时用 get_repo。",
    }


TOOLS = [
    {
        "name": "list_facets",
        "description": ("列出 Stars Atlas 的全部分类维度与条目数：领域(domain)、子类、"
                        "主题标签(tag)、形态、语言、收藏年份。先用它确定有哪些可筛选的取值，"
                        "再调用 search_repos / recommend_for_task。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": ["all", "domains", "subcategories", "tags", "forms", "languages"],
                         "description": "要看哪一类；默认 all"},
            },
        },
    },
    {
        "name": "search_repos",
        "description": ("在已收藏的 GitHub 项目中检索：按关键词打分，并可按领域、子类、主题标签、"
                        "语言、形态、最低 Star 数、最近提交时间过滤。返回精简结果（含摘要与链接）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，空格分隔；中英文均可"},
                "domain": {"type": "string", "description": "领域 id，如 ai / web / net / sec，见 list_facets"},
                "subcategory": {"type": "string", "description": "子类 id，如 ai.agent、net.proxy"},
                "tag": {"type": "string", "description": "主题标签 id，如 ai.mcp、net.proxy"},
                "language": {"type": "string", "description": "主要语言，如 Python / TypeScript"},
                "form": {"type": "string",
                         "description": "形态 id：form.app / form.cli / form.ext / form.lib / "
                                        "form.theme / form.list / form.asset / form.doc"},
                "min_stars": {"type": "integer", "description": "最低 Star 数"},
                "pushed_since": {"type": "string", "description": "最近提交不早于该日期，YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "返回条数，默认 8，上限 50"},
            },
        },
    },
    {
        "name": "recommend_for_task",
        "description": ("用一段自然语言描述你正在做的任务，返回最契合的项目候选。"
                        "适合「我要做 X，有没有现成的项目可以参考/复用」这类场景；"
                        "结果按标签与摘要的相关度排序，并以 Star 数作轻微偏好。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "任务描述，例如：给 CLI 加一个本地向量检索的 RAG 记忆"},
                "domain": {"type": "string", "description": "可选的领域限定，缩小范围"},
                "tag": {"type": "string", "description": "可选的主题标签限定"},
                "language": {"type": "string", "description": "可选的语言限定"},
                "min_stars": {"type": "integer", "description": "可选的最低 Star 数"},
                "limit": {"type": "integer", "description": "返回条数，默认 6，上限 25"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "get_repo",
        "description": ("取单个项目的完整档案：中英文摘要、领域与子类、主题标签、形态、"
                        "AI 标签、仓库自带 topics、最近提交与收藏时间，以及 GitHub 地址。"),
        "inputSchema": {
            "type": "object",
            "properties": {"full_name": {"type": "string", "description": "owner/repo，例如 fatedier/frp"}},
            "required": ["full_name"],
        },
    },
    {
        "name": "get_domain_digest",
        "description": ("取某个领域（或某个主题标签）的代表作速览，按 Star 数排序。"
                        "适合需要快速了解某个方向都有哪些成熟项目时使用。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "领域 id，如 ai、web、net"},
                "tag": {"type": "string", "description": "主题标签 id（与 domain 二选一）"},
                "limit": {"type": "integer", "description": "返回条数，默认 12，上限 40"},
            },
        },
    },
]

HANDLERS = {
    "list_facets": tool_list_facets,
    "search_repos": tool_search_repos,
    "recommend_for_task": tool_recommend_for_task,
    "get_repo": tool_get_repo,
    "get_domain_digest": tool_get_domain_digest,
}


# ════════════════════════════════════════════════════════════
# JSON-RPC / MCP 传输
# ════════════════════════════════════════════════════════════


def log(msg: str) -> None:
    """日志只能走 stderr —— stdout 是协议通道，混入任何多余字符都会破坏会话。"""
    print(msg, file=sys.stderr, flush=True)


def call_tool(c: Corpus, name: str, args: dict) -> dict:
    fn = HANDLERS.get(name)
    if fn is None:
        return {"error": f"未知工具 {name}；可用：{', '.join(HANDLERS)}"}
    try:
        return fn(c, args or {})
    except Exception as e:  # 工具失败应作为结果返回，而不是炸掉整个会话
        return {"error": f"{type(e).__name__}: {e}"}


def handle(c: Corpus, msg: dict) -> dict | None:
    """处理一条 JSON-RPC 消息，返回响应（通知则返回 None）。"""
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        params = msg.get("params") or {}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Stars Atlas：一个按领域/主题分类的 GitHub 收藏项目库，共 %d 个项目。"
                "推荐流程：先 list_facets 看有哪些领域与标签，"
                "再用 recommend_for_task 或 search_repos 找候选，"
                "最后用 get_repo 取完整档案。每个结果都带 GitHub 地址，可直接阅读源码。"
                % len(c.items)),
        }}

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params") or {}
        result = call_tool(c, params.get("name"), params.get("arguments") or {})
        is_err = "error" in result and len(result) == 1
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(result, ensure_ascii=False, indent=1)}],
            "isError": is_err,
        }}

    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve(c: Corpus) -> int:
    """stdio 主循环：一行一条 JSON-RPC 消息。"""
    log(f"[{SERVER_NAME}] 已加载 {len(c.items)} 个项目，等待 MCP 消息…")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"忽略无法解析的消息: {e}")
            continue
        resp = handle(c, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


# ════════════════════════════════════════════════════════════
# 自检：不经过 MCP 直接调用工具
# ════════════════════════════════════════════════════════════


def selftest(c: Corpus) -> int:
    fails = 0

    def check(label, cond, extra=""):
        nonlocal fails
        print(f"  {'ok  ' if cond else 'FAIL'} {label}{(' — ' + extra) if extra else ''}")
        if not cond:
            fails += 1

    print(f"语料：{len(c.items)} 个项目")
    f = tool_list_facets(c, {"kind": "all"})
    check("list_facets 返回领域", len(f.get("domains", [])) > 5)
    check("list_facets 返回标签", len(f.get("tags", [])) > 5)

    r = tool_search_repos(c, {"query": "反向代理 内网穿透", "limit": 5})
    names = [x["full_name"] for x in r["repos"]]
    check("中文检索有结果", r["matched"] > 0, f"{r['matched']} 命中")
    check("中文检索相关", any("frp" in n or "clash" in n or "proxy" in n.lower() for n in names),
          ", ".join(names[:3]))

    r2 = tool_search_repos(c, {"query": "browser automation testing", "limit": 5})
    check("英文检索有结果", r2["matched"] > 0, ", ".join(x["full_name"] for x in r2["repos"][:3]))

    r3 = tool_search_repos(c, {"domain": "data", "language": "Python", "min_stars": 1000, "limit": 5})
    check("过滤组合生效", all(x["language"] == "Python" for x in r3["repos"]),
          ", ".join(f"{x['full_name']}({x['language']})" for x in r3["repos"][:3]))

    rec = tool_recommend_for_task(c, {"task": "我想给终端里的编码助手加一个长期记忆和 RAG 检索", "limit": 5})
    check("任务推荐有结果", len(rec.get("repos", [])) > 0, ", ".join(x["full_name"] for x in rec.get("repos", [])[:3]))

    if c.items:
        target = c.items[0]["k"]
        one = tool_get_repo(c, {"full_name": target})
        check("get_repo 精确命中", one.get("full_name") == target)
        check("get_repo 带摘要", bool(one.get("summary_zh") or one.get("summary_en")))
    missing = tool_get_repo(c, {"full_name": "definitely/not-here"})
    check("get_repo 未命中时给出建议", "error" in missing)

    dg = tool_get_domain_digest(c, {"domain": "ai", "limit": 5})
    check("领域速览有结果", len(dg.get("repos", [])) == 5)
    check("速览按 Star 降序",
          all(dg["repos"][i]["stars"] >= dg["repos"][i + 1]["stars"] for i in range(len(dg["repos"]) - 1)))

    bad = tool_get_domain_digest(c, {"domain": "nope"})
    check("无效领域给出可用值", "error" in bad and bad.get("available_domains"))

    print(f"\n{'✅ 自检全部通过' if not fails else f'❌ {fails} 项失败'}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Stars Atlas MCP 服务器（stdio，零依赖）")
    ap.add_argument("--data", help="指定数据集文件（默认自动查找 dist/atlas/atlas.json）")
    ap.add_argument("--url", default=os.environ.get("STARS_ATLAS_URL") or f"{DEFAULT_SITE}/atlas/atlas.json",
                    help="本地数据缺失时回落到该地址")
    ap.add_argument("--no-url", action="store_true", help="禁止回落到网络")
    ap.add_argument("--selftest", action="store_true", help="不启动 MCP，直接自检工具逻辑")
    args = ap.parse_args()

    try:
        corpus = Corpus.load(Path(args.data) if args.data else None,
                             None if args.no_url else args.url)
    except Exception as e:
        log(f"❌ 数据加载失败: {e}")
        return 1

    if args.selftest:
        return selftest(corpus)
    return serve(corpus)


if __name__ == "__main__":
    raise SystemExit(main())
