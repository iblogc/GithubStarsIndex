# GitHub Stars Index

English | [中文](README.md)

> Automatically fetch GitHub Stars, generate AI summaries, and make them easily searchable.

## Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Stars Atlas (Star Chart View)](#stars-atlas-star-chart-view)
- [Agent Access (No Server Required)](#agent-access-no-server-required)
- [RSS Feeds](#rss-feeds)
- [Configuration Reference (Environment Variables / .env)](#configuration-reference-environment-variables--env)
- [Obsidian Sync (Optional)](#obsidian-sync-optional)
- [Local Installation](#local-installation)

---

## Features

- 🤖 **Automatic Sync**: Fetches all starred repositories from your GitHub account.
- 📝 **AI Summaries**: Reads each repository's README and uses AI to generate concise summaries and technical tags.
- 🏷️ **Smart Tagging**: Built-in `TAG_MAPPING` for automatic synonym merging and tech stack normalization (e.g., LLM -> Large Language Model), preventing tag explosion.
- ⚡️ **High Performance**: Supports **concurrency** for AI API calls, significantly speeding up the processing of new projects.
- 🗃️ **Data Driven**: Uses `data/stars.json` at runtime and publishes it to `gh-pages/data/stars.json` for custom development.
- 🎨 **Template Driven**: Uses Jinja2 templates to generate Markdown and static HTML search pages.
- ⏭️ **Smart Incremental Updates**: Uses AI for new projects, while **automatically updating star counts and metadata** for existing ones.
- ⏰ **Automated Workflow**: Regularly runs via GitHub Actions with customizable cron schedules.
- 🔄 **Vault Sync (Optional)**: Automatically pushes generated `stars_zh.md` & `stars_en.md` to your **Obsidian Vault**.
- 🌐 **GitHub Pages (Optional)**: Deploys a static search page with multi-language (ZH/EN) support and real-time search.
- 💻 **Flexible AI Providers**: Compatible with any **OpenAI-format API** (OpenAI, Azure, local Ollama, etc.).
- 🗺️ **Stars Atlas**: Alongside the classic search page, generates an `atlas/` view that sorts every repo into **16 domains / 85 subcategories / 100+ tags** using a **pure rule engine** (no AI, no extra keys), presented as a master–detail data grid: faceted filters on the left, a dense sortable table in the middle, full records on the right. Facet counts, infinite scroll, keyboard navigation, shareable URLs, en/zh and light/dark.
- 🤖 **Agent-ready**: Publishes a static data API (per-domain/per-topic shards plus a JSONL corpus) and ships a **zero-dependency MCP server**, so an agent can search, get recommendations, and pull full records on its own.
- 📡 **RSS feeds**: Site-wide and per-domain, ordered by when you starred each repo.

---

## Stars Atlas (Star Chart View)

The classic page lays repos out as a grid of cards. Atlas offers a different reading: **a searchable data table**, built from the same `data/stars.json`. Both views coexist and neither disturbs the other.

Three columns: faceted filters (domain, language, tag, form, year, star tier) on the left, a dense sortable table in the middle, the selected repository's full record on the right. On narrow screens the rail becomes a drawer and details move into a dialog.

| Dimension | Description |
| :--- | :--- |
| **Domains** (16) | AI, Developer tooling, Web & backend, Data, Cloud & ops, System & desktop, Network, Security & privacy, Media & graphics, Docs & knowledge, Office & productivity, Communication & social, Mobile, Design & visual, Learning & reference, Games & fun |
| **Subcategories** (85) | Deeper cuts, e.g. "AI → Agents & coding / Models & inference / RAG & knowledge" |
| **Topic tags** (100+) | Fine-grained keywords for cross-domain filtering: MCP, Proxies, WeChat, Type & typography … |
| **Forms** (8) | App / CLI / Extension / Library / Theme / Collection / Asset / Docs |
| **Description / AI summary** | The description is the repo's own GitHub text; the AI summary is model-generated. Both are shown separately. |
| **Repo topics / AI tags** | The former are the author's GitHub topics; the latter are AI-derived category tags. |

Classification runs in the keyword engine in `scripts/atlas/taxonomy.py` — deterministic, explainable, and offline:

- **Deterministic**: the same `stars.json` always yields the same classification, so results are reproducible and diffable.
- **Explainable**: `--explain` prints exactly which keywords matched, in which field.
- **Spam-resistant**: fields are weighted (topics/tags most trustworthy, summary prose least), and only the three most specific matches per field count, so a category with a long keyword list does not automatically win.
- **Honest fallback**: thin evidence lands in "Other" rather than being forced into a domain.

```bash
# Build the data grid page (reads data/stars.json only; no fetching, no AI)
python3 scripts/sync_atlas.py

# Classification stats only
python3 scripts/sync_atlas.py --stats

# Why was a repo classified that way?
python3 scripts/sync_atlas.py --explain microsoft/playwright -v

# Classification regression tests (29 real anchors + structural invariants)
python3 tests/test_atlas.py -v
```

Artifacts:

| File | Description |
| :--- | :--- |
| `dist/atlas/index.html` | The data grid page (published at `https://<your-domain>/atlas/`) |
| `dist/atlas/atlas.json` | Dataset with classifications, for downstream use |

> [!NOTE]
> To adjust the taxonomy, edit `CANON_TAGS` / `CATS` in `scripts/atlas/taxonomy.py`, then run `python3 tests/test_atlas.py` to confirm no anchor regressed.

---

## Agent Access (No Server Required)

The site is static — there is no backend to query. The "API" is therefore a set of
**pre-split files** that any agent can `GET` without credentials, and without running anything.

**Read the catalog first, then pull one shard.** `api/index.json` lists every domain,
subcategory and topic with counts, plus `shards[].bytes` — so the caller knows the context
cost *before* fetching, instead of dumping the whole site into the window.

| What you need | What to fetch |
| :--- | :--- |
| An overview of the taxonomy | `api/index.json` (tens of KB) |
| One specific area | `api/c/<domain>.json` (e.g. `api/c/ai.json`), `api/t/<topic>.json` |
| A cheap scan of everything | `api/index.jsonl` (one compact profile per line, ~1/3 the size of the full corpus) |
| Full summaries and topics | `api/repos.jsonl` |

The `fields` key in `api/index.json` documents every field in Chinese, readable by a model as-is.

### MCP (optional, recommended)

If the host supports MCP, mount `scripts/atlas_mcp.py` to call this as tools instead of
hand-rolling HTTP requests. It has **no third-party dependencies** (standard library only),
so there is nothing to install:

```json
{
  "mcpServers": {
    "stars-atlas": {
      "command": "python3",
      "args": ["/absolute/path/scripts/atlas_mcp.py"]
    }
  }
}
```

It exposes five tools: `list_facets`, `search_repos`, **`recommend_for_task`** (describe your
task in plain language and get suitable projects), `get_repo`, and `get_domain_digest`.
Run `python3 scripts/atlas_mcp.py --selftest` to check it without a host.

> [!TIP]
> Find candidates → read summaries → open `url` to read the source. That is the intended
> loop: agents can use this data both to **recommend** an existing project and to **learn
> from** its implementation.

---

## RSS Feeds

`dist/atlas/feed.xml` (site-wide) and `dist/atlas/feed-<domain>.xml` (e.g. `feed-ai.xml`),
standard RSS 2.0, ordered by **when you starred each repo** — so new stars show up in your reader.

- All: `https://<your-domain>/atlas/feed.xml`
- Per domain: `https://<your-domain>/atlas/feed-ai.xml`

The page head already carries `<link rel="alternate" type="application/rss+xml">`, so most
readers will auto-discover the feed when you paste the site URL.

---

## Process Overview

```mermaid
graph TD
    Start([Start]) --> Trigger{Trigger Mode}
    Trigger -- "Actions (Schedule/Manual)" --> Sync[Run sync_stars.py]
    Trigger -- "Local (Manual Run)" --> Sync
    
    Sync --> FetchGH[Fetch GitHub Stars]
    FetchGH --> Filter{Incremental Check}
    Filter -- "Processed Projects" --> UpdateMeta[Update Stars/Metadata]
    Filter -- "New Projects" --> FetchRD[Fetch README]
    
    FetchRD --> AI[AI Summarization/Tagging]
    AI --> Norm[Tag Governance/Normalization]
    Norm --> Store[(data/stars.json)]
    UpdateMeta --> Store
    Store --> Render
    
    Render[[Jinja2 Template Rendering]] --> Output
    
    subgraph Output [Output Results]
        MD[Markdown Archive]
        HTML[Static HTML Search Page]
    end
    
    Output --> Dispatch{Distribution}
    Dispatch -- "VAULT_SYNC" --> Obs[Push to Obsidian Vault]
    Dispatch -- "PAGES_SYNC" --> Pages[Deploy GitHub Pages]
    
    Obs --> End([Finish])
    Pages --> End
```

---

## Quick Start

### Step 1: Fork This Repository

Click the **Fork** button in the top right corner to copy this repository to your account.

> [!IMPORTANT]
> This site template includes the `analytics.1step.dev` analytics script with `data-website-id` set to `GitHubStarsIndex`. After forking, change it to your own website ID or remove the script at the bottom of `templates/index.html.j2` so your traffic does not appear in the original project dashboard.

### Step 2: Configure Environment (Choose One)

This project is driven by environment variables. **Priority: GitHub Secrets > .env file**.

#### Method A: Using GitHub Environment Variables (Recommended for continuous running)

Go to **Settings → Secrets and variables → Actions** in your repository:

**🔐 Required Secrets/Variables**
- `GH_USERNAME`: The GitHub username whose stars you want to crawl.
- `AI_API_KEY`: Your AI interface API Key.

**📋 Optional Variables**
These have built-in defaults and usually don't need configuration:
- `AI_BASE_URL`: AI API endpoint (defaults to OpenAI).
- `AI_MODEL`: Model name (defaults to `gpt-4o-mini`).
- `OUTPUT_FILENAME`: Base name for generated files (defaults to `stars`).
- `VAULT_SYNC_PATH`: Save directory in your Vault (defaults to `GitHub-Stars/`).
- `PAGES_SYNC_ENABLED`: Whether to sync to Pages (defaults to `true`).

> [!TIP]
> **About GitHub API Limits**:
> - **Running Online (Actions)**: The workflow automatically injects `GITHUB_TOKEN` with a high limit (1,000 requests/hour), easily handling heavy crawls.
> - **Running Locally**: Without a `GH_TOKEN`, the limit is 60 requests/hour. If you have many stars, it's recommended to add a `GH_TOKEN` to your `.env` to increase the limit to 5,000 requests/hour.

#### Method B: Using a .env File (Best for local development)

1. Copy `.env.example` to `.env` in the root directory.
2. Fill in the required fields in `.env`.

---

### Step 3: Customize Schedule Frequency

Edit `.github/workflows/sync.yml` to modify the `cron` expression:

```yaml
schedule:
  - cron: "0 2 * * 1"  # Example: Run every Monday at 2 AM
```

### Step 4: Manually Trigger the First Run

Go to **Actions → 🌟 GitHub Stars Index 同步 → Run workflow** and click run.

---

## Configuration Reference

| Variable             | Type                     | Description                                   | Default Value               |
| -------------------- | ------------------------ | --------------------------------------------- | --------------------------- |
| `GH_USERNAME`        | Required                 | GitHub username to sync                       | -                           |
| `AI_API_KEY`         | Required                 | AI API Key                                    | -                           |
| `AI_BASE_URL`        | Optional                 | OpenAI-compatible API endpoint                | `https://api.openai.com/v1` |
| `AI_MODEL`           | Optional                 | AI model to use                               | `gpt-4o-mini`               |
| `OUTPUT_FILENAME`    | Optional                 | Base name for generated MD/HTML files         | `stars`                     |
| `VAULT_SYNC_ENABLED` | Optional                 | Whether to enable Obsidian sync               | `false`                     |
| `VAULT_REPO`         | Optional                 | Vault repository (`owner/repo`)               | -                           |
| `VAULT_SYNC_PATH`    | Optional                 | Directory path for Vault sync                 | `GitHub-Stars/`             |
| `PAGES_SYNC_ENABLED` | Optional                 | Whether to deploy to GitHub Pages             | `true`                      |
| `MAX_CONCURRENCY`    | Optional                 | AI concurrency limit (recommended 1-10)       | `1`                         |
| `GH_TOKEN`           | **Strongly Recommended** | Increases API limits to prevent rate-limiting | -                           |

---

## Obsidian Sync (Optional)

This feature allows you to automatically push the generated star summaries to your Obsidian Vault (or any other) GitHub repository, keeping your notes updated automatically.

### Core Mechanism
**Cross-repo sync**: Many Obsidian users use GitHub to store and sync their notes. This project uses the GitHub API to push the generated Markdown files directly to your designated Vault repository.

### Setup Steps

1.  **Prepare Target Repository**: Ensure your Obsidian Vault is already hosted on GitHub.
2.  **Create Personal Access Token (PAT)**:
    - Visit the [Fine-grained PAT configuration page](https://github.com/settings/personal-access-tokens).
    - **Repository access**: Choose "Only select repositories" and select your **Vault repository**.
    - **Permissions**: Under "Repository permissions," set **Contents** to **Read and write**.
    - Once generated, add it to this project's **Settings -> Secrets -> Actions** as `VAULT_PAT`.
3.  **Enable Sync Configuration**:
    - In this project's **Settings -> Variables -> Actions**:
        - Set `VAULT_SYNC_ENABLED` to `true`.
        - Set `VAULT_REPO` to `your-username/repo-name` (e.g., `iblogc/my-obsidian-vault`).
        - Set `VAULT_SYNC_PATH` to the desired folder in your Vault (e.g., `Reading/GitHub-Stars/`).
4.  **Save and Finish**: The next time the Action runs, `stars_zh.md` and `stars_en.md` will automatically appear in your Vault repository.

> [!TIP]
> **How to view locally?**
> Once the remote sync is complete, just use the **Obsidian Git** plugin to "Pull," or run `git pull` in your local vault directory. The latest star summaries will then appear in your note library.

---

## GitHub Pages Deployment (Optional)

This project automatically generates multi-language static web pages with real-time search functionality.

1. Ensure `PAGES_SYNC_ENABLED=true`.
2. After running the Action once, go to **Settings -> Pages**.
3. Select `gh-pages` branch and `/(root)` directory, then click Save.

> [!IMPORTANT]
> **Data Source Migration (Compatibility for Forks)**:
> - The current recommended data source is `gh-pages/data/stars.json`.
> - `data/stars.json` in the `main` branch is only used for initial migration compatibility.
> - Normal runs will no longer commit `data/stars.json` back to the `main` branch.

---

## Docker Deployment

If you want to run this long-term on a server with automatic synchronization, Docker Compose is recommended.

### 1. Configuration
Copy `.env.example` to `.env` and fill in the necessary information:
```bash
cp .env.example .env
# Edit .env to fill in GH_USERNAME, AI_API_KEY, and GH_TOKEN
```

> [!IMPORTANT]
> **GH_TOKEN is Mandatory**: In Docker environments, calling the GitHub API without a token easily triggers [Rate Limiting](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api). Configuration increases the limit from 60 to 5,000 requests per hour.

### 2. Start Service
Launch with Docker Compose:
```bash
docker compose up -d
```
This starts two containers:
- `sync`: The core sync script. By default, it runs every **24 hours**. You can adjust this by setting `SCHEDULE_HOURS` in your `.env`.
- `web`: An Nginx-based static server for viewing the generated index.

### 3. Access the Page
Open your browser and visit: `http://localhost:8080`

### 4. Management Commands
```bash
# View sync logs
docker logs -f github-stars-sync

# Run a manual sync immediately
docker compose run --rm sync

# Update page rendering only (skip AI calls)
docker compose run --rm sync --render-only
```

---

## Local Installation

```bash
# Clone the repository and install dependencies
git clone https://github.com/iblogc/GithubStarsIndex.git
cd GithubStarsIndex

# Install dependencies
pip install -r requirements.txt
# Or use uv (recommended)
uv pip install -r requirements.txt

# Configure using .env
cp .env.example .env
# Edit .env and fill in AI_API_KEY and GH_USERNAME

# [Normal Run] Fetch metadata, call AI for summaries, and render pages
python scripts/sync_stars.py
# Or
uv run scripts/sync_stars.py

# [Render Only] Skip fetching/AI, re-render HTML/MD from local stars.json
python scripts/sync_stars.py --render-only
```

---

## File Structure

| File                         | Description                                       |
| :--------------------------- | :------------------------------------------------ |
| `data/stars.json`            | Temporary runtime data (migration entry point)    |
| `templates/`                 | Jinja2 generation templates (Markdown/HTML)       |
| `dist/`                      | Automatically generated local results (HTML / MD) |
| `scripts/sync_stars.py`      | Core sync and generation script                   |
| `scripts/sync_atlas.py`      | Atlas dataset/page builder (reads stars.json only) |
| `scripts/atlas/publish.py`   | Static API shards and RSS generation              |
| `scripts/atlas_mcp.py`       | MCP server (stdio, no third-party deps)           |
| `scripts/atlas/taxonomy.py`  | Atlas taxonomy and scoring engine                 |
| `templates/atlas.html.j2`    | Atlas page template                               |
| `tests/test_atlas.py`        | Classification + artifact contract tests          |
| `.github/workflows/sync.yml` | GitHub Actions scheduled workflow                 |
| `.env.example`               | Configuration example file                        |

---

## Appendix: Applying for a GitHub Token (GH_TOKEN)

To ensure the program can smoothly crawl all your starred repositories, it's recommended to create a Personal Access Token (PAT).

### Steps:
1.  Go to the [GitHub Fine-grained PAT page](https://github.com/settings/personal-access-tokens/new).
2.  **Token name**: `Stars-Index-Sync` (or any name you prefer).
3.  **Expiration**: `90 days` or `Custom` is recommended.
4.  **Resource owner**: Select your personal account.
5.  **Repository access**: Choose `Public Repositories (read-only)` (or `All repositories`).
6.  **Permissions**: No special permissions are required; default public access is enough to fetch your stars list.
7.  Click **Generate token**, then **copy and save** it immediately.
8.  Add this token to the `GH_TOKEN` field in your `.env` file.

> [!TIP]
> If you've enabled **Vault Sync (Obsidian Sync)**, you can reuse the same `VAULT_PAT` (with write permissions) as your `GH_TOKEN`.
