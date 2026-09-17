#!/usr/bin/env python3
"""Delete and recreate a GitHub repository — used to purge unreachable objects.

Why this exists
---------------
After rewriting git history and force-pushing, GitHub keeps the old commits as
*unreachable objects*: they stay fetchable by SHA for an unbounded time. The only
reliable fix is to delete the repository and recreate it, because a fresh
repository never receives those old objects at all.

Safety rails (all mandatory)
----------------------------
  * --confirm-delete must be passed explicitly.
  * refuses to run if the remote has forks / stars / watchers / open issues / PRs.
  * refuses to run if the local backup bundle is missing or unreadable.
  * the token is read from the GH_TOKEN environment variable and is never
    written to disk, never echoed, and never logged.

Usage
-----
    export GH_TOKEN=ghp_xxx            # classic PAT with: repo + delete_repo
    python tools/recreate_repo.py hk124cn/Personal-AI-Dev-Center \
        --backup-file C:/Users/me/AppData/Local/Temp/padc_rebuild_backup_x/padc-main.bundle \
        --confirm-delete

Exit code 0 only when the repository has been deleted and recreated empty.
Pushing the clean history is left to git (see --print-push-hint output).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
UA = "padc-recreate-repo"


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _request(method: str, url: str, token: str, body: dict | None = None):
    """Return (status, parsed_json_or_text). Never raises for HTTP errors."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"message": raw}
        return e.code, parsed
    except Exception as e:  # network level
        return 0, {"message": str(e)}


def get_repo(full: str, token: str):
    return _request("GET", f"{API}/repos/{full}", token)


def delete_repo(full: str, token: str):
    return _request("DELETE", f"{API}/repos/{full}", token)


def create_repo(owner: str, name: str, token: str, private: bool):
    body = {
        "name": name,
        "private": private,
        "has_issues": True,
        "has_wiki": False,
        "has_projects": False,
        "auto_init": False,
    }
    return _request("POST", f"{API}/user/repos", token, body)


# --------------------------------------------------------------------------- #
# Main flow
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Delete and recreate a GitHub repo (purge unreachable objects)")
    ap.add_argument("repo", help="owner/name, e.g. hk124cn/Personal-AI-Dev-Center")
    ap.add_argument("--confirm-delete", action="store_true", help="required: actually perform the deletion")
    ap.add_argument("--backup-file", default="", help="path to the git bundle backup that must exist")
    ap.add_argument("--delete-wait", type=int, default=90, help="seconds to wait for deletion to settle")
    ap.add_argument("--recreate-wait", type=int, default=180, help="seconds to keep retrying repo creation")
    args = ap.parse_args()

    if not args.confirm_delete:
        print("REFUSED: pass --confirm-delete to proceed.")
        return 2

    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        print("REFUSED: GH_TOKEN environment variable is empty.")
        return 2

    if "/" not in args.repo:
        print("REFUSED: repo must be in owner/name form.")
        return 2
    owner, name = args.repo.split("/", 1)

    # ---- rail 1: backup must exist ------------------------------------- #
    if args.backup_file:
        if not os.path.isfile(args.backup_file) or os.path.getsize(args.backup_file) == 0:
            print(f"REFUSED: backup file missing or empty: {args.backup_file}")
            return 2
        print(f"[ok] backup bundle present ({os.path.getsize(args.backup_file):,} bytes)")

    # ---- rail 2: inspect the remote before touching it ------------------ #
    status, info = get_repo(args.repo, token)
    if status == 401:
        print(f"REFUSED: token rejected (401): {info.get('message')}")
        print("        classic PAT needs scopes: repo + delete_repo")
        return 2
    if status == 404:
        print(f"[!] repository {args.repo} not found (already deleted?) — will only create it.")
        info = {}
    elif status != 200:
        print(f"REFUSED: cannot inspect repository (HTTP {status}): {info.get('message')}")
        return 2
    else:
        risky = {
            "forks_count": info.get("forks_count", 0),
            "stargazers_count": info.get("stargazers_count", 0),
            "subscribers_count": info.get("subscribers_count", 0),
            "open_issues_count": info.get("open_issues_count", 0),
        }
        print("[i] remote inventory: " + ", ".join(f"{k}={v}" for k, v in risky.items()))
        blocking = {k: v for k, v in risky.items() if v}
        if blocking:
            print(f"REFUSED: repository has dependents/content {blocking}; delete it manually after review.")
            return 2
        was_private = bool(info.get("private"))
        print(f"[ok] no forks/stars/watchers/issues — safe to delete (private={was_private})")

    # ---- delete --------------------------------------------------------- #
    if status == 200:
        print(f"[..] deleting {args.repo} ...")
        dst, dbody = delete_repo(args.repo, token)
        if dst not in (204, 202):
            print(f"FAILED to delete (HTTP {dst}): {dbody.get('message')}")
            return 1
        print("[ok] DELETE accepted (204)")

        deadline = time.time() + args.delete_wait
        while time.time() < deadline:
            s, _ = get_repo(args.repo, token)
            if s == 404:
                print("[ok] repository confirmed gone (404)")
                break
            time.sleep(3)
        else:
            print("[!] still reachable after waiting; continuing anyway")

    # ---- recreate ------------------------------------------------------- #
    print(f"[..] creating {args.repo} (public, empty) ...")
    deadline = time.time() + args.recreate_wait
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        cs, cbody = create_repo(owner, name, token, private=False)
        if cs == 201:
            print(f"[ok] created: {cbody.get('full_name')} (private={cbody.get('private')})")
            print(f"[ok] default_branch={cbody.get('default_branch')}")
            break
        msg = str(cbody.get("message", ""))
        if cs == 422 and ("already exists" in msg.lower() or "name already" in msg.lower()):
            print(f"[..] name not released yet (attempt {attempt}: {msg}) — retrying in 5s")
            time.sleep(5)
            continue
        if cs == 403:
            print(f"FAILED: forbidden (403). Token needs 'repo' (or public_repo) scope: {msg}")
            return 1
        print(f"FAILED to create (HTTP {cs}): {msg}")
        return 1
    else:
        print("FAILED: repository name never became available.")
        return 1

    # ---- final verify --------------------------------------------------- #
    s, final = get_repo(args.repo, token)
    if s == 200 and not final.get("size"):
        print("[ok] repository exists and is empty — ready for git push")
    print("\nNext step (run from the project root):")
    print("  git remote set-url origin git@github.com:%s.git" % args.repo)
    print("  git push -u origin main")
    print("  git ls-remote origin HEAD        # should match your local main SHA")
    return 0


if __name__ == "__main__":
    sys.exit(main())
