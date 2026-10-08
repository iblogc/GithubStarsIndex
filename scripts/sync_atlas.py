#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stars Atlas —— 把 data/stars.json 编译成一份「带分类的地图数据集」。

与 v1 (scripts/sync_stars.py) 的关系：
  * 不抓取、不调用 AI，**只读** data/stars.json；
  * 输出 dist/atlas/index.html 与 dist/atlas/atlas.json，互不干扰。

产物特点：
  * 每个仓库带：主领域 / 子类 / 次要领域 / 主题标签 / 形态标签 / 热度 / 活跃度 / 收藏年代；
  * 附带全局 facets（领域、语言、标签、形态、年份的计数），页面无需自己再统计。

用法：
    python3 scripts/sync_atlas.py                  # 读 data/stars.json，输出 dist/atlas/
    python3 scripts/sync_atlas.py --data other.json --out /tmp/atlas
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from atlas.taxonomy import (  # noqa: E402
    CANON_TAGS,
    CATS,
    CAT_BY_ID,
    CAT_PRIOR,
    FIELD_WEIGHT,
    FORM_TAGS,
    PRIMARY_MIN,
    SECONDARY_MIN,
    SECONDARY_RATIO,
    SUB_SCORE_CAP,
)

ROOT = Path(__file__).parent.parent
DEFAULT_DATA = ROOT / "data" / "stars.json"
DEFAULT_TEMPLATE_DIR = ROOT / "templates"
DEFAULT_TEMPLATE = "atlas.html.j2"
DEFAULT_OUT = ROOT / "dist" / "atlas"

CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
NON_ALNUM_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


# ════════════════════════════════════════════════════════════
# 文本归一化
# ════════════════════════════════════════════════════════════

def _expand_name(name: str) -> str:
    """`ClashXPro2` -> `ClashXPro2 clash x pro 2`，让正则命中项目名。"""
    spaced = CAMEL_RE.sub(" ", name)
    return f"{name} {spaced}"


class Fields:
    """一个仓库的全部可匹配文本，按权重分层。"""

    __slots__ = ("of", "tv")

    def __init__(self, rec: dict):
        meta = rec.get("metadata", rec)
        summary = rec.get("summary") or {}
        topics = meta.get("topics") or []
        tags = list(summary.get("tags_zh") or []) + list(summary.get("tags_en") or [])
        desc = meta.get("description") or ""
        summ = f"{summary.get('zh') or ''} {summary.get('en') or ''}"

        # topics / tags 额外做一份「连字符归一」文本，便于 `ai-agents` 命中 `ai agent`
        t_norm = " ".join(NON_ALNUM_RE.sub(" ", t).strip() for t in topics)
        g_norm = " ".join(NON_ALNUM_RE.sub(" ", t).strip() for t in tags)

        self.of = {
            "topics": f"{' '.join(topics)} {t_norm}".lower(),
            "tags": f"{' '.join(tags)} {g_norm}".lower(),
            "name": _expand_name(meta.get("name") or "").lower(),
            "desc": desc.lower(),
            "summary": summ.lower(),
        }
        # 中文分词缺失，用紧凑串 + 空格串两种形式提高命中率
        self.of["name"] = f"{self.of['name']} {NON_ALNUM_RE.sub(' ', self.of['name']).strip()}"
        for k in ("topics", "tags"):
            v = self.of[k]
            self.of[k] = f"{v} {NON_ALNUM_RE.sub(' ', v).strip()}"


# ════════════════════════════════════════════════════════════
# 打分
# ════════════════════════════════════════════════════════════


TOP_N = 3           # 每个字段最多采纳的命中数
TOP_DECAY = (1.0, 0.62, 0.42)
TAG_MIN = 2.6       # 主题标签入选门槛
FORM_MIN = 2.6      # 形态标签入选门槛


def _score_group(fields: Fields, pats) -> tuple[float, list]:
    """返回 (得分, 命中模式列表)。

    计分规则（关键是「抗堆词」）：
      * 按字段累加 —— topics/tags 是作者自打的标签，最可信；摘要正文是长文本，噪声最大；
      * 每个字段内只采纳特异性最高的前 3 条命中，并按 1 / 0.62 / 0.42 递减，
        这样「关键词表里词多」不会自动获胜，只有真正命中多个明确词才会加满分；
      * 每条模式再乘 `spec`（字面长度代理），进一步压制 `工具`/`平台` 这类泛词。
    """
    score = 0.0
    hits = []
    for fid, weight in FIELD_WEIGHT.items():
        text = fields.of.get(fid)
        if not text:
            continue
        matched = [p for p in pats if p.search(text)]
        if not matched:
            continue
        matched.sort(key=lambda p: -p.spec)
        for rank, pat in enumerate(matched[:TOP_N]):
            score += weight * pat.spec * TOP_DECAY[rank]
        hits.extend(matched)
    return score, hits


def classify(rec: dict) -> dict:
    """为单个仓库计算分类结果。

    两层证据：**子类是决定性的**（`智能体`、`爬虫`、`代理`这些词几乎不会误命中），
    领域级关键词只作辅助（`工具`、`平台`这类泛词大量出现在环境描述里）。
    因此领域得分 = 子类证据 + 领域泛词证据 × 折扣，避免一个项目因为 README 里
    提到自托管就被判成「云与运维」。
    """
    fields = Fields(rec)

    # ── 子类证据：最能代表领域归属的信号 ──────────────────
    sub_scores: dict[str, float] = {}
    sub_by_cat: dict[str, float] = {}
    for cat in CATS:
        for sub in cat.subs:
            s, _ = _score_group(fields, sub.pats)
            if s <= 0:
                continue
            capped = min(s, SUB_SCORE_CAP)
            sub_scores[sub.id] = capped
            sub_by_cat[cat.id] = sub_by_cat.get(cat.id, 0.0) + capped

    # ── 领域泛词：只作辅助，权重压到 0.3 ──────────────────
    cat_scores: dict[str, float] = {}
    for cat in CATS:
        if cat.kind == "misc" or not cat.pats:
            continue
        s, _ = _score_group(fields, cat.pats)
        broad = 0.3 * s
        evidence = sub_by_cat.get(cat.id, 0.0) + broad
        if evidence > 0:
            cat_scores[cat.id] = evidence * CAT_PRIOR.get(cat.id, 1.0)

    order = {c.id: i for i, c in enumerate(CATS)}
    ranked = sorted(cat_scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))

    if not ranked or ranked[0][1] < PRIMARY_MIN:
        primary, p_score = "misc", ranked[0][1] if ranked else 0.0
    else:
        primary, p_score = ranked[0]

    # 次要领域：达到主领域一定比例
    secondary = [
        cid
        for cid, sc in ranked[1:]
        if cid != primary and sc >= max(SECONDARY_MIN, p_score * SECONDARY_RATIO) and cid != "misc"
    ][:3]

    # 主领域下最强的子类
    subs_here = [(sid, sc) for sid, sc in sub_scores.items() if sid.startswith(primary + ".")]
    if subs_here:
        subs_here.sort(key=lambda kv: -kv[1])
        sub_best = subs_here[0][0]
    elif primary != "misc" and CAT_BY_ID[primary].subs:
        sub_best = CAT_BY_ID[primary].subs[0].id
    else:
        sub_best = None

    # ── 主题标签 ──────────────────────────────────────────
    tag_hits: list[tuple[str, float]] = []
    for tid, _zh, _en, pats in CANON_TAGS:
        if not pats:
            continue
        s, _ = _score_group(fields, pats)
        if s >= TAG_MIN:
            tag_hits.append((tid, s))
    tag_hits.sort(key=lambda kv: -kv[1])
    tags = [tid for tid, _ in tag_hits[:7]]

    # ── 形态 ──────────────────────────────────────────────
    form_hits: list[tuple[str, float]] = []
    for fid, _zh, _en, pats in FORM_TAGS:
        s, _ = _score_group(fields, pats)
        if s >= FORM_MIN:
            form_hits.append((fid, s))
    form_hits.sort(key=lambda kv: -kv[1])
    top_form = form_hits[0][1] if form_hits else 0.0
    forms = [fid for fid, s in form_hits[:3] if s >= max(FORM_MIN, top_form * 0.45)]
    if not forms:
        forms = ["form.app"]

    return {
        "cat": primary,
        "catScore": round(p_score, 2),
        "cats": [primary] + secondary,
        "sub": sub_best,
        "tags": tags,
        "forms": forms,
        "scores": {k: round(v, 1) for k, v in ranked[:5]},
    }


# ════════════════════════════════════════════════════════════
# 时间与热度
# ════════════════════════════════════════════════════════════


def _year(value: str) -> int | None:
    if not value or len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


def _full_stars(stars: int, created_hint: int | None) -> int:
    """把『刚被 Star 的年轻仓库』的星数折算成可比的成熟度。

    收藏时长远小于 1 年的项目，星数是起步值；按收藏年份外推，
    让新项目与老项目在同一把尺子上比较。
    """
    if not created_hint:
        return stars
    now_year = datetime.now(timezone.utc).year
    age = max(1, now_year - created_hint)
    if age >= 2:
        return stars
    return int(stars * 1.6)


def heat_of(stars: int, priorities: dict) -> int:
    """对数热度：10k 星 ≈ 75，100k 星 ≈ 100。"""
    import math

    boost = priorities.get("full_stars", stars) / max(1, stars)
    effective = stars * boost
    if effective <= 0:
        return 0
    v = 100 * math.log10(1 + effective) / math.log10(1 + 500_000)
    return max(0, min(100, round(v)))


def momentum_of(pushed_at: str, heat: int) -> int:
    """活跃度：最近更新越新越高，叠加自身热度。"""
    y = _year(pushed_at)
    if not y:
        return 0
    now = datetime.now(timezone.utc)
    if y == now.year:
        recency = 100
    elif y == now.year - 1:
        recency = 55
    elif y == now.year - 2:
        recency = 28
    else:
        recency = max(0, 14 - (now.year - y - 2) * 2)
    return max(0, min(100, round(recency * 0.8 + heat * 0.2)))


def tier_of(stars: int) -> int:
    if stars >= 10_000:
        return 1
    if stars >= 1000:
        return 2
    return 3


def compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        s = f"{n / 1000:.1f}k"
        return s.replace(".0k", "k")
    return str(n)


# ════════════════════════════════════════════════════════════
# 数据集构建
# ════════════════════════════════════════════════════════════


def build_item(rec: dict) -> dict:
    meta = rec.get("metadata", rec)
    summary = rec.get("summary") or {}
    stars = int(meta.get("stars") or 0)
    starred = (meta.get("starred_at") or "")[:10]
    pushed = (meta.get("pushed_at") or "")[:10]
    star_year = _year(starred)
    priority = {"full_stars": _full_stars(stars, star_year)}
    cls = classify(rec)
    heat = heat_of(stars, priority)

    return {
        "k": meta.get("full_name") or "",
        "n": meta.get("name") or "",
        "o": meta.get("owner") or "",
        "u": meta.get("url") or "",
        "h": meta.get("homepage") or "",
        "d": (meta.get("description") or "").strip(),
        "s": stars,
        "sc": compact(stars),
        "l": meta.get("language") or "N/A",
        "t": (meta.get("topics") or [])[:12],
        "p": pushed,
        "st": starred,
        "y": star_year or 0,
        "z": summary.get("zh") or "",
        "e": summary.get("en") or "",
        "cat": cls["cat"],
        "cats": cls["cats"],
        "sub": cls["sub"],
        "tags": cls["tags"],
        "forms": cls["forms"],
        "cs": cls["catScore"],
        "heat": heat,
        "mom": momentum_of(pushed, heat),
        "tier": tier_of(stars),
    }


def build_facets(items: list[dict]) -> dict:
    langs: dict[str, dict] = {}
    tags: dict[str, dict] = {}
    forms: dict[str, dict] = {}
    subs: dict[str, dict] = {}
    years: dict[int, dict] = {}
    cats: dict[str, dict] = {}

    for c in CATS:
        cats[c.id] = {"id": c.id, "code": c.code, "zh": c.zh, "en": c.en,
                      "blurbZh": c.blurb_zh, "blurbEn": c.blurb_en,
                      "count": 0, "stars": 0, "subs": []}
        for s in c.subs:
            subs[s.id] = {"id": s.id, "zh": s.zh, "en": s.en, "count": 0, "cat": c.id}
            cats[c.id]["subs"].append(subs[s.id])

    for tid, zh, en, _p in CANON_TAGS:
        tags[tid] = {"id": tid, "zh": zh, "en": en, "count": 0}
    for fid, zh, en, _p in FORM_TAGS:
        forms[fid] = {"id": fid, "zh": zh, "en": en, "count": 0}

    for it in items:
        for cid in it["cats"]:
            cats[cid]["count"] += 1
            cats[cid]["stars"] += it["s"]
        if it["sub"] and it["sub"] in subs:
            subs[it["sub"]]["count"] += 1
        for tid in it["tags"]:
            tags[tid]["count"] += 1
        for fid in it["forms"]:
            forms[fid]["count"] += 1
        langs.setdefault(it["l"], {"name": it["l"], "count": 0, "stars": 0})
        langs[it["l"]]["count"] += 1
        langs[it["l"]]["stars"] += it["s"]
        if it["y"]:
            years.setdefault(it["y"], {"year": it["y"], "count": 0, "stars": 0})
            years[it["y"]]["count"] += 1
            years[it["y"]]["stars"] += it["s"]

    def drop_zero(seq):
        return [x for x in seq if x["count"] > 0]

    for c in cats.values():
        c["subs"] = drop_zero(c["subs"])
        c["subs"].sort(key=lambda s: -s["count"])

    return {
        "cats": [cats[c.id] for c in CATS],
        "languages": sorted((v for v in langs.values() if v["count"]), key=lambda v: -v["count"]),
        "tags": sorted((v for v in tags.values() if v["count"]), key=lambda v: -v["count"]),
        "forms": sorted((v for v in forms.values() if v["count"]), key=lambda v: -v["count"]),
        "years": sorted(years.values(), key=lambda v: v["year"]),
    }


def build_dataset(raw: dict) -> dict:
    repos = raw.get("repos") or {}
    items = []
    for rec in repos.values():
        meta = rec.get("metadata") or {}
        if not meta.get("full_name"):
            continue
        items.append(build_item(rec))

    # 默认排序：按热度
    items.sort(key=lambda x: (-x["heat"], -x["s"]))
    facets = build_facets(items)

    total_stars = sum(i["s"] for i in items)
    known = [i for i in items if i["cat"] != "misc"]
    return {
        "meta": {
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "sourceUpdated": raw.get("last_updated") or "",
            "count": len(items),
            "stars": total_stars,
            "starsCompact": compact(total_stars),
            "classified": len(known),
            "classifiedPct": round(100 * len(known) / max(1, len(items)), 1),
            "langs": len(facets["languages"]),
            "vocab": len(facets["tags"]),
            "medianStars": sorted(i["s"] for i in items)[len(items) // 2] if items else 0,
        },
        "facets": facets,
        "items": items,
    }


# ════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════


def render_page(dataset: dict, template_dir: Path, template: str) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tpl = env.get_template(template)
    return tpl.render(data=dataset)


def explain(raw: dict, query: str, verbose: bool = False) -> int:
    """打印单个仓库的分类依据：各领域得分与命中的关键词。"""
    q = query.lower()
    hits = [
        rec
        for rec in (raw.get("repos") or {}).values()
        if q in (rec.get("metadata", {}).get("full_name") or "").lower()
    ]
    if not hits:
        print(f"未找到匹配 {query!r} 的仓库")
        return 1
    for rec in hits[:3]:
        meta = rec["metadata"]
        cls = classify(rec)
        print(f"\n=== {meta['full_name']}  ★{meta['stars']}  {meta.get('language')}")
        print(f"    desc: {(meta.get('description') or '')[:110]}")
        print(f"    topics: {', '.join((meta.get('topics') or [])[:10])}")
        print(f"    tags  : {', '.join((rec.get('summary') or {}).get('tags_zh') or [])[:110]}")
        print(f"  → 主领域 {cls['cat']} ({cls['catScore']})  次要 {cls['cats'][1:]}  子类 {cls['sub']}")
        print(f"    主题 {cls['tags']}")
        print(f"    形态 {cls['forms']}")
        print("    领域得分:")
        for cid, sc in sorted(cls["scores"].items(), key=lambda kv: -kv[1]):
            print(f"      {cid:<8} {sc:>6}  {CAT_BY_ID[cid].zh}")
        print("    子类得分:")
        for sid, sc in sorted(sub_scores_of(rec).items(), key=lambda kv: -kv[1])[:6]:
            print(f"      {sid:<22} {sc:>6}")
        if verbose:
            fields = Fields(rec)
            print("    命中依据:")
            for cid, sc in sorted(cls["scores"].items(), key=lambda kv: -kv[1])[:3]:
                print(f"      [{cid}] {', '.join(_why(fields, CAT_BY_ID[cid].pats))}")
    return 0


def sub_scores_of(rec: dict) -> dict:
    fields = Fields(rec)
    out = {}
    for cat in CATS:
        for sub in cat.subs:
            s, _ = _score_group(fields, sub.pats)
            if s > 0:
                out[sub.id] = round(min(s, SUB_SCORE_CAP), 1)
    return out


def _why(fields: Fields, pats) -> list[str]:
    """列出真正命中的模式及其字段来源，用于人工核对分类依据。"""
    out = []
    for fid, weight in FIELD_WEIGHT.items():
        text = fields.of.get(fid)
        if not text:
            continue
        for pat in pats:
            if pat.search(text):
                out.append(f"{pat._src}@{fid}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Stars Atlas 数据集与页面构建")
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    ap.add_argument("--template", default=DEFAULT_TEMPLATE)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--stats", action="store_true", help="只打印分类统计，不写文件")
    ap.add_argument("--explain", metavar="QUERY", help="打印某个仓库（名/owner 片段）的分类依据")
    ap.add_argument("--list-misc", action="store_true", help="列出未分类项目")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印命中的关键词依据")
    args = ap.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"❌ 找不到数据文件: {data_path}", file=sys.stderr)
        return 1

    raw = json.loads(data_path.read_text(encoding="utf-8"))

    if args.explain:
        return explain(raw, args.explain, args.verbose)

    dataset = build_dataset(raw)
    meta, facets = dataset["meta"], dataset["facets"]

    print(
        f"✅ 构建完成: {meta['count']} 个仓库 / {meta['classified']} 已分类 "
        f"({meta['classifiedPct']}%) / {meta['starsCompact']} stars"
    )

    if args.stats:
        print("\n领域分布:")
        for c in facets["cats"]:
            if c["count"]:
                bar = "█" * max(1, round(c["count"] / max(1, meta["count"]) * 60))
                print(f"  {c['code']:>3} {c['zh']:<8} {c['count']:>4}  {bar}")
        print(f"\n语言 {len(facets['languages'])} 种，标签 {len(facets['tags'])} 个")
        print("其它(misc) 项目:")
        for it in dataset["items"]:
            if it["cat"] == "misc":
                print(f"   - {it['k']}  :: {it['d'][:70]}")
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "atlas.json"
    json_path.write_text(json.dumps(dataset, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"   ↳ 数据: {json_path} ({json_path.stat().st_size / 1024:.0f} KB)")

    html = render_page(dataset, Path(args.template_dir), args.template)
    html_path = out_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"   ↳ 页面: {html_path} ({html_path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
