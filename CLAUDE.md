# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Install Python dependencies:

```bash
pip install -r backend/requirements.txt
```

Run the local backend and frontend server:

```bash
python backend/app.py
```

The FastAPI app listens on `http://localhost:8765` and serves `index.html` at `/`. On Windows, `start.bat` starts the same backend in a minimized window.

Run a full SSH sync of all configured projects:

```bash
python backend/sync.py
```

Run the LLM integration test script:

```bash
python backend/test_llm.py
```

Run one project sync from Python:

```bash
python -c "from backend.sync import sync_single; sync_single('ecommerce-platform')"
```

There is no package manager config, lint config, or test framework config in this project. `backend/test_llm.py` is a diagnostic script, not a pytest suite.

## Architecture

This is a local personal development dashboard for tracking servers, remote projects, SSH-accessible Markdown project notes, and optional LLM analysis.

`backend/app.py` is the FastAPI application. It serves the single-page frontend, exposes dashboard data from `backend/data/latest.json`, edits `config.json`, triggers sync jobs, and launches local SSH terminals via `/api/ssh-launch`. The backend uses port `8765`; the frontend hardcodes `API_BASE = 'http://localhost:8765'`.

`index.html` is a self-contained SPA with no build step. It contains the UI, demo fallback state, config editing screens, LLM settings UI, and API integration code. On startup it loads `/api/config/full` first to replace the hardcoded demo servers/projects, then loads `/api/data` and merges synced Markdown-derived data into the current project cards.

`config.json` is the source of truth for configured servers, projects, SSH connection details, remote project paths, and LLM settings. Server entries contain SSH host/user/port/key path and projects reference servers by id. Project `remote_path` values are treated as absolute remote paths by the sync engine.

`backend/sync.py` is the SSH sync engine. It uses Paramiko with Ed25519 private keys, discovers `*.md` files at max depth 1 in each remote project directory, always attempts `TODO.md`, `PROGRESS.md`, and `ISSUES.md`, parses known files with specialized parsers, parses other Markdown files generically, optionally runs LLM analysis, and writes `backend/data/latest.json`. Both full sync and `sync_single()` update `backend/data/latest.json`; single-project sync replaces just that project's entry.

`backend/llm_analyzer.py` builds a Chinese project-analysis prompt over remote Markdown contents and can call Anthropic, OpenAI, or Qwen APIs using raw HTTP. It expects a JSON response and merges LLM-derived issues, todos, features, architecture, routes, summary, and `llm_analyzed` fields into sync results.

`templates/` contains the expected remote Markdown conventions for projects: `TODO.md`, `PROGRESS.md`, and `ISSUES.md`. The sync parser understands checkbox tasks, progress lines like `当前进度: 60%`, last update lines, milestones, and issue sections.

`deploy_key.sh` and `deploy_key.ps1` deploy the local `~/.ssh/dev_center` public key to configured servers for passwordless SSH. The PowerShell version reads servers from `config.json`; the shell version contains masked example host values.

## Data flow

1. User edits servers/projects/LLM settings through the SPA or directly in `config.json`.
2. `backend/app.py` persists config changes and triggers syncs on request.
3. `backend/sync.py` connects to remote servers and reads Markdown project files from each configured `remote_path`.
4. Parsed and optionally LLM-enriched results are written to `backend/data/latest.json`.
5. `index.html` fetches `/api/data` and merges todos, progress, issues, sync metadata, and errors into the dashboard UI.

## Current implementation notes

LLM settings support multiple saved configurations through `llm_configs` plus `llm_active_id`; `backend/app.py` keeps the legacy single `llm` object synchronized to the active config for compatibility with older docs and sync logic. The local SPA loads these settings through `/api/config/full` so API keys can be edited locally.

The docs mention `python backend/sync.py --project ecommerce-platform`, but `backend/sync.py` currently has no CLI argument parsing; use the Python one-liner above or `/api/sync/{project_id}` instead.

The backend masks sensitive data in `/api/config`, but `/api/config/full` intentionally returns full local configuration for the SPA, including SSH hosts, users, and key paths.

LLM provider documentation references older default model names. When updating Anthropic integration, prefer current Claude model IDs and keep frontend model labels, docs, and `config.llm.example.json` consistent.
