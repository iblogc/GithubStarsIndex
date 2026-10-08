# -*- coding: utf-8 -*-
"""
Stars Atlas 分类体系与评分引擎。

设计目标
--------
1. **确定性**：同一份 stars.json 永远得到同一份分类结果，不依赖 AI，不需要额外密钥。
2. **可解释**：每个项目给出的分类都来自命中的关键词（`matched`），低置信度的项目会被
   降级到 `cat.misc`，而不是硬塞进某个大类。
3. **可维护**：想调整分类，只改本文件的 PATTERNS / CANON_TAGS 即可，其余代码不动。

三层结构
--------
- **领域 (domain)**：项目做什么（AI、Web、网络、安全……）
- **形态 (form)**：项目是什么（应用、命令行、插件、库、主题、合集、素材）
- **主题 (topic，即 canon tag)**：细颗粒关键词，用于交叉筛选（约 130 个）

一个项目会得到一个主领域 + 一个主形态 + 若干主题标签；主领域下再细分到子类 (sub)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ════════════════════════════════════════════════════════════
# 字段权重：命中在 topics / tags 上比命中在摘要正文里更可信
# ════════════════════════════════════════════════════════════

FIELD_WEIGHT = {
    "topics": 2.6,
    "tags": 2.6,
    "name": 2.2,
    "desc": 1.7,
    "summary": 1.0,
}

# 单个子类得分上限：防止“关键词写得多的子类”通吃
SUB_SCORE_CAP = 9.0
# 副领域门槛：得分需达到主领域的该比例才被计入次分类
SECONDARY_RATIO = 0.45
SECONDARY_MIN = 2.2
# 低于该分直接归入「其它」，避免噪声分类
PRIMARY_MIN = 2.4


class _Pat:
    """惰性编译的正则。

    `spec` 是模式的“特异性”权重，用来压制 `工具`/`平台` 这类泛词 —— 它们几乎出现在
    每条 README 里，单独命中不足以判定领域：

      * 拉丁字母/数字按 1 个字符计，中文按 2 个字符计（中文单字信息量≈英文 2~3 字）；
      * 单个中日韩字符额外按 0.55 折算 —— `主题` 会命中「主题色」，`网络` 会命中
        「网络分析」，而 `screenshot`、`字体` 这类完整词才是真信号。
    """

    __slots__ = ("_src", "_rx", "pid", "spec")

    _seq = 0
    _CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
    _WORDCHARS = re.compile(r"[A-Za-z0-9\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+")

    def __init__(self, src: str):
        self._src = src
        self._rx = re.compile(src)
        _Pat._seq += 1
        self.pid = _Pat._seq

        # 只数「词」的部分，忽略 \b ^ $ 等元字符
        lit = sum(2 if self._CJK.match(w) else len(w) for w in _Pat._WORDCHARS.findall(src))
        self.spec = max(0.4, min(1.7, 0.45 + lit / 12.0))

    def search(self, text: str) -> bool:
        return self._rx.search(text) is not None

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Pat {self._src!r}>"


def P(*srcs: str) -> tuple[_Pat, ...]:
    return tuple(_Pat(s) for s in srcs)


# ════════════════════════════════════════════════════════════
# 主题标签（canon tag）：约 130 个，按顺序匹配，同一个原始标签只归一个主题
# ════════════════════════════════════════════════════════════

CANON_TAGS: list[tuple[str, str, str, tuple[_Pat, ...]]] = [
    # ── AI ──────────────────────────────────────────────────
    ("ai.agent", "AI 智能体", "AI agents", P(r"ai[\s\-_]?agents?\b", r"\bagents?\b", r"智能体", r"multi[\s\-]?agent", r"subagent", r"智能代理")),
    ("ai.cli", "AI 编程助手", "AI coding agents", P(r"claude[\s\-]?code", r"\bcodex\b", r"coding[\s\-]?agents?", r"ai[\s\-]?coding", r"gemini[\s\-]?cli", r"opencode", r"cursor", r"windsurf", r"vibe[\s\-]?coding", r"vibecoding", r"编程助手", r"编码助手", r"copilot")),
    ("ai.mcp", "MCP 协议", "MCP", P(r"\bmcp\b", r"model[\s\-]?context[\s\-]?protocol", r"mcp[\s\-]?server", r"mcp[\s\-]?client")),
    ("ai.skill", "技能与提示词", "Skills & prompts", P(r"agent[\s\-]?skills?", r"claude[\s\-]?skills?", r"提示工程", r"prompt[\s\-]?engineering", r"\bprompts?\b", r"系统提示")),
    ("ai.llm", "大模型", "LLM", P(r"大模型", r"\bllms?\b", r"large[\s\-]?language", r"language[\s\-]?models?", r"foundation[\s\-]?model")),
    ("ai.rag", "RAG 与知识库", "RAG & retrieval", P(r"\brag\b", r"retrieval[\s\-]?augmented", r"检索增强", r"知识库", r"knowledge[\s\-]?base", r"语义搜索", r"semantic[\s\-]?search", r"vector[\s\-]?(search|database)", r"embeddings?", r"向量数据库", r"知识图谱")),
    ("ai.model", "模型与推理", "Models & inference", P(r"ollama", r"llama\.?cpp", r"\bvllm\b", r"\bsglang\b", r"推理框架", r"本地模型", r"local[\s\-]?ai", r"量化", r"quantization", r"微调", r"fine[\s\-]?tun", r"\blora\b", r"模型加载", r"推理引擎", r"模型路由", r"\bgguf\b")),
    ("ai.vendor", "模型厂商", "Model vendors", P(r"openai", r"anthropic", r"\bgpt[-\s]?\d", r"chatgpt", r"claude\b", r"gemini", r"deepseek", r"qwen", r"通义", r"kimi", r"glm", r"chatglm", r"\bgrok\b", r"minimax", r"文心", r"豆包")),
    ("ai.gateway", "模型网关", "AI gateway", P(r"ai[\s\-]?gateway", r"\bone[\s\-]?api\b", r"new[\s\-]?api", r"接口聚合", r"模型路由", r"\bbyok\b", r"中转", r"\bapi[\s\-]?proxy\b", r"负载均衡")),
    ("ai.vision", "图像与视觉", "Vision & image", P(r"stable[\s\-]?diffusion", r"diffusion", r"文生图", r"text[\s\-]?to[\s\-]?image", r"图像生成", r"image[\s\-]?generation", r"计算机视觉", r"computer[\s\-]?vision", r"opencv", r"\bmidjourney\b", r"图像识别", r"\bcomfyui\b", r"图像处理", r"抠图", r"超分")),
    ("ai.speech", "语音与音频", "Speech & audio", P(r"语音识别", r"speech[\s\-]?to[\s\-]?text", r"\basr\b", r"whisper", r"\btts\b", r"text[\s\-]?to[\s\-]?speech", r"语音合成", r"voice[\s\-]?clon", r"声音克隆", r"音频处理", r"funasr", r"唱歌")),
    ("ai.ocr", "OCR 与文档智能", "OCR & document AI", P(r"\bocr\b", r"文字识别", r"文档解析", r"document[\s\-]?(parsing|understanding)", r"pdf[\s\-]?pars", r"版面分析", r"表格识别")),
    ("ai.mm", "多模态", "Multimodal", P(r"多模态", r"multi[\s\-]?modal", r"图文理解", r"视觉语言", r"\bvlm\b")),
    ("ai.app", "AI 应用", "AI apps", P(r"chatbot", r"聊天机器人", r"ai[\s\-]?(assistant|app|tools?|search|writing|note)", r"智能助手", r"个人助手", r"\bchats?\b", r"对话系统", r"数字员工", r"\baigc\b", r"生成式")),
    ("ai.eval", "评测与可观测", "Eval & observability", P(r"\beval(uation)?\b", r"评测", r"benchmark", r"llm[\s\-]?observ", r"trace", r"提示词管理")),

    # ── 开发 ────────────────────────────────────────────────
    ("dev.editor", "编辑器与 IDE", "Editors & IDEs", P(r"\bvscode\b", r"visual[\s\-]?studio", r"\bide\b", r"编辑器", r"\beditor\b", r"\bneovim\b", r"\bvim\b", r"jetbrains", r"obsidian[\s\-]?plugin", r"笔记软件插件")),
    ("dev.terminal", "终端与 Shell", "Terminal & shell", P(r"\bterminal\b", r"终端", r"\bnvim\b", r"\btmux\b", r"\bshell\b", r"\bzsh\b", r"\bbash\b", r"oh[\s\-]?my[\s\-]?zsh", r"终端美化", r"命令提示符", r"\btui\b")),
    ("dev.cli", "命令行工具", "CLI tools", P(r"\bcli\b", r"command[\s\-]?line", r"命令行", r"命令行工具")),
    ("dev.git", "Git 与版本控制", "Git & VCS", P(r"\bgit\b", r"github[\s\-]", r"gitlab", r"版本控制", r"\bworktree", r"代码托管", r"\bdiff\b", r"commit")),
    ("dev.test", "测试与质量", "Testing & QA", P(r"自动化测试", r"\btesting\b", r"\btests?\b", r"e2e", r"end[\s\-]?to[\s\-]?end", r"playwright", r"puppeteer", r"单元测试", r"代码质量", r"代码审查", r"\blint", r"覆盖率")),
    ("dev.build", "构建与包管理", "Build & packaging", P(r"打包", r"\bbuild\b", r"webpack", r"\bvite\b", r"rollup", r"esbuild", r"\bnpm\b", r"包管理", r"\bpypi\b", r"\bcmake\b", r"编译器", r"compiler", r"minif", r"脚手架")),
    ("dev.perf", "调试与性能", "Debug & performance", P(r"调试", r"\bdebug", r"性能优化", r"profiler", r"性能分析", r"\bperf\b", r"内存分析", r"火焰图")),
    ("dev.api", "API 与 SDK", "API & SDK", P(r"\bsdks?\b", r"\bapi\b", r"开发者接口", r"\bopenapi\b", r"\bgraphql\b", r"\brest\b", r"接口文档", r"\bmock\b")),
    ("dev.tool", "开发工具", "Dev tooling", P(r"开发工具", r"开发者工具", r"developer[\s\-]?tools?", r"开发效率", r"devtools", r"效率工具", r"程序员", r"编码效率", r"\bcode[\s\-]?gen", r"代码生成", r"代码可视化", r"可视化编程")),
    ("dev.doc", "代码文档", "Docs & commenting", P(r"文档生成", r"技术文档", r"documentation", r"\bdocs?\b", r"注释", r"\bjsdoc\b", r"readme")),
    ("dev.platform", "平台与集成", "Platforms & integration", P(r"\bplugins?\b", r"插件化", r"插件系统", r"\bextensions?\b", r"插件", r"\bintegrations?\b", r"嵌入式")),

    # ── Web / 前端 / 后端 ───────────────────────────────────
    ("web.frontend", "前端框架", "Frontend frameworks", P(r"\breact\b", r"\bvue\b", r"\bangular", r"\bsvelte\b", r"\bnext\.?js\b", r"\bnuxt\b", r"\bastro\b", r"\bremix\b", r"前端框架", r"前端开发", r"\bsolidjs\b", r"\bsveltekit\b", r"\bqwik\b")),
    ("web.ui", "UI 组件库", "UI libraries", P(r"组件库", r"component[\s\-]?library", r"\bui[\s\-]?(kit|library|components?)\b", r"\bant[\s\-]?design\b", r"element[\s\-]?ui", r"shadcn", r"\bmaterial[\s\-]?(ui|design)", r"tailwind", r"bootstrap", r"\bchakra\b", r"\bmantine\b", r"\bnaive[\s\-]?ui\b", r"组件", r"\bweui\b", r"uikit")),
    ("web.css", "样式与动效", "CSS & motion", P(r"\bcss\b", r"\bscss\b", r"\bsass\b", r"样式", r"动画效果", r"响应式", r"\banimation\b", r"\bframer[\s\-]?motion\b", r"\bgsap\b", r"主题色", r"\bless\b", r"postcss")),
    ("web.site", "网站与静态站", "Sites & static generators", P(r"静态网站", r"static[\s\-]?site", r"\bhexo\b", r"\bhugo\b", r"\bjekyll\b", r"\bvitepress\b", r"\bdocusaurus\b", r"个人博客", r"\bblog\b", r"博客主题", r"主题", r"建站", r"\bwordpress\b", r"落地页", r"landing[\s\-]?page", r"导航站", r"文档站")),
    ("web.fullstack", "全栈与低代码", "Full-stack & low-code", P(r"全栈", r"低代码", r"low[\s\-]?code", r"no[\s\-]?code", r"无代码", r"零代码", r"\badmin\b", r"后台管理", r"管理后台", r"cms", r"内容管理", r"工作流引擎", r"\bcrud\b")),
    ("web.backend", "后端框架", "Backend frameworks", P(r"\bdjango\b", r"\bflask\b", r"\bfastapi\b", r"\bspring\b", r"\bnestjs\b", r"\bexpress\b", r"\bkoa\b", r"\blaravel\b", r"后端框架", r"后端服务", r"服务端", r"后端", r"微服务", r"serverless", r"\bgrpc\b", r"web[\s\-]?framework", r"\bweb\b[^\n]{0,24}framework")),
    ("web.browser", "浏览器自动化", "Browser automation", P(r"浏览器自动化", r"browser[\s\-]?(automation|use)", r"\bselenium\b", r"playwright", r"puppeteer", r"无头浏览器", r"headless", r"网页自动化", r"自动化测试浏览器")),
    ("web.spa", "Web 应用", "Web apps", P(r"web[\s\-]?(app|application|ui)", r"单页应用", r"\bspa\b", r"网页应用", r"在线工具", r"\bdashboard\b", r"仪表盘", r"\bpwa\b", r"websocket", r"浏览器", r"\bhtml5\b")),

    # ── 数据 ────────────────────────────────────────────────
    ("data.db", "数据库", "Databases", P(r"数据库", r"\bdatabase", r"\bmysql\b", r"\bpostgres", r"\bredis\b", r"\bmongodb\b", r"\bsqlite\b", r"\bmariadb\b", r"\boracle\b", r"sql[\s\-]?server", r"\borm\b", r"\bsql\b", r"存储引擎", r"\bolap\b", r"数据仓库")),
    ("data.viz", "数据可视化", "Data visualization", P(r"数据可视化", r"visualization", r"图表", r"\bchart", r"仪表", r"\bbi\b", r"商业智能", r"报表", r"\becharts\b", r"\bd3\b", r"绘图", r"\bplot", r"\bgrafana\b", r"数据大屏", r"信息图")),
    ("data.analysis", "数据分析", "Data analysis", P(r"数据分析", r"data[\s\-]?analysis", r"\bpandas\b", r"dataframe", r"\bjupyter\b", r"notebook", r"探索性", r"statistics", r"统计学", r"data[\s\-]?science", r"数据科学", r"\beda\b", r"大数据", r"\bspark\b", r"\bduckdb\b", r"数据质量", r"data[\s\-]?quality")),
    ("data.crawl", "爬虫与采集", "Crawlers & scraping", P(r"爬虫", r"\bscrap", r"\bcrawler", r"crawling", r"采集", r"抓取", r"\bspider\b", r"数据提取", r"data[\s\-]?extraction", r"\brss\b", r"订阅源")),
    ("data.geo", "地理与空间数据", "Geo & spatial data", P(r"行政区划", r"地理数据", r"\bgeojson\b", r"\bgis\b", r"经纬度",
        r"地址库", r"地图数据", r"地图导航", r"行政区划代码")),
    ("data.etl", "管道与同步", "ETL & sync", P(r"\betl\b", r"数据同步", r"\bsync\b", r"数据管道", r"\bpipeline\b", r"数据迁移", r"backup", r"备份")),

    # ── 云与运维 ────────────────────────────────────────────
    ("cloud.container", "容器与编排", "Containers & orchestration", P(r"\bdocker\b", r"\bkubernetes\b", r"\bk8s\b", r"容器", r"\bcompose\b", r"云原生", r"cluster")),
    ("cloud.deploy", "部署与自托管", "Deploy & self-hosting", P(r"部署", r"\bdeploy", r"自托管", r"self[\s\-]?hosted", r"私有化部署", r"\bnginx\b", r"\bcaddy\b", r"上线", r"\bpaas\b", r"\bvercel\b", r"\bnetlify\b", r"运维")),
    ("cloud.ci", "CI/CD 与自动化", "CI/CD & automation", P(r"\bci[/\s\-]?cd\b", r"github[\s\-]?actions", r"持续集成", r"自动化部署", r"\bjenkins\b", r"\brenovate\b")),
    ("cloud.monitor", "监控与运维", "Monitoring & ops", P(r"监控", r"\bmonitor", r"\bprometheus\b", r"\bgrafana\b", r"日志", r"\blogs?\b", r"告警", r"巡检", r"系统状态", r"uptime")),
    ("cloud.edge", "边缘与云函数", "Edge & serverless", P(r"cloudflare", r"\bworkers?\b", r"边缘计算", r"edge[\s\-]?computing", r"serverless", r"无服务器", r"云函数", r"\blambda\b")),
    ("cloud.cloud", "云服务与平台", "Cloud platforms", P(r"云计算", r"cloud[\s\-]?computing", r"\baws\b", r"\bazure\b", r"\baliyun\b", r"阿里云", r"腾讯云", r"对象存储", r"\bs3\b", r"\bsaas\b")),

    # ── 系统与桌面 ──────────────────────────────────────────
    ("sys.macos", "macOS 工具", "macOS tools", P(r"macos", r"\bmac\b", r"菜单栏", r"menu[\s\-]?bar", r"menubar", r"\bappkit\b", r"\bswiftui\b", r"\balfred\b", r"spotlight", r"窗口管理", r"\bdock\b", r"触控板")),
    ("sys.windows", "Windows 工具", "Windows tools", P(r"windows", r"\bwin32\b", r"powershell", r"\bwpf\b", r"\bwinui\b", r"资源管理器")),
    ("sys.linux", "Linux 与发行版", "Linux & distros", P(r"\blinux\b", r"ubuntu", r"debian", r"arch[\s\-]?linux", r"树莓派", r"raspberry", r"\bnixos\b", r"\bsystemd\b")),
    ("sys.desktop", "桌面应用", "Desktop apps", P(r"桌面应用", r"desktop[\s\-]?(app|application)", r"\belectron\b", r"\btauri\b", r"跨平台桌面", r"原生应用", r"\bqt\b", r"系统工具", r"文件管理", r"磁盘")),
    ("sys.automation", "系统自动化", "System automation", P(r"系统自动化", r"快捷键", r"\bhotkey", r"宏", r"\bmacro\b", r"自动化脚本", r"定时任务", r"\bcron\b", r"批处理", r"窗口自动化", r"桌面自动化")),
    ("sys.virtual", "虚拟化与远程", "Virtualization & remote", P(r"虚拟机", r"\bvm\b", r"虚拟化", r"远程桌面", r"remote[\s\-]?desktop", r"\bvnc\b", r"\bssh\b", r"远程控制", r"屏幕共享", r"容器化")),
    ("sys.resource", "资源与效率", "System utilities", P(r"系统增强", r"清理", r"\bcleaner\b", r"性能提升", r"内存", r"电池", r"电耗", r"输入法", r"剪贴板", r"截图", r"screenshot")),

    # ── 网络 ────────────────────────────────────────────────
    ("net.proxy", "代理与科学上网", "Proxies & circumvention", P(r"代理", r"\bproxy\b", r"\bclash\b", r"\bv2ray\b", r"shadowsocks", r"\bxray\b", r"vmess", r"vless", r"trojan", r"hysteria", r"翻墙", r"科学上网", r"机场", r"\bmihomo\b", r"\bsing[\s\-]?box\b", r"\btrojan\b", r"\bsocks5?\b")),
    ("net.tunnel", "内网穿透与隧道", "Tunnels & NAT", P(r"内网穿透", r"隧道", r"\btunnel\b", r"\bfrp\b", r"\bngrok\b", r"\bnat\b", r"反向代理", r"端口映射", r"\bdns\b", r"ddns", r"\bipv6\b", r"组网", r"\bvpn\b", r"tailscale", r"\bp2p\b", r"zerotier")),
    ("net.http", "HTTP 与网络工具", "HTTP & networking", P(r"\bhttp\b", r"\bcurl\b", r"网络工具", r"\bwireshark\b", r"抓包", r"\btcp\b", r"\budp\b", r"\bwebsocket\b", r"接口调试", r"请求", r"\bgrpc\b", r"网络诊断", r"\bping\b", r"限速", r"带宽")),
    ("net.cloudflare", "CDN 与 DNS", "CDN & DNS", P(r"\bcdn\b", r"加速", r"\bdns\b", r"\bcloudflare\b", r"域名", r"证书", r"https 证书", r"负载均衡", r"网关", r"API 网关")),
    ("net.download", "下载与传输", "Download & transfer", P(r"下载", r"download", r"\baria2\b", r"\bbt\b", r"磁力", r"种子", r"文件传输", r"传输工具", r"\brclone\b", r"\bsftp\b", r"同步盘")),

    # ── 安全与隐私 ──────────────────────────────────────────
    ("sec.security", "安全与攻防", "Security & research", P(r"网络安全", r"\bsecurity\b", r"安全", r"渗透", r"漏洞", r"vulnerability", r"exploit", r"攻击面", r"\bcve\b", r"安全研究")),
    ("sec.reverse", "逆向与调试", "Reverse engineering", P(r"逆向", r"reverse[\s\-]?engineer", r"\bida\b", r"\bfrida\b", r"\bxposed\b", r"lsposed", r"zygisk", r"hook", r"内存修改", r"\binject", r"脱壳", r"静态分析")),
    ("sec.crypto", "加密与鉴权", "Crypto & auth", P(r"加密", r"\bcrypto", r"\bencrypt", r"\baes\b", r"\btls\b", r"密码", r"password", r"鉴权", r"认证", r"\boauth\b", r"\bjwt\b", r"端到端", r"密钥", r"哈希")),
    ("sec.privacy", "隐私与去广告", "Privacy & blocking", P(r"隐私", r"privacy", r"去广告", r"广告拦截", r"adblock", r"防追踪", r"匿名", r"telemetry", r"数据合规", r"脱敏")),
    ("sec.firewall", "防火墙与访问控制", "Firewall & access", P(r"防火墙", r"firewall", r"访问控制", r"权限管理", r"\biam\b", r"堡垒机", r"审计")),

    # ── 影音图像 ────────────────────────────────────────────
    ("media.video", "视频处理", "Video processing", P(r"视频", r"\bvideo\b", r"\bffmpeg\b", r"转码", r"\btranscod", r"剪辑", r"视频编辑", r"video[\s\-]?editing", r"字幕", r"subtitle", r"压制", r"播放器", r"直播", r"推流", r"屏幕录制", r"录屏", r"screen[\s\-]?(record|capture)")),
    ("media.audio", "音频与音乐", "Audio & music", P(r"音乐", r"\bmusic\b", r"音频", r"\baudio\b", r"\bmp3\b", r"\bflac\b", r"播放列表", r"歌词", r"\bspotify\b", r"网易云", r"\bmidi\b", r"音效", r"播客", r"podcast")),
    ("media.image", "图像与素材", "Images & assets", P(r"图像处理", r"\bimage\b", r"图片", r"压缩", r"compress", r"\bwebp\b", r"相册", r"photo", r"壁纸", r"图标", r"icon", r"\bsvg\b", r"插画", r"素材", r"像素", r"截图工具")),
    ("media.gen", "生成与创作", "Generative creation", P(r"生成式", r"文生视频", r"AI 影视", r"影视制作", r"短视频", r"storyboard", r"分镜", r"自动剪辑", r"数字人", r"虚拟主播", r"漫画", r"小说生成")),
    ("media.gif", "动图与录屏", "GIF & capture", P(r"\bgif\b", r"动图", r"录屏", r"屏幕", r"镜像", r"投屏", r"\bcast\b", r"截图", r"截图工具")),

    # ── 文档 / 办公 ─────────────────────────────────────────
    ("doc.markdown", "Markdown 与排版", "Markdown & typesetting", P(r"markdown", r"\bmd\b", r"排版", r"编辑器", r"\btex\b", r"\blatex\b", r"排版引擎", r"\bmermaid\b", r"图表绘制")),
    ("doc.pdf", "PDF 与文档转换", "PDF & conversion", P(r"\bpdf\b", r"文档转换", r"格式转换", r"\bdocx\b", r"\bword\b", r"\bexcel\b", r"\bxlsx\b", r"office", r"文档解析", r"文档处理", r"\bcsv\b", r"电子书", r"\bepub\b")),
    ("doc.note", "笔记与知识管理", "Notes & PKM", P(r"笔记", r"\bnote", r"知识管理", r"第二大脑", r"obsidian", r"notion", r"\bwiki\b", r"双链", r"卡片盒", r"思维导图", r"memo", r"备忘")),
    ("doc.slides", "演示与汇报", "Slides & presentation", P(r"\bppt\b", r"演示文稿", r"幻灯片", r"slides?", r"汇报", r"简历", r"\bresume\b", r"简历模板")),
    ("doc.todo", "任务与日程", "Tasks & calendar", P(r"任务管理", r"\btodo\b", r"待办", r"看板", r"kanban", r"日历", r"calendar", r"日程", r"甘特图", r"番茄钟", r"提醒")),
    ("doc.mail", "邮件与信息流", "Email & feeds", P(r"邮件", r"\bmail\b", r"newsletter", r"\brss\b", r"信息流", r"订阅", r"阅读器", r"\bfeed\b", r"周刊", r"周报", r"\bweekly\b")),
    ("doc.knowledge", "教程与手册", "Guides & references", P(r"教程", r"tutorial", r"指南", r"手册", r"cheatsheet", r"速查", r"文档站", r"文档", r"guide", r"手把手", r"从零", r"入门", r"\bbook\b", r"书籍")),

    # ── 沟通社交 ────────────────────────────────────────────
    ("comm.wechat", "微信生态", "WeChat ecosystem", P(r"微信", r"\bwechat\b", r"weixin", r"公众号", r"小程序", r"\bweui\b", r"企业微信", r"wecom", r"群机器人")),
    ("comm.im", "即时通讯", "Messaging", P(r"即时通讯", r"\bim\b", r"聊天", r"\bchat\b", r"消息推送", r"\btelegram\b", r"\bdiscord\b", r"\bslack\b", r"飞书", r"feishu", r"钉钉", r"dingtalk", r"\bqq\b", r"\bsms\b", r"短信", r"通知")),
    ("comm.social", "社交平台", "Social platforms", P(r"社交", r"\bsocial\b", r"微博", r"知乎", r"小红书", r"xiaohongshu", r"rednote", r"\btwitter\b", r"哔哩哔哩", r"bilibili", r"抖音", r"douyin", r"tiktok", r"朋友圈", r"论坛", r"评论")),
    ("comm.community", "社区与协作", "Community & collaboration", P(r"社区", r"forum", r"协作", r"collaboration", r"团队协作", r"远程协作", r"实时协作", r"在线协作", r"会议", r"视频会议", r"\bmeeting\b")),
    ("comm.bot", "机器人与助手", "Bots & assistants", P(r"\bbot\b", r"机器人", r"推送机器人", r"客服", r"自动回复", r"助手")),

    # ── 移动端 ──────────────────────────────────────────────
    ("mobile.android", "Android", "Android", P(r"android", r"安卓", r"\badb\b", r"\bgradle\b", r"android[\s\-]?开发", r"\bxposed\b")),
    ("mobile.ios", "iOS 与苹果生态", "iOS & Apple", P(r"\bios\b", r"\biphone\b", r"苹果", r"\bapple\b", r"\bswift\b", r"objective[\s\-]?c", r"\bxcode\b", r"越狱")),
    ("mobile.cross", "跨平台框架", "Cross-platform", P(r"\bflutter\b", r"react[\s\-]?native", r"跨平台", r"cross[\s\-]?platform", r"\buni[\s\-]?app\b", r"\bkmp\b", r"tvbox")),
    ("mobile.app", "移动应用", "Mobile apps", P(r"移动应用", r"mobile[\s\-]?(app|development)", r"移动端", r"手机应用", r"\bapps?\b", r"客户端", r"安卓应用")),
    ("mobile.automation", "移动自动化", "Mobile automation", P(r"移动端自动化", r"mobile[\s\-]?automation", r"android[\s\-]?automation", r"群控", r"脚本精灵", r"自动点击", r"\bui[\s\-]?automator\b")),

    # ── 游戏娱乐 ────────────────────────────────────────────
    ("game.game", "游戏与模拟", "Games & emulation", P(r"游戏", r"\bgame", r"\bunity\b", r"\bgodot\b", r"模拟器", r"emulator", r"像素", r"pixel[\s\-]?art", r"\brom\b", r"街机")),
    ("game.fun", "趣味与桌宠", "Fun & toys", P(r"桌宠", r"desktop[\s\-]?pet", r"宠物", r"\bpet\b", r"趣味", r"\bfun\b", r"猫咪", r"\bcat\b", r"弹幕", r"彩蛋")),
    ("game.content", "内容与娱乐", "Content & entertainment", P(r"直播", r"番剧", r"追番", r"影视", r"电影", r"资源站", r"聚合", r"网盘", r"漫画", r"小说")),

    # ── 学习参考 ────────────────────────────────────────────
    ("edu.roadmap", "学习路线", "Roadmaps", P(r"roadmap", r"学习路线", r"知识体系", r"学习路径", r"技能树", r"自学")),
    ("edu.course", "教程与课程", "Courses & tutorials", P(r"教程", r"课程", r"course", r"tutorial", r"教学", r"讲解", r"入门", r"实战", r"从零", r"进阶")),
    ("edu.algo", "算法与面试", "Algorithms & interview", P(r"算法", r"algorithm", r"数据结构", r"\bleetcode\b", r"面试", r"interview", r"刷题", r"竞赛")),
    ("edu.lang", "语言学习", "Language learning", P(r"语言学习", r"外语学习", r"英语学习", r"背单词", r"词汇量",
        r"沉浸式学习", r"\blanguage learning\b", r"\bflashcard")),
    ("edu.book", "书籍与资料", "Books & resources", P(r"书籍", r"\bbook", r"电子书", r"资料", r"资源", r"读书", r"笔记合集", r"课程资料")),
    ("edu.ai4sci", "科研与科学计算", "Science & research", P(r"科研", r"科学计算", r"\bscientific\b", r"\bnumpy\b", r"\bmatlab\b", r"论文", r"paper", r"数学", r"物理", r"生物信息", r"地理", r"气象")),

    # ── 商业与财务 ──────────────────────────────────────────
    ("biz.finance", "记账与财务", "Finance & accounting", P(r"记账", r"财务", r"会计", r"accounting", r"\bfinance\b", r"invoice", r"账单", r"\bledger\b", r"报销", r"税务")),
    ("biz.invest", "投资与行情", "Investing & markets", P(r"股票", r"\bstock\b", r"行情", r"投资", r"量化", r"quant", r"基金", r"加密货币", r"crypto", r"钱包", r"wallet", r"\bbtc\b", r"区块链", r"blockchain")),
    ("biz.trade", "电商与交易", "Commerce & payments", P(r"电商", r"\becommerce\b", r"商城", r"支付", r"payment", r"结算", r"订单", r"库存", r"\bpos\b", r"收银")),
    ("biz.ops", "运营与增长", "Growth & operations", P(r"运营", r"增长", r"营销", r"\bmarketing\b", r"SEO", r"统计", r"analysis", r"广告", r"推广", r"签到", r"积分")),
    ("biz.work", "企业办公", "Enterprise & workflow", P(r"企业", r"办公", r"OA", r"审批", r"工单", r"ERP", r"CRM", r"\bhr\b", r"人事", r"合同", r"数字员工")),

    # ── 生活与其他 ──────────────────────────────────────────
    ("life.health", "健康与运动", "Health & fitness", P(r"健康", r"health", r"健身", r"fitness", r"运动", r"睡眠", r"饮食", r"减肥", r"体检", r"运动轨迹")),
    ("life.living", "生活助手", "Lifestyle", P(r"生活", r"天气", r"weather", r"菜谱", r"食谱", r"购物", r"旅行", r"旅游", r"地图", r"导航", r"快递", r"租房", r"电影票", r"日程提醒")),
    ("life.edu_kid", "亲子与教育", "Family & kids", P(r"亲子", r"儿童", r"宝宝", r"育儿", r"学生", r"家长", r"学校", r"班级", r"考试")),
    ("misc.other", "其他", "Other", P()),  # 兜底：不参与匹配
]

# ════════════════════════════════════════════════════════════
# 形态 (form)：项目“是什么”，与领域正交
# ════════════════════════════════════════════════════════════

FORM_TAGS: list[tuple[str, str, str, tuple[_Pat, ...]]] = [
    ("form.app", "应用 / 工具", "App", P(r"\bapp\b", r"应用", r"工具", r"\btools?\b", r"utility", r"客户端", r"站点", r"平台", r"系统")),
    ("form.cli", "命令行", "CLI", P(r"\bcli\b", r"命令行", r"command[\s\-]?line", r"\bterminal\b", r"\btui\b", r"终端")),
    ("form.ext", "插件 / 扩展", "Extension", P(r"插件", r"\bplugin", r"\bextension\b", r"扩展", r"userscript", r"油猴", r"tampermonkey", r"\baddon\b", r"chrome[\s\-]?extension")),
    ("form.lib", "库 / 框架", "Library", P(r"库", r"\blibrary\b", r"\bframework\b", r"框架", r"\bsdk\b", r"引擎", r"\bengine\b", r"工具包", r"\btoolkit\b", r"组件库", r"依赖")),
    ("form.theme", "主题 / 模板", "Theme", P(r"主题", r"\btheme\b", r"模板", r"\btemplate\b", r"样板", r"boilerplate", r"配色方案", r"\bstyle\b", r"皮肤")),
    ("form.list", "合集 / 清单", "Collection", P(r"合集", r"\bawesome\b", r"清单", r"\blist\b", r"精选", r"资源集", r"导航", r"资料大全", r"总结", r"汇总", r"周刊", r"daily")),
    ("form.asset", "素材 / 字体", "Assets", P(r"字体", r"\bfont\b", r"typeface", r"图标集", r"\bicons?\b", r"素材", r"壁纸", r"配色", r"\bpalette\b", r"logo", r"插画", r"音效包")),
    ("form.doc", "教程 / 文档", "Docs", P(r"教程", r"tutorial", r"文档", r"\bdocs?\b", r"指南", r"手册", r"书籍", r"\bbook\b", r"课程", r"course", r"讲解")),
]

# ════════════════════════════════════════════════════════════
# 领域 (domain)：项目“做什么”，含子分类
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SubCat:
    id: str
    zh: str
    en: str
    pats: tuple[_Pat, ...] = ()


@dataclass(frozen=True)
class Cat:
    id: str
    code: str          # 分类编号（目录脊标，如 AI / DV）
    zh: str
    en: str
    blurb_zh: str
    blurb_en: str
    pats: tuple[_Pat, ...]
    subs: tuple[SubCat, ...]
    kind: str = "domain"   # domain | form | misc


def _sub(id: str, zh: str, en: str, *srcs: str) -> SubCat:
    return SubCat(id, zh, en, P(*srcs) if srcs else ())


CATS: tuple[Cat, ...] = (
    Cat(
        "ai", "AI", "人工智能", "AI",
        "从模型到智能体，本仓库里最密集的一块。",
        "Model tooling through agent harnesses — the densest region of this atlas.",
        P(r"\bai\b", r"人工智能", r"artificial[\s\-]?intelligen", r"\bllm", r"大模型", r"智能体", r"\bagents?\b",
          r"gpt", r"openai", r"claude", r"gemini", r"deepseek", r"qwen", r"\bmcp\b", r"\brag\b",
          r"机器学习", r"machine[\s\-]?learning", r"深度学习", r"deep[\s\-]?learning", r"神经网络",
          r"生成式", r"generative", r"diffusion", r"transformer", r"提示工程", r"prompt", r"embedding",
          r"向量", r"推理", r"\btoken", r"大语言模型", r"计算机视觉", r"图像拼接", r"全景", r"\bsift\b", r"\bstitching\b"),
        (
            _sub("ai.agent", "智能体与编码", "Agents & coding", r"\bagents?\b", r"智能体", r"claude[\s\-]?code", r"\bcodex\b",
                 r"cursor", r"opencode", r"vibe[\s\-]?coding", r"coding[\s\-]?agent", r"编程助手", r"multi[\s\-]?agent",
                 r"编排", r"orchestrat", r"\bcopilot\b", r"skills?", r"技能"),
            _sub("ai.model", "模型与推理", "Models & inference", r"ollama", r"llama", r"\bvllm\b", r"\bsglang\b", r"\bgguf\b",
                 r"推理", r"inference", r"量化", r"quantiz", r"微调", r"fine[\s\-]?tun", r"训练", r"train", r"本地模型",
                 r"weight", r"checkpoint", r"模型部署"),
            _sub("ai.chat", "对话与助手", "Chat & assistants", r"chatbot", r"聊天机器人", r"对话", r"\bchat\b", r"助手",
                 r"assistant", r"问答", r"\bqa\b", r"客服"),
            _sub("ai.rag", "RAG 与知识库", "RAG & knowledge", r"\brag\b", r"检索增强", r"知识库", r"知识图谱", r"向量数据库",
                 r"vector", r"语义搜索", r"semantic[\s\-]?search", r"embedding", r"记忆", r"memory"),
            _sub("ai.tool", "工具与服务", "AI tooling & gateways", r"网关", r"gateway", r"聚合", r"中转", r"代理接口",
                 r"one[\s\-]?api", r"new[\s\-]?api", r"\bbyok\b", r"工作流", r"workflow", r"\bn8n\b", r"dify", r"coze",
                 r"comfy", r"插件"),
            _sub("ai.media", "图像语音视频", "Image / speech / video", r"图像生成", r"image[\s\-]?generation", r"文生图",
                 r"stable[\s\-]?diffusion", r"diffusion", r"语音", r"speech", r"\basr\b", r"\btts\b", r"whisper", r"\bocr\b",
                 r"多模态", r"video[\s\-]?generation", r"文生视频", r"图像识别", r"视觉"),
        ),
    ),
    Cat(
        "dev", "DV", "开发工具", "Developer tooling",
        "编辑器、终端、构建、测试——日常写代码的那一圈。",
        "Editors, terminals, builds and tests — the daily loop around writing code.",
        P(r"开发工具", r"developer[\s\-]?tools?", r"\bdev\b[\s\-]?tools?", r"版本管理", r"环境变量", r"任务运行器",
          r"工具库", r"通用库", r"字符串匹配", r"文本处理", r"日期计算", r"时间处理", r"\bide\b", r"\bvscode\b", r"编辑器", r"\beditor\b", r"\bterminal\b",
          r"终端", r"\bcli\b", r"命令行", r"\bgit\b", r"版本控制", r"自动化测试", r"\btesting\b", r"单元测试",
          r"调试", r"debug", r"性能优化", r"\bperf\b", r"打包", r"构建工具", r"构建系统", r"\bbuild\b[^\n]{0,20}(tool|system|pipeline)", r"\bwebpack\b", r"\bvite\b",
          r"包管理", r"\bnpm\b", r"\bpypi\b", r"编译器", r"compiler", r"代码审查", r"lint", r"重构", r"refactor",
          r"\bdevops\b", r"\bsdk\b"),
        (
            _sub("dev.editor", "编辑器与 IDE", "Editors & IDEs", r"\bvscode\b", r"visual[\s\-]?studio", r"编辑器", r"\beditor\b",
                 r"\bide\b", r"neovim", r"\bvim\b", r"jetbrains", r"语法高亮", r"插件市场"),
            _sub("dev.terminal", "终端与 Shell", "Terminal & shell", r"终端", r"\bterminal\b", r"\bshell\b", r"\bzsh\b", r"\bbash\b",
                 r"tmux", r"命令提示符", r"\btui\b", r"命令行美化", r"oh[\s\-]?my"),
            _sub("dev.vcs", "Git 与协作", "Git & collaboration", r"\bgit\b", r"github", r"gitlab", r"\bworktree", r"commit",
                 r"\bdiff\b", r"代码托管", r"合并请求", r"\bpull[\s\-]?request\b", r"协作"),
            _sub("dev.quality", "测试与质量", "Testing & quality", r"测试", r"\btest", r"e2e", r"playwright", r"puppeteer",
                 r"覆盖率", r"质量", r"审查", r"review", r"lint", r"静态分析", r"格式化", r"formatter"),
            _sub("dev.build", "构建与包管理", "Build & packaging", r"构建工具", r"构建系统", r"打包工具", r"webpack", r"vite", r"rollup", r"esbuild",
                 r"依赖", r"包管理", r"npm", r"pypi", r"cargo", r"编译", r"compiler", r"脚手架", r"monorepo"),
            _sub("dev.perf", "调试与性能", "Debug & performance", r"调试", r"debug", r"性能", r"perf", r"profiler", r"火焰图",
                 r"内存", r"崩溃", r"日志分析"),
            _sub("dev.util", "基础库与工具", "Utility libraries", r"工具库", r"通用库", r"字符串匹配", r"文本处理",
                 r"日期计算", r"时间处理", r"\bdatetime\b", r"date[\s\-]?time", r"编码转换", r"序列化",
                 r"正则", r"依赖库", r"标准库", r"\bfuzz", r"工具函数"),
            _sub("dev.api", "API 与 SDK", "API & SDK", r"\bapi\b", r"\bsdk\b", r"接口", r"openapi", r"graphql", r"\brest\b",
                 r"\bmock\b", r"\bwebhook\b", r"鉴权", r"文档接口"),
            _sub("dev.automation", "自动化与脚本", "Automation & scripts", r"自动化", r"automation", r"脚本", r"script", r"\bhook\b",
                 r"定时", r"cron", r"批量", r"任务调度", r"rpa", r"工作流"),
        ),
    ),
    Cat(
        "web", "WB", "Web 与服务端", "Web & backend",
        "浏览器里跑的一切，从前端框架到后端服务。",
        "Everything that runs in a browser or answers HTTP — frontend to backend.",
        P(r"\bweb\b", r"前端", r"frontend", r"\breact\b", r"\bvue\b", r"\bcss\b", r"组件库", r"component[\s\-]?library",
          r"\bdjango\b", r"\bflask\b", r"\bfastapi\b", r"\bspring\b", r"\bexpress\b", r"\bnginx\b", r"后端", r"服务端",
          r"\bapi\b", r"服务器", r"\bserver\b", r"微服务", r"\bgraphql\b", r"\bgrpc\b", r"网站", r"静态站", r"\bnext\.?js\b", r"文件上传", r"\buploader\b", r"\bupload\b",
          r"低代码", r"后台管理", r"cms", r"\badmin\b", r"\brest\b", r"\bhttp\b", r"全栈", r"bootstrap", r"tailwind", r"\bssr\b"),
        (
            _sub("web.frontend", "前端框架", "Frontend frameworks", r"前端", r"frontend", r"\breact\b", r"\bvue\b", r"angular",
                 r"svelte", r"next\.?js", r"nuxt", r"astro", r"remix", r"组件", r"\bjsx\b", r"\btsx\b"),
            _sub("web.ui", "组件与设计系统", "Components & design systems", r"组件库", r"component[\s\-]?library", r"ui[\s\-]?kit", r"设计系统", r"design[\s\-]?system",
                 r"ant[\s\-]?design", r"element[\s\-]?ui", r"shadcn", r"material", r"\bui\b", r"样式库", r"控件"),
            _sub("web.css", "样式与动效", "CSS & motion", r"\bcss\b", r"\bscss\b", r"\bsass\b", r"\bless\b", r"样式", r"动画",
                 r"animation", r"动效", r"响应式", r"主题色", r"布局"),
            _sub("web.site", "站点与博客", "Sites & blogs", r"静态网站", r"static[\s\-]?site", r"博客", r"\bblog\b", r"hexo", r"hugo",
                 r"jekyll", r"vitepress", r"docusaurus", r"个人主页", r"建站", r"主题", r"导航站", r"文档站", r"wordpress"),
            _sub("web.backend", "后端与接口", "Backend & APIs", r"后端", r"backend", r"服务端", r"django", r"flask", r"fastapi",
                 r"spring", r"express", r"nest", r"gin\b", r"微服务", r"接口", r"\bapi\b", r"网关", r"\borm\b", r"\brpc\b"),
            _sub("web.app", "Web 应用", "Web apps", r"web[\s\-]?(app|application|ui)", r"网页应用", r"单页应用", r"仪表盘", r"dashboard",
                 r"在线工具", r"协作", r"\bpwa\b", r"websocket", r"实时", r"浏览器"),
            _sub("web.lowcode", "低代码与后台", "Low-code & admin", r"低代码", r"low[\s\-]?code", r"no[\s\-]?code", r"无代码",
                 r"零代码", r"后台管理", r"管理后台", r"cms", r"内容管理", r"表单", r"可视化搭建"),
            _sub("web.automation", "浏览器自动化", "Browser automation", r"浏览器自动化", r"browser[\s\-]?automation", r"selenium",
                 r"playwright", r"puppeteer", r"无头", r"headless", r"网页自动化", r"自动化测试"),
        ),
    ),
    Cat(
        "data", "DA", "数据", "Data",
        "数据库、分析、可视化与采集管道。",
        "Databases, analysis, visualization and collection pipelines.",
        P(r"数据库", r"\bdatabase", r"\bsql\b", r"\bmysql\b", r"postgres", r"redis", r"mongodb", r"sqlite", r"\betl\b",
          r"数据分析", r"data[\s\-]?analysis", r"数据可视化", r"visualization", r"pandas", r"jupyter", r"\bbi\b",
          r"报表", r"图表", r"爬虫", r"crawler", r"scraping", r"采集", r"大数据", r"big[\s\-]?data", r"\bspark\b",
          r"统计", r"statistic", r"数据科学", r"data[\s\-]?science", r"数据仓库", r"数据同步", r"\bolap\b",
          r"行政区划", r"地理数据", r"地址库", r"\bgeojson\b", r"数据清洗", r"数据源"),
        (
            _sub("data.db", "数据库与存储", "Databases & storage", r"数据库", r"database", r"\bsql\b", r"mysql", r"postgres",
                 r"redis", r"mongodb", r"sqlite", r"存储", r"\borm\b", r"索引", r"缓存"),
            _sub("data.analytics", "分析与统计", "Analysis & statistics", r"数据分析", r"analysis", r"pandas", r"jupyter", r"notebook",
                 r"统计", r"statistic", r"\beda\b", r"数据科学", r"data[\s\-]?science", r"数据挖掘", r"报表"),
            _sub("data.viz", "可视化与看板", "Visualization & dashboards", r"可视化", r"visualiz", r"图表", r"chart", r"看板", r"dashboard",
                 r"仪表盘", r"大屏", r"echarts", r"d3", r"\bbi\b", r"图形化"),
            _sub("data.pipeline", "管道与同步", "Pipelines & sync", r"管道", r"pipeline", r"\betl\b", r"同步", r"sync", r"迁移",
                 r"备份", r"backup", r"调度", r"数据流"),
            _sub("data.geo", "地理与空间数据", "Geo & spatial data", r"行政区划", r"地理数据", r"\bgeojson\b", r"\bgis\b",
                 r"经纬度", r"地址库", r"地图数据", r"行政区划代码"),
            _sub("data.crawl", "采集与爬虫", "Crawling & collection", r"爬虫", r"crawler", r"scrap", r"采集", r"抓取", r"提取",
                 r"解析", r"数据源", r"\brss\b", r"订阅"),
        ),
    ),
    Cat(
        "cloud", "CL", "云与运维", "Cloud & ops",
        "把东西跑起来、跑稳：容器、部署、CI、监控。",
        "Getting things running and keeping them running: containers, deploys, CI, monitoring.",
        P(r"\bdocker\b", r"kubernetes", r"\bk8s\b", r"容器", r"部署", r"deploy", r"自托管", r"self[\s\-]?hosted",
          r"运维", r"\bci[/\s\-]?cd\b", r"github[\s\-]?actions", r"持续集成", r"监控", r"monitor", r"prometheus",
          r"grafana", r"告警", r"serverless", r"无服务器", r"云函数", r"cloudflare", r"边缘计算", r"\baws\b",
          r"\bazure\b", r"阿里云", r"云计算", r"nginx", r"\bpaas\b", r"基础设施", r"infrastructure"),
        (
            _sub("cloud.container", "容器与编排", "Containers & orchestration", r"docker", r"kubernetes", r"k8s", r"容器", r"编排",
                 r"compose", r"helm", r"集群"),
            _sub("cloud.deploy", "部署与自托管", "Deploy & self-host", r"部署", r"deploy", r"自托管", r"self[\s\-]?hosted", r"私有化",
                 r"nginx", r"caddy", r"服务器管理", r"面板", r"宝塔"),
            _sub("cloud.ci", "CI/CD", "CI/CD", r"\bci[/\s\-]?cd\b", r"github[\s\-]?actions", r"持续集成", r"流水线", r"jenkins",
                 r"自动部署", r"发布流程"),
            _sub("cloud.monitor", "监控与可观测", "Monitoring & observability", r"监控", r"monitor", r"prometheus", r"grafana", r"日志",
                 r"告警", r"trace", r"可观测", r"uptime", r"状态页"),
            _sub("cloud.edge", "边缘与云函数", "Edge & serverless", r"cloudflare", r"\bworkers?\b", r"边缘计算", r"\bedge\b[^\n]{0,16}(comput|runtim|function|native)", r"serverless",
                 r"无服务器", r"云函数", r"lambda", r"\bcdn\b"),
            _sub("cloud.cloud", "云平台与服务", "Cloud platforms", r"aws", r"azure", r"阿里云", r"腾讯云", r"云计算", r"对象存储",
                 r"\bs3\b", r"云服务", r"虚拟机", r"kvm"),
        ),
    ),
    Cat(
        "sys", "SY", "系统与桌面", "System & desktop",
        "操作系统层面的工具：桌面应用、菜单栏、窗口与文件管理。",
        "Operating-system level tools: desktop apps, menu bar utilities, window and file management.",
        P(r"macos", r"\bmac\b", r"windows", r"linux", r"桌面应用", r"桌面工具", r"desktop", r"菜单栏", r"menu[\s\-]?bar", r"\belectron\b",
          r"\btauri\b", r"系统工具", r"系统增强", r"文件管理", r"窗口管理", r"快捷键", r"hotkey", r"虚拟机", r"远程桌面",
          r"剪贴板", r"截图工具", r"磁盘", r"输入法", r"跨平台桌面", r"native[\s\-]?app", r"\bappkit\b", r"\bswiftui\b"),
        (
            _sub("sys.macos", "macOS", "macOS", r"macos", r"\bmac\b", r"菜单栏", r"menubar", r"swiftui", r"appkit", r"alfred",
                 r"spotlight", r"\bdock\b", r"\bfinder\b", r"触控板"),
            _sub("sys.windows", "Windows", "Windows", r"windows", r"win32", r"powershell", r"wpf", r"winui", r"注册表", r"资源管理器"),
            _sub("sys.linux", "Linux", "Linux", r"linux", r"ubuntu", r"debian", r"arch", r"树莓派", r"raspberry", r"nixos", r"systemd"),
            _sub("sys.desktop", "桌面应用", "Desktop apps", r"桌面应用", r"desktop[\s\-]?(app|application)", r"electron", r"tauri",
                 r"跨平台桌面", r"\bqt\b", r"原生应用", r"gui", r"界面应用"),
            _sub("sys.file", "文件与资源管理", "Files & storage", r"文件管理", r"file[\s\-]?manager", r"文件传输", r"磁盘清理", r"清理工具",
                 r"下载器", r"资源管理器", r"文件预览", r"备份工具"),
            _sub("sys.utility", "系统增强", "System utilities", r"系统工具", r"screenshot", r"内存", r"电池", r"电源", r"窗口管理",
                 r"快捷键", r"输入法", r"剪贴板", r"屏保", r"进程管理"),
            _sub("sys.remote", "虚拟化与远程", "Virtualization & remote", r"虚拟机", r"virtual", r"远程", r"remote", r"vnc", r"ssh",
                 r"屏幕共享", r"投屏", r"屏幕镜像", r"容器化"),
        ),
    ),
    Cat(
        "net", "NW", "网络", "Network",
        "代理、隧道、DNS、下载——让流量按你的意思走。",
        "Proxies, tunnels, DNS and transfers — making traffic go where you want.",
        P(r"代理", r"\bproxy\b", r"\bclash\b", r"\bv2ray\b", r"shadowsocks", r"vpn", r"翻墙", r"科学上网", r"隧道", r"tunnel",
          r"内网穿透", r"\bfrp\b", r"\bdns\b", r"\bhttp\b", r"\btcp\b", r"网络工具", r"network", r"抓包", r"下载", r"download",
          r"aria2", r"文件传输", r"\bp2p\b", r"组网", r"\bcdn\b", r"负载均衡", r"带宽", r"网速"),
        (
            _sub("net.proxy", "代理与科学上网", "Proxies", r"代理", r"proxy", r"clash", r"v2ray", r"shadowsocks", r"xray", r"vless",
                 r"trojan", r"hysteria", r"翻墙", r"科学上网", r"机场", r"节点", r"socks", r"订阅链接"),
            _sub("net.tunnel", "隧道与组网", "Tunnels & mesh", r"隧道", r"tunnel", r"内网穿透", r"frp", r"ngrok", r"\bnat\b", r"端口映射",
                 r"组网", r"p2p", r"tailscale", r"zerotier", r"反向代理", r"\bdns\b", r"ddns"),
            _sub("net.tool", "网络工具", "Network tools", r"网络工具", r"抓包", r"wireshark", r"请求", r"http", r"接口调试", r"ping",
                 r"端口", r"扫描", r"限速", r"流量", r"网速测试"),
            _sub("net.transfer", "下载与传输", "Downloads & transfer", r"下载", r"download", r"aria2", r"磁力", r"种子", r"\bbt\b",
                 r"传输", r"rclone", r"sftp", r"同步盘", r"网盘", r"离线下载"),
        ),
    ),
    Cat(
        "sec", "SE", "安全与隐私", "Security & privacy",
        "攻防、逆向、加密与隐私保护。",
        "Offense, reverse engineering, crypto and privacy.",
        P(r"安全", r"security", r"渗透", r"漏洞", r"vulnerability", r"逆向", r"reverse", r"加密", r"crypt", r"密码", r"password",
          r"隐私", r"privacy", r"防火墙", r"firewall", r"鉴权", r"认证", r"auth", r"审计", r"脱敏", r"越权", r"病毒", r"木马",
          r"\bcve\b", r"exploit"),
        (
            _sub("sec.offense", "攻防与漏洞", "Offense & vulnerabilities", r"渗透", r"漏洞", r"vulnerability", r"exploit", r"\bcve\b",
                 r"攻击", r"扫描器", r"提权", r"安全研究", r"红队"),
            _sub("sec.reverse", "逆向与 Hook", "Reverse & hooking", r"逆向", r"reverse", r"\bida\b", r"frida", r"xposed", r"lsposed",
                 r"zygisk", r"hook", r"脱壳", r"静态分析", r"内存修改", r"注入", r"反编译"),
            _sub("sec.crypto", "加密与鉴权", "Crypto & auth", r"加密", r"crypt", r"aes", r"tls", r"ssl", r"哈希", r"密钥", r"password",
                 r"密码", r"鉴权", r"认证", r"oauth", r"jwt", r"签名", r"端到端"),
            _sub("sec.privacy", "隐私与管控", "Privacy & control", r"隐私", r"privacy", r"去广告", r"拦截", r"跟踪", r"匿名", r"合规",
                 r"防火墙", r"firewall", r"权限", r"访问控制", r"审计", r"水印"),
        ),
    ),
    Cat(
        "media", "MD", "影音与图像", "Media & graphics",
        "视频、音频、图片的处理、播放与创作。",
        "Processing, playing and creating video, audio and images.",
        P(r"视频", r"video", r"\bffmpeg\b", r"音频", r"audio", r"音乐", r"music", r"播放器", r"player", r"图像", r"image",
          r"图片", r"\bgif\b", r"字幕", r"剪辑", r"转码", r"直播", r"录屏", r"截图", r"相册", r"壁纸", r"动漫", r"影视"),
        (
            _sub("media.video", "视频", "Video", r"视频", r"video", r"ffmpeg", r"剪辑", r"转码", r"字幕", r"播放器", r"直播", r"推流",
                 r"录屏", r"影视", r"番剧"),
            _sub("media.audio", "音频与音乐", "Audio & music", r"音频", r"audio", r"音乐", r"music", r"播放列表", r"歌词", r"\btts\b",
                 r"语音合成", r"音效", r"播客", r"midi", r"降噪"),
            _sub("media.image", "图像与设计素材", "Images & assets", r"图像", r"image", r"图片", r"相册", r"壁纸", r"截图", r"压缩",
                 r"识别", r"\bocr\b", r"图标", r"插画", r"像素", r"svg", r"水印", r"滤镜"),
            _sub("media.create", "创作与生成", "Creation & generation", r"生成", r"创作", r"文生", r"数字人", r"短剧", r"漫画", r"小说",
                 r"storyboard", r"分镜", r"配音", r"剪辑助手"),
        ),
    ),
    Cat(
        "doc", "DC", "文档与知识", "Docs & knowledge",
        "Markdown、PDF、笔记与知识管理。",
        "Markdown, PDF, notes and knowledge management.",
        P(r"markdown", r"文档处理", r"文档转换", r"文档生成", r"文档解析", r"文档工具", r"\bpdf\b", r"笔记", r"\bnote\b",
          r"知识管理", r"知识库", r"\bwiki\b", r"obsidian",
          r"notion", r"电子书", r"\bepub\b", r"排版", r"思维导图", r"教程", r"tutorial", r"指南", r"手册", r"书籍", r"\bbook\b",
          r"周刊", r"周报", r"\bweekly\b", r"项目清单", r"资源清单", r"收藏清单",
          r"docx", r"office", r"\bexcel\b", r"\bppt\b", r"演示", r"\btex\b"),
        (
            _sub("doc.markdown", "Markdown 与排版", "Markdown & typesetting", r"markdown", r"排版", r"md", r"latex", r"\btex\b",
                 r"mermaid", r"富文本", r"编辑器"),
            _sub("doc.convert", "转换与处理", "Conversion & processing", r"转换", r"convert", r"pdf", r"docx", r"office", r"excel",
                 r"xlsx", r"ppt", r"解析", r"抽取", r"合并", r"压缩"),
            _sub("doc.note", "笔记与 PKM", "Notes & PKM", r"笔记", r"note", r"知识管理", r"第二大脑", r"obsidian", r"notion", r"双链",
                 r"卡片", r"memo", r"wiki", r"标签管理"),
            _sub("doc.learn", "教程与资料", "Guides & references", r"教程", r"tutorial", r"指南", r"手册", r"cheatsheet", r"书籍", r"book",
                 r"课程", r"course", r"讲解", r"从零", r"入门", r"实战", r"笔记合集"),
        ),
    ),
    Cat(
        "office", "OF", "办公与效率", "Office & productivity",
        "任务、日历、邮件、演示——把日常事务收进一个窗口。",
        "Tasks, calendars, mail and decks — daily operations in one window.",
        P(r"办公", r"效率", r"productivity", r"任务管理", r"todo", r"待办", r"看板", r"kanban", r"日历", r"calendar", r"邮件",
          r"mail", r"自动化工作流", r"n8n", r"表单", r"审批", r"工单", r"\boa\b", r"erp", r"crm", r"记账", r"财务", r"invoice",
          r"支付", r"payment", r"电商", r"商城", r"库存", r"签到", r"运营", r"投资", r"理财", r"定投", r"投资策略"),
        (
            _sub("office.task", "任务与日程", "Tasks & calendar", r"任务", r"待办", r"todo", r"看板", r"kanban", r"日历", r"calendar",
                 r"日程", r"提醒", r"甘特", r"番茄钟", r"时间管理"),
            _sub("office.comm", "邮件与通知", "Mail & notifications", r"邮件", r"mail", r"通知", r"推送", r"消息", r"newsletter",
                 r"订阅", r"阅读器", r"rss"),
            _sub("office.doc", "文档办公", "Documents", r"office", r"文档", r"excel", r"表格", r"ppt", r"演示", r"简历", r"report",
                 r"报表", r"打印", r"签名"),
            _sub("office.biz", "财务与经营", "Finance & business", r"记账", r"财务", r"会计", r"发票", r"报销", r"订单",
                 r"电商", r"商城", r"支付", r"库存", r"客户", r"crm", r"erp", r"审批", r"工单", r"签到", r"运营",
                 r"投资", r"理财", r"定投", r"股票", r"基金", r"行情", r"保险", r"税务", r"薪酬"),
        ),
    ),
    Cat(
        "comm", "CM", "沟通与社交", "Communication & social",
        "IM、机器人、社交平台与社区协作。",
        "Messaging, bots, social platforms and community collaboration.",
        P(r"即时通讯", r"聊天", r"chat", r"机器人", r"\bbot\b", r"微信", r"wechat", r"公众号", r"telegram", r"discord", r"slack",
          r"飞书", r"钉钉", r"\bqq\b", r"社交", r"social", r"微博", r"知乎", r"小红书", r"bilibili", r"社区", r"论坛",
          r"消息推送", r"客服", r"会议", r"meeting", r"直播互动"),
        (
            _sub("comm.wechat", "微信生态", "WeChat", r"微信", r"wechat", r"公众号", r"小程序", r"企业微信", r"wecom", r"weui",
                 r"微信群", r"朋友圈"),
            _sub("comm.im", "即时通讯", "Messaging", r"即时通讯", r"聊天", r"chat", r"\bim\b", r"消息", r"通知", r"telegram", r"discord",
                 r"slack", r"飞书", r"钉钉", r"qq", r"短信"),
            _sub("comm.bot", "机器人与自动化", "Bots", r"机器人", r"bot", r"自动回复", r"推送", r"客服", r"助手", r"群管理", r"签到"),
            _sub("comm.social", "社交与内容平台", "Social platforms", r"社交", r"social", r"社区", r"论坛", r"微博", r"知乎", r"小红书",
                 r"bilibili", r"抖音", r"评论", r"话题"),
        ),
    ),
    Cat(
        "mobile", "MO", "移动端", "Mobile",
        "手机与平板上的应用、框架和自动化。",
        "Apps, frameworks and automation for phones and tablets.",
        P(r"android", r"安卓", r"\bios\b", r"iphone", r"手机", r"移动端", r"mobile", r"flutter", r"react[\s\-]?native",
          r"移动应用", r"\bapk\b", r"\bswift\b", r"kotlin", r"小程序", r"跨平台应用"),
        (
            _sub("mobile.android", "Android", "Android", r"android", r"安卓", r"apk", r"adb", r"gradle", r"magisk", r"xposed"),
            _sub("mobile.ios", "iOS 与苹果", "iOS & Apple", r"\bios\b", r"iphone", r"ipad", r"swift", r"xcode", r"objective[\s\-]?c",
                 r"越狱", r"apple"),
            _sub("mobile.cross", "跨平台框架", "Cross-platform", r"flutter", r"react[\s\-]?native", r"跨平台", r"uni[\s\-]?app", r"kmp",
                 r"rn\b", r"混合开发"),
            _sub("mobile.app", "移动应用", "Mobile apps", r"移动应用", r"mobile[\s\-]?app", r"手机应用", r"客户端", r"移动端"),
            _sub("mobile.automation", "移动自动化", "Mobile automation", r"自动化", r"群控", r"自动点击", r"脚本", r"ui[\s\-]?automator",
                 r"爬虫", r"签到"),
        ),
    ),
    Cat(
        "design", "DS", "设计与视觉", "Design & visual",
        "字体、图标、配色、设计系统这些视觉基础件。",
        "Type, icons, palettes and design systems — the visual primitives.",
        P(r"设计", r"design", r"字体", r"\bfont\b", r"typography", r"图标", r"\bicon", r"配色", r"palette", r"颜色", r"插画",
          r"svg", r"动画效果", r"素材", r"logo", r"figma", r"视觉设计", r"排版"),
        (
            _sub("design.font", "字体与排版", "Type & typography", r"字体", r"font", r"typeface", r"字库", r"连字", r"ligature",
                 r"排版", r"字重", r"中文排版"),
            _sub("design.icon", "图标与插画", "Icons & illustration", r"图标", r"icon", r"插画", r"illustration", r"svg", r"logo",
                 r"矢量", r"素材", r"贴纸"),
            _sub("design.color", "配色与主题", "Color & themes", r"配色", r"palette", r"颜色", r"color", r"主题", r"theme", r"渐变",
                 r"调色板", r"色卡"),
            _sub("design.system", "设计系统与工具", "Design systems & tools", r"设计系统", r"design[\s\-]?system", r"组件规范", r"figma",
                 r"原型", r"白板", r"流程设计", r"线框"),
        ),
    ),
    Cat(
        "edu", "ED", "学习与参考", "Learning & reference",
        "路线图、教程、书籍、算法与科研。",
        "Roadmaps, tutorials, books, algorithms and research.",
        P(r"教程", r"tutorial", r"学习路线", r"学习资源", r"自学", r"课程", r"course", r"教学",
          r"路线图", r"roadmap", r"面试", r"interview", r"算法",
          r"algorithm", r"数据结构", r"leetcode", r"书籍", r"\bbook\b", r"资料", r"科研", r"论文", r"paper", r"数学",
          r"科学计算", r"考试", r"知识体系", r"入门", r"指南", r"语言学习", r"外语学习", r"背单词", r"词汇", r"英语学习"),
        (
            _sub("edu.roadmap", "学习路线", "Roadmaps", r"路线", r"roadmap", r"知识体系", r"学习路径", r"技能树", r"自学", r"进阶"),
            _sub("edu.course", "教程与课程", "Courses", r"教程", r"tutorial", r"课程", r"course", r"教学", r"讲解", r"从零", r"实战",
                 r"入门", r"视频课"),
            _sub("edu.algo", "算法与面试", "Algorithms & interview", r"算法", r"algorithm", r"数据结构", r"leetcode", r"面试", r"interview",
                 r"刷题", r"竞赛", r"\bcs\b"),
            _sub("edu.book", "书籍与资料", "Books & resources", r"书籍", r"book", r"电子书", r"资料", r"资源", r"读书", r"手册",
                 r"合集", r"笔记"),
            _sub("edu.lang", "语言学习", "Language learning", r"语言学习", r"外语学习", r"英语学习", r"背单词", r"词汇量",
                 r"沉浸式学习", r"\blanguage learning\b", r"\bflashcard"),
            _sub("edu.science", "科研与科学计算", "Science & computing", r"科研", r"科学", r"scientific", r"numpy", r"matlab", r"数学",
                 r"物理", r"生物", r"气象", r"地理", r"论文", r"仿真", r"模拟"),
        ),
    ),
    Cat(
        "game", "GM", "游戏与娱乐", "Games & fun",
        "游戏、模拟器、桌宠和一点纯粹的乐趣。",
        "Games, emulators, desktop pets and pure fun.",
        P(r"游戏", r"\bgame", r"unity", r"godot", r"模拟器", r"emulator", r"像素", r"桌宠", r"宠物", r"娱乐", r"趣味",
          r"弹幕", r"追番", r"直播", r"小说", r"漫画", r"斗地主", r"麻将", r"猜", r"打卡"),
        (
            _sub("game.play", "游戏与模拟", "Games & emulation", r"游戏", r"game", r"unity", r"godot", r"模拟器", r"emulator", r"像素",
                 r"rom", r"街机", r"外挂"),
            _sub("game.fun", "趣味与桌宠", "Fun & pets", r"桌宠", r"宠物", r"pet", r"趣味", r"彩蛋", r"猫咪", r"打卡", r"陪伴"),
            _sub("game.content", "内容与聚合", "Content & aggregation", r"聚合", r"追番", r"番剧", r"漫画", r"小说", r"资源", r"直播",
                 r"影视", r"网盘"),
        ),
    ),
    Cat(
        "misc", "MS", "其他", "Other",
        "尚未归入其他领域，或用途很杂的项目。",
        "Projects that don't sit in one region yet, or span too many.",
        P(),
        (_sub("misc.other", "其他项目", "Other projects"),),
        kind="misc",
    ),
)

CAT_BY_ID = {c.id: c for c in CATS}
SUB_BY_ID = {s.id: (c, s) for c in CATS for s in c.subs}
TAG_BY_ID = {t[0]: t for t in CANON_TAGS}
FORM_BY_ID = {t[0]: t for t in FORM_TAGS}

# 子类归属：sub id 前缀即其父领域
SUB_TO_CAT = {s.id: c.id for c in CATS for s in c.subs}

# 领域基础分：部分领域天然更容易被宽泛词命中，需要一点先验抑制
CAT_PRIOR = {
    "misc": 0.0,
    "web": 1.0,
    "dev": 1.0,
    "ai": 0.95,
    "sys": 0.95,
    "data": 0.95,
    "doc": 0.95,
    "office": 0.9,
    "comm": 0.9,
    "media": 0.95,
    "net": 0.95,
    "sec": 0.95,
    "cloud": 0.95,
    "mobile": 0.95,
    "design": 0.9,
    "edu": 0.85,
    "game": 0.9,
}

# 泛词降权：命中这些词的领域得分乘以折扣。它们几乎出现在每条 README 里，
# 只有配合该领域更具体的证据时才有意义。
GENERIC_PATTERNS = {
    "工具", "工具集", "平台", "系统", "应用", "软件", "程序", "服务", "项目",
    "在线工具", "开源", "开源项目", "开源工具", "开源软件", "解决方案", "框架",
    "网络安全", "安全", "认证", "配置", "界面", "功能", "支持", "数据", "内容",
    "文档", "组件", "插件", "主题", "模型", "生成", "分析", "管理", "设计",
}
NON_SPECIFIC_PENALTY = 0.62
