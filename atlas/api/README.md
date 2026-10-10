# Stars Atlas 数据接口

面向 Agent 与人查阅的静态数据集。**没有服务端**：所有内容都是预先切好的文件，
用普通 GET 即可获取。

- 数据更新：2026-10-10 10:27 UTC
- 规模：1176 个仓库 / 23,381,998 stars / 17 个领域 / 101 个主题
- 入口：`https://stars.iblogc.com/atlas/api/index.json`

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
| `ai` | 人工智能 | 458 |
| `dev` | 开发工具 | 358 |
| `web` | Web 与服务端 | 426 |
| `data` | 数据 | 115 |
| `cloud` | 云与运维 | 70 |
| `sys` | 系统与桌面 | 199 |
| `net` | 网络 | 77 |
| `sec` | 安全与隐私 | 40 |
| `media` | 影音与图像 | 114 |
| `doc` | 文档与知识 | 107 |
| `office` | 办公与效率 | 39 |
| `comm` | 沟通与社交 | 117 |
| `mobile` | 移动端 | 114 |
| `design` | 设计与视觉 | 41 |
| `edu` | 学习与参考 | 47 |
| `game` | 游戏与娱乐 | 30 |
| `misc` | 其他 | 5 |

完整清单（含主题分片与字节数）见 `api/index.json` 的 `shards` 字段。

## 字段

每个仓库对象的字段含义，见 `api/index.json` 的 `fields` 字段（含中文说明）。

## 订阅

- 全站：`https://stars.iblogc.com/atlas/feed.xml`
- 分领域：`feed-<领域 id>.xml`，例如 `feed-ai.xml`

## 许可与出处

数据来自 GitHub 公开仓库，摘要由 AI 生成、分类由规则引擎生成，仅供参考；
每条记录都带 `url`，请以原仓库为准。
