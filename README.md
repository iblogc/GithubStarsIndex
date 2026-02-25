# GitHub Stars Index

> 自动抓取 GitHub Stars，生成 AI 摘要，便于检索。

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [Obsidian 同步（可选）](#obsidian-同步可选)
- [本地运行](#本地运行)

---

## 功能特性

- 🤖 自动抓取 GitHub 账号 Star 的全部仓库
- 📝 为每个仓库读取 README，调用 AI 生成内容摘要和技术标签
- ⚡️ **高效率**：支持**并发调用** AI 接口，大幅提升处理大量新项目时的速度
- 🗃️ **数据驱动**：所有信息存储为 `data/stars.json`，支持二次开发
- 🎨 **模版驱动**：使用 Jinja2 模版生成 Markdown 和 HTML 静态页面
- ⏭️ 增量更新，已处理项目状态保存在 JSON 中，避免重复消耗 API
- ⏰ GitHub Actions **定时自动运行**，cron 表达式自由配置
- 🔄 可选：自动将生成的 `stars_zh.md` & `stars_en.md` **推送到 Obsidian Vault 仓库**
- 🌐 可选：自动同步到 **GitHub Pages** 分支，支持多语言 (ZH/EN) 切换与前端交互搜索
- 💻 支持任意 **OpenAI 格式兼容接口**（OpenAI / Azure / 本地 Ollama 等）

---

## 快速开始

### 第一步：Fork 本仓库

点击右上角 **Fork**，将本仓库复制到你自己的账号下。

### 第二步：配置 Secrets 和 Variables

进入 Fork 后仓库的 **Settings → Secrets and variables → Actions**，分两类配置：

**🔐 Secrets**（机密，加密保存）

| Secret 名称  | 说明                                                      | 必填 |
| ------------ | --------------------------------------------------------- | ---- |
| `AI_API_KEY` | AI 接口的 API Key                                         | ✅    |
| `VAULT_PAT`  | Vault 仓库的 Personal Access Token（仅 Vault 同步时需要） | ❌    |

> `GITHUB_TOKEN` 由 Actions 自动提供，无需手动添加。

**📋 Variables**（非机密，明文保存）

| Variable 名称        | 说明                                                                             | 必填 |
| -------------------- | -------------------------------------------------------------------------------- | ---- |
| `GH_USERNAME`        | 要抓取 Stars 的 GitHub 用户名                                                    | ✅    |
| `AI_BASE_URL`        | AI 接口地址（OpenAI 兼容格式，如 `https://api.openai.com/v1`）                   | ✅    |
| `AI_MODEL`           | 模型名称（如 `gpt-4o-mini`），不填则用 `config.yml` 默认值                       | ❌    |
| `MAX_CONCURRENCY`    | AI 摘要并发生成数量（默认 `5`），过高可能触发接口限速                            | ❌    |
| `VAULT_SYNC_ENABLED` | 是否启用同步到 Vault 仓库，填 `true` 开启                                        | ❌    |
| `VAULT_REPO`         | Vault 仓库（`owner/repo-name` 格式）                                             | ❌    |
| `VAULT_FILE_PATH`    | 目标路径前缀（如 `GitHub-Stars/stars.md`），将自动生成 `_zh.md` 和 `_en.md` 后缀 | ❌    |
| `PAGES_SYNC_ENABLED` | 是否启用同步到 GitHub Pages，填 `true` 开启                                      | ❌    |

### 第三步：按需修改 config.yml

`config.yml` 只包含非敏感的**行为配置**，通常不需要修改，默认即可直接运行。如需调整 AI 超时等参数，在此修改即可。

### 第四步：自定义定时频率

编辑 `.github/workflows/sync.yml`，修改 `cron` 表达式：

```yaml
schedule:
  - cron: "0 2 * * 1"  # 改为你想要的频率
```

> 💡 可使用 [crontab.guru](https://crontab.guru) 在线生成 cron 表达式

### 第五步：手动触发首次运行

进入 **Actions → 🌟 GitHub Stars Index同步 → Run workflow**，手动触发首次全量同步。

> 💡 **测试技巧**：
> - **`test_limit`**: 第一次运行如果 Star 很多，可以填入数字（如 `5`），脚本会只总结前 5 个新项目，方便你快速测试预览效果。
> - **`force_rebuild`**: 勾选此项会清删本地已有的 `data/stars.json` 数据集，强制对所有项目重新生成摘要。

---

## 配置说明

所有非敏感配置均在 `config.yml` 中管理：

```yaml
ai:
  model: "gpt-4o-mini"         # AI 模型（可被 AI_MODEL Variable 覆盖）
  max_readme_length: 8000       # README 截取长度（避免超 Token）
  timeout: 60                   # 请求超时（秒）
  max_retries: 3                # 失败重试次数
  concurrency: 5                # 并发生成摘要的线程数（可被 MAX_CONCURRENCY 覆盖）

output:
  file_path: "stars.md"         # 输出文件名。注：文件现在生成在 dist/ 目录下（如 dist/stars_zh.md）

vault_sync:
  # Vault 同步的开关和仓库名通过 Actions Variables 控制，此处仅配置默认路径和 commit 信息
  default_file_path: "GitHub-Stars/stars.md"
  commit_message: "🤖 自动更新 GitHub Stars 摘要"

pages_sync:
  # GitHub Pages 开关通过 Actions Variable: PAGES_SYNC_ENABLED 控制
  output_dir: "dist"             # 生成文件的输出目录
  file_name: "index.html"        # 生成的文件名
  template: "index.html.j2"      # 使用的模板文件
```

---

## Obsidian 同步（可选）

如果你想将 `stars.md` 自动同步到 Obsidian Vault：

1. 在 Vault 仓库所属账号创建一个 **[Fine-grained Personal Access Token（PAT）](https://github.com/settings/personal-access-tokens)**，赋予目标 Vault 仓库的 **Contents: Read and write** 权限

2. 将 PAT 添加为本仓库的 Secret：`VAULT_PAT`

3. 在仓库 **Settings → Secrets and variables → Actions → Variables** 中配置：

   | Variable 名称        | 示例值                              |
   | -------------------- | ----------------------------------- |
   | `VAULT_SYNC_ENABLED` | `true`                              |
   | `VAULT_REPO`         | `your-username/your-obsidian-vault` |
   | `VAULT_FILE_PATH`    | `GitHub-Stars/stars.md`             |

4. 确保 Obsidian Git 插件开启了**定时 Pull**，每次 Action 运行后 Obsidian 会自动获取最新的 `stars_zh.md` 和 `stars_en.md`
    
---

## GitHub Pages 部署（可选）

如果你想展示一个漂亮的静态网页：

1. 在仓库 **Settings → Secrets and variables → Actions → Variables** 中配置：

   | Variable 名称        | 示例值 | 说明                             |
   | -------------------- | ------ | -------------------------------- |
   | `PAGES_SYNC_ENABLED` | `true` | 填 `true` 以开启 HTML 生成及部署 |

2. 进入仓库 **Settings → Pages**：
   - **Build and deployment -> Source**: 选择 `Deploy from a branch`
   - **Branch**: 选择 `gh-pages` 分支，目录选择 `/(root)`
   - 点击 **Save**

3. 成功运行一次 Action 后，你就可以通过 `https://<username>.github.io/<repo-name>/` 访问你的 Stars Index 页面了。

---

## 本地运行

```bash
# 克隆仓库
git clone https://github.com/your-username/github-stars-summary.git
cd github-stars-summary

# 安装依赖
pip install -r requirements.txt

# 使用环境变量文件进行测试 (推荐)
# 1. 复制示例文件
cp .env.example .env
# 2. 编辑 .env 并填入你的配置
# 3. 直接运行脚本
python scripts/sync_stars.py

# 或者手动设置环境变量
# ── 必填环境变量 ──
export GH_USERNAME="your-github-username"       # 要抓取 Stars 的 GitHub 用户名
export AI_BASE_URL="https://api.openai.com/v1"  # AI 接口地址
export AI_API_KEY="sk-..."                       # AI API Key

# ── 选填环境变量 ──
export AI_MODEL="gpt-4o-mini"     # 不填则用 config.yml 中的默认值
export MAX_CONCURRENCY=5          # 并发数
export GH_TOKEN="ghp_..."         # GitHub Token，不填也能运行，但 API 限速更严（60次/小时）

# 运行
python scripts/sync_stars.py
```

---

## 文件说明

| 文件                         | 说明                                 |
| ---------------------------- | ------------------------------------ |
| `config.yml`                 | 主配置文件（非敏感配置）             |
| `data/stars.json`            | **核心数据集**（抓取的全量项目数据） |
| `templates/stars.md.j2`      | Markdown 生成模版                    |
| `templates/index.html.j2`    | HTML (GitHub Pages) 生成模版         |
| `dist/index.html`            | 自动生成的 HTML 静态页面             |
| `dist/stars_zh.md`           | 自动生成的中文版 Stars Index 文档    |
| `dist/stars_en.md`           | 自动生成的英文版 Stars Index 文档    |
| `scripts/sync_stars.py`      | 核心同步与生成脚本                   |
| `.github/workflows/sync.yml` | GitHub Actions 定时工作流            |
