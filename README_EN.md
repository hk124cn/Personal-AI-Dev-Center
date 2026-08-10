# Personal AI Dev Center

中文 | **[English](README_EN.md)**

> A local multi-server / multi-project management panel — one console to rule all your development resources: servers, remote projects, AI agents, knowledge base, and personal assets.

![Version](https://img.shields.io/badge/version-1.4.16-blue) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey) ![Stack](https://img.shields.io/badge/Electron%20%2B%20FastAPI%20%2B%20SPA-green)

## Introduction

Personal AI Dev Center is a local desktop console that brings everything under one roof: projects scattered across cloud servers, SSH sync, AI coding agents, project Markdown docs, a knowledge base, and a personal asset ledger.

- **Electron shell + FastAPI backend** (localhost:8765) + **single-file static SPA** (no frontend build step)
- **SSH/Paramiko sync engine**: bidirectional incremental sync over tar-over-SSH; file lists are piped via stdin; downloads are **read-only on the server**
- **Optional LLM analysis** of project Markdown docs (OpenAI-compatible API, multiple saved configs)
- All data stays local (`%APPDATA%/Personal AI Dev Center/`) — nothing is uploaded anywhere

## Screenshots

| Dashboard | Personal Assets |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Assets](docs/screenshots/assets.png) |

| Asset Detail Card | Agent Center | Knowledge Base |
|---|---|---|
| ![Asset detail](docs/screenshots/asset-detail.png) | ![Agents](docs/screenshots/agents.png) | ![Knowledge](docs/screenshots/knowledge.png) |

> All screenshots show the built-in demo dataset (`config.example.json`), not a real environment.

## Features

- **Dashboard**: online servers, active projects, AI tools, and overall progress at a glance
- **Server management**: server cards, parallel TCP-port speed test, one-click SSH terminal
- **Project center**: project cards with progress/priority/tech stack; parses remote `TODO.md` / `PROGRESS.md` / `ISSUES.md`
- **Bidirectional file sync**: incremental download/upload (batched tar-over-SSH, streaming extraction, per-file progress, cancellable; downloads never modify the server)
- **Agent center**: manage AI coding tools and their LLMs (provider/model/key); identical provider+model+key combos are flagged as collisions
- **Knowledge base**: categorized Markdown notes + editor (live preview) + full-text search + JSON export/import
- **Personal assets**: SIM cards / credit cards (incl. prepaid balance) / email accounts / memberships; overview totals; clickable registration sites; **cross-asset foreign-key linking** (email/credit card/membership ↔ phone ↔ email, two-way sync, dropdowns auto-update, dangling references auto-cleared); click a row to see a detail card first, edit from there
- **Domain management**: domain / asset ledger
- **LLM analysis**: AI-generated issues/todos/architecture/summary from project docs during sync

## Architecture

```
┌────────────────────────────────────────────┐
│ Electron shell (electron/main.js)          │
│  └─ spawns FastAPI backend (localhost:8765)│
├────────────────────────────────────────────┤
│ backend/app.py    FastAPI: REST API + SPA  │
│ backend/sync.py   SSH sync engine (Paramiko)│
│ backend/llm_analyzer.py  LLM (raw HTTP)    │
│ index.html        single-file SPA (no build)│
├────────────────────────────────────────────┤
│ config.json       single source of truth   │
│ %APPDATA%/.../    knowledge / runtime data │
└────────────────────────────────────────────┘
```

Data flow: SPA edits config → `config.json` → manual sync → SSH reads remote Markdown → parse (optional LLM enrichment) → `backend/data/latest.json` → SPA renders the dashboard.

## Quick Start

### Option 1: Prebuilt exe (Windows)

Download `Personal-AI-Dev-Center.exe` from Releases and double-click (portable, no install). On first run the demo config is copied to `%APPDATA%/Personal AI Dev Center/config.json` — replace it with your own servers afterwards.

### Option 2: Run from source

```bash
pip install -r backend/requirements.txt
python backend/app.py
# open http://localhost:8765
```

Dev mode (Electron):

```bash
npm install
npm start
```

### Build it yourself

```bash
npm run build        # portable exe → dist/
npm run build:setup  # NSIS installer
```

## Configuration

1. Copy `config.example.json` to `config.json` (or just edit in the UI — it is created automatically)
2. Fill in your servers (host/user/port/SSH key path) and projects (remote_path)
3. Passwordless SSH: deploy your public key with `deploy_key.ps1` / `deploy_key.sh`
4. LLM analysis: set an OpenAI-compatible endpoint (provider/base_url/api_key/model) in Settings; multiple configs supported

**Privacy by design**: the real `config.json` (IPs / keys / asset data) is excluded via `.gitignore` and never enters the repo; the shipped `config.example.json` is a pure mock dataset, and all screenshots are based on it.

## Project Structure

```
├── electron/          Electron shell (main.js / preload.js / icons)
├── backend/
│   ├── app.py         FastAPI app (API + SPA hosting)
│   ├── sync.py        SSH sync engine (incremental up/download, MD parsing)
│   ├── llm_analyzer.py LLM analysis
│   └── requirements.txt
├── index.html         single-file SPA (entire frontend, no build)
├── config.example.json demo config (mock data, safe to share)
├── templates/         remote Markdown conventions (TODO/PROGRESS/ISSUES)
├── docs/              docs & screenshots
└── package.json       electron-builder config
```

## Sync Mechanics

- **Manually triggered**: UI button / `/api/sync` / `/api/sync/{project_id}` — no scheduler
- **Incremental**: mtime+size comparison, only changed files are transferred
- **Download**: one-shot remote `find` scan (read-only) → `tar --null -cf - -T -` (file list via stdin — no ARG_MAX limit, zero writes on the server) → local streaming extraction with per-file progress and cancellation
- **Upload**: local tar streamed up, extracted remotely (file-list preview before upload)
- **Ignore rules**: smart filtering by default (skips `.git` / `node_modules` / `__pycache__` dirs and csv/log/zip/exe extensions); each download/upload lets you pick "Smart Filter" or "All Files" on the spot
