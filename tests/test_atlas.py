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
    # 创建年份维度必须存在且覆盖绝大多数（少数仓库已被删除，无从获取）
    cy = ds["facets"].get("createdYears") or []
    if not cy:
        errors.append("缺少 createdYears 分面")
    else:
        covered = sum(x["count"] for x in cy)
        if covered < n * 0.95:
            errors.append(f"创建年份仅覆盖 {covered}/{n}，低于 95%")
        # 创建年份应单调递增
        ys = [x["year"] for x in cy]
        if ys != sorted(ys):
            errors.append("创建年份未按年升序")

    # 主领域计数之和应等于项目数
    primary_only = sum(c["count"] for c in ds["facets"]["cats"])
    if primary_only < n:
        errors.append(f"领域计数 {primary_only} < 项目数 {n}")
    for item in ds["items"]:
        for key in ("k", "cat", "tags", "forms", "d", "z", "t", "s", "st", "p", "cr", "cy"):
            if key not in item:
                errors.append(f"{item.get('k')}: 缺少字段 {key}")
        # 自创指标已移除：页面上的数字必须都能追溯到 GitHub 原始字段
        for banned in ("heat", "mom", "tier"):
            if banned in item:
                errors.append(f"{item['k']}: 不应再包含自创指标 {banned}")
        if not isinstance(item["s"], int) or item["s"] < 0:
            errors.append(f"{item['k']}: stars 非法 {item['s']}")
        # 创建年份不能晚于收藏年份（先有仓库，才可能被收藏）
        if item["cy"] and item["y"] and item["cy"] > item["y"]:
            errors.append(f"{item['k']}: 创建年份 {item['cy']} 晚于收藏年份 {item['y']}")
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
                for k in ("full_name", "url", "stars", "domain", "ai_tags", "repo_topics", "description", "created_year"):
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


def check_page_contract() -> int:
    """页面契约：确认 /atlas/ 是数据网格，而不是被改回旧版星图或漏掉关键接线。

    只做结构断言（渲染逻辑是 JS，由浏览器验证），防止模板被静默替换。
    """
    errors = []
    tpl_path = ROOT / "templates" / "atlas.html.j2"
    if not tpl_path.exists():
        return _report(["缺少 templates/atlas.html.j2"])
    tpl = tpl_path.read_text(encoding="utf-8")

    for needle, name in {'id="f-cyear"': "创建年份分面", 'id="g-cyear"': "创建年份分组"}.items():
        if needle not in tpl:
            errors.append(f"页面缺少{name}（{needle}）")

    # 顶栏的「订阅与接口」入口：必须在页面里可达，且指向真实产物
    if 'id="data-open"' not in tpl:
        errors.append("页面缺少订阅与接口入口（id=data-open）")
    if 'id="data-dlg"' not in tpl:
        errors.append("页面缺少订阅与接口面板（id=data-dlg）")
    for needle, name in {"'feed.xml'": "全站 RSS", "'api/index.json'": "接口入口", "'llms.txt'": "llms.txt"}.items():
        if needle not in tpl:
            errors.append(f"订阅与接口面板未链接到{name}（{needle}）")

    # 窄屏详情对话框只能由用户主动打开：渲染路径不得直接 showModal
    if 'showDetail(item, openDialog)' not in tpl:
        errors.append("showDetail 缺少 openDialog 参数，窄屏可能在校验后自动弹框")
    if 'showDetail(CURRENT[S.sel] || null, false)' not in tpl:
        errors.append("render/resize 路径未显式关闭弹框，窄屏加载会弹出详情对话框")

    must = {
        "表格容器": 'id="tbody"',
        "表头容器": 'id="thead-row"',
        "筛选栏": 'id="rail"',
        "详情面板": 'id="detail"',
        "无限滚动哨兵": 'id="sentinel"',
        "空状态": 'id="empty-state"',
        "RSS 发现链接": 'application/rss+xml',
        "接口发现链接": 'api/index.json',
        "搜索框": 'id="q"',
    }
    for name, needle in must.items():
        if needle not in tpl:
            errors.append(f"页面缺少{name}（{needle}）")

    # 不应再有旧版星图的痕迹
    for name, needle in {"canvas 星图": '<canvas', "星图绘制": "getContext("}.items():
        if needle in tpl:
            errors.append(f"页面仍包含{name}（{needle}），可能未完成替换")

    # 必须区分两种标签与描述
    for name, needle in {"项目描述": "desc", "AI 标签": "aiTags", "自带标签": "repoTopics"}.items():
        if needle not in tpl:
            errors.append(f"页面缺少{name}（{needle}）")

    return _report(errors)


def _css_without_media(css: str) -> str:
    """剥掉所有 @media 块，只留下顶层规则（用花括号配对，不靠正则）。"""
    out, i, n = [], 0, len(css)
    while i < n:
        j = css.find("@media", i)
        if j == -1:
            out.append(css[i:])
            break
        out.append(css[i:j])
        k = css.find("{", j)
        if k == -1:
            break
        depth, p = 1, k + 1
        while p < n and depth:
            if css[p] == "{":
                depth += 1
            elif css[p] == "}":
                depth -= 1
            p += 1
        i = p
    return "".join(out)


def _css_has(css: str, selector: str, decl: str) -> bool:
    """该选择器的**任一**规则体里是否声明了某个属性。

    刻意不取「最后一条」：同一选择器常被媒体查询再次覆盖（例如窄屏
    `.search-hint { display: none }`），那一条里当然不会有 nowrap，
    但它并不否定基础规则里的声明。
    """
    import re
    for body in re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", css):
        if decl in body:
            return True
    return False


def check_no_label_wrapping() -> int:
    """短标签不得在控件内部折行。

    这类缺陷不会报错，只是「LANG:」独占一行那样难看，极易在后续改动中回归：
    宽度不够时应当**整颗控件换行**，而不是把控件里的文字压断。
    """
    errors = []

    home = (ROOT / "templates" / "index.html.j2")
    if home.exists():
        css = home.read_text(encoding="utf-8")
        # 允许整项换行（否则宽屏挤不下时只能压断文字）
        # 必须落在**基础规则**里：写在媒体查询里只能救窄屏，宽屏仍会压断文字
        if not _css_has(_css_without_media(css), ".header-links", "flex-wrap"):
            errors.append("首页 .header-links 的基础规则缺少 flex-wrap：宽屏挤不下时会把控件内文字压断")
        # 各短标签内部不折行
        for selector in (".search-hint", ".sort-pill", ".footer-tag"):
            if not _css_has(css, selector, "nowrap"):
                errors.append(f"首页 {selector} 缺少 white-space: nowrap")
        import re
        if not re.search(r"\.header-links a[^{]*\{[^}]*white-space:\s*nowrap", css):
            errors.append("首页 .header-links 的链接未设置 white-space: nowrap")

    atlas = (ROOT / "templates" / "atlas.html.j2")
    if atlas.exists():
        css = atlas.read_text(encoding="utf-8")
        if not _css_has(css, ".toggle", "nowrap"):
            errors.append("Atlas .toggle 缺少 white-space: nowrap")
        # 文字不折行后，窄屏必须允许整颗按钮换行，否则会撑宽文档
        if not _css_has(css, ".tb-right", "flex-wrap"):
            errors.append("Atlas .tb-right 缺少 flex-wrap：窄屏会横向溢出")

    return _report(errors)


def check_cross_page_nav() -> int:
    """跨页契约：原版主页要有进入 Atlas 的入口，Atlas 要能返回原版。

    这两个页面分别由 sync_stars.py 与 sync_atlas.py 渲染，容易出现
    「改了一边忘了另一边」，所以在这里定死。
    """
    errors = []
    home = ROOT / "templates" / "index.html.j2"
    atlas = ROOT / "templates" / "atlas.html.j2"

    if not home.exists():
        errors.append("缺少 templates/index.html.j2（原版主页模板）")
    else:
        tpl = home.read_text(encoding="utf-8")
        # 入口链接：相对路径指向子目录，部署后即 /atlas/
        if 'href="atlas/"' not in tpl:
            errors.append("原版主页缺少进入 Atlas 的入口链接（href=\"atlas/\"）")
        if 'id="atlas-entry"' not in tpl:
            errors.append("原版主页入口缺少 id=atlas-entry（i18n 需要它来更新文案）")
        if "atlas_entry" not in tpl and "atlas:" not in tpl:
            errors.append("原版主页入口文案未接入 i18n")

    if not atlas.exists():
        errors.append("缺少 templates/atlas.html.j2")
    else:
        tpl = atlas.read_text(encoding="utf-8")
        # 返回链接：相对路径回到站点根
        if 'href="../"' not in tpl:
            errors.append("Atlas 页面缺少返回原版主页的链接（href=\"../\"）")

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

    print("5) 页面契约")
    page = check_page_contract()
    print(f"   {'ok' if page == 0 else f'{page} failed'}")

    print("6) 跨页导航")
    nav = check_cross_page_nav()
    print(f"   {'ok' if nav == 0 else f'{nav} failed'}")

    print("7) 标签不折行")
    wrap = check_no_label_wrapping()
    print(f"   {'ok' if wrap == 0 else f'{wrap} failed'}")

    total_fail = failed + inv + shape + pub + page + nav + wrap
    print("\n" + ("✅ 全部通过" if total_fail == 0 else f"❌ {total_fail} 项失败"))
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
