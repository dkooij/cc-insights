"""Parse Claude Code session logs (~/.claude/projects/**/*.jsonl) into cost records.

Every assistant message carries an Anthropic API `usage` block. We deduplicate the
streamed copies of each message (same message id + request id), price the tokens
per model, and roll them up by local day, hour, project, model and session.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
PROJECTS_DIR = CLAUDE_DIR / "projects"
HOME = str(Path.home())

# USD per million tokens: (input, output, cache_read).
# Cache writes are priced off input: 1.25x for 5-minute TTL, 2x for 1-hour TTL.
# Verified against the totalCostUSD that Claude Code itself records (cost-state entries).
PRICES = {
    "claude-fable-5-2": (8.00, 40.00, 0.20),
    "claude-mythos-5-2": (8.00, 40.00, 0.20),
    "claude-fable-5-1": (10.00, 50.00, 1.00),
    "claude-mythos-5-1": (10.00, 50.00, 1.00),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-opus-4-7": (5.00, 25.00, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 0.50),
    "claude-opus-4-5": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30),
    "claude-sonnet-4-5": (3.00, 15.00, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
# Haiku 5.5 is billed at a higher tier once a prompt exceeds 100K tokens.
LONG_PROMPT_TIERS = {"claude-haiku-5-5": (100_000, (0.50, 2.50, 0.05))}
# Fast mode multiplies the standard rate.
FAST_MULTIPLIER = {"claude-opus-5-5": 2.0, "claude-fable-5-2": 2.0, "claude-mythos-5-2": 2.0,
                   "claude-opus-5": 2.0, "claude-opus-4-8": 2.0}
WEB_SEARCH_PER_REQUEST = 0.01

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def model_family(model: str) -> str:
    m = _DATE_SUFFIX.sub("", model or "")
    m = m.split("[")[0]
    if m.startswith("anthropic."):
        m = m[len("anthropic."):]
    return m


def price_for(model: str):
    fam = model_family(model)
    if fam in PRICES:
        return fam, PRICES[fam], True
    # Fallback by tier name so a new release still gets a sensible estimate.
    for key in ("fable", "mythos", "opus", "sonnet", "haiku"):
        if key in fam:
            for k, v in PRICES.items():
                if key in k:
                    return fam, v, False
    return fam, PRICES["claude-sonnet-5-5"], False


def message_cost(model: str, u: dict) -> dict:
    fam, (p_in, p_out, p_read), known = price_for(model)
    inp = u.get("input_tokens") or 0
    out = u.get("output_tokens") or 0
    read = u.get("cache_read_input_tokens") or 0
    write = u.get("cache_creation_input_tokens") or 0
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens")
    w5m = cc.get("ephemeral_5m_input_tokens")
    if w1h is None and w5m is None:
        w5m, w1h = write, 0
    w1h = w1h or 0
    w5m = w5m or 0

    tier = LONG_PROMPT_TIERS.get(fam)
    if tier and inp + read + write > tier[0]:
        p_in, p_out, p_read = tier[1]

    mult = FAST_MULTIPLIER.get(fam, 1.0) if u.get("speed") == "fast" else 1.0
    c_in = inp * p_in / 1e6 * mult
    c_out = out * p_out / 1e6 * mult
    c_read = read * p_read / 1e6 * mult
    c_write = (w5m * 1.25 + w1h * 2.0) * p_in / 1e6 * mult
    stu = u.get("server_tool_use") or {}
    c_web = (stu.get("web_search_requests") or 0) * WEB_SEARCH_PER_REQUEST
    return {
        "in": inp, "out": out, "cr": read, "cw": w5m + w1h,
        "c_in": c_in, "c_out": c_out, "c_cr": c_read, "c_cw": c_write + c_web,
        "cost": c_in + c_out + c_read + c_write + c_web,
        "known": known, "fast": mult > 1,
    }


def project_from_cwd(cwd: str, fallback_dir: str) -> tuple[str, str]:
    """Return (project key path, display name)."""
    if not cwd:
        cwd = "/" + fallback_dir.lstrip("-").replace("-", "/")
    path = cwd
    for marker in ("/.claude/worktrees/", "/.worktrees/", "/.git/worktrees/"):
        if marker in path:
            path = path.split(marker)[0]
    if "/Application Support/Claude/scratch-workspaces" in path or "/claude-scratch" in path:
        return "scratch", "Scratch (desktop app)"
    if path.rstrip("/") == HOME:
        return path, "~ (home)"
    name = os.path.basename(path.rstrip("/")) or path
    return path, name


# ----------------------------------------------------------------- file cache
_file_cache: dict[str, tuple[tuple[float, int], dict]] = {}


def _parse_file(path: Path, project_dir: str) -> dict:
    msgs = {}
    titles = {}
    for line in path.open("r", encoding="utf-8", errors="ignore"):
        if '"usage"' in line:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            m = d.get("message") or {}
            u = m.get("usage")
            model = m.get("model") or ""
            if not u or model.startswith("<"):
                continue
            key = (m.get("id"), d.get("requestId"))
            prev = msgs.get(key)
            if prev and (prev["u"].get("output_tokens") or 0) >= (u.get("output_tokens") or 0):
                continue
            msgs[key] = {
                "ts": d.get("timestamp"),
                "model": model,
                "u": u,
                "cwd": d.get("cwd") or "",
                "session": d.get("sessionId") or path.stem,
                "sidechain": bool(d.get("isSidechain")) or "subagents" in path.parts,
                "branch": d.get("gitBranch") or "",
            }
        elif '"custom-title"' in line or '"agent-name"' in line:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("customTitle") or d.get("agentName")
            if t and d.get("sessionId"):
                titles[d["sessionId"]] = t
    return {"msgs": msgs, "titles": titles, "project_dir": project_dir}


def _iter_files():
    if not PROJECTS_DIR.exists():
        return
    for proj in PROJECTS_DIR.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.rglob("*.jsonl"):
            yield f, proj.name


def collect() -> dict:
    seen_files = set()
    all_msgs: dict = {}
    titles: dict = {}
    for f, proj_dir in _iter_files():
        key = str(f)
        seen_files.add(key)
        try:
            st = f.stat()
        except OSError:
            continue
        sig = (st.st_mtime, st.st_size)
        cached = _file_cache.get(key)
        if not cached or cached[0] != sig:
            cached = (sig, _parse_file(f, proj_dir))
            _file_cache[key] = cached
        parsed = cached[1]
        titles.update(parsed["titles"])
        for k, rec in parsed["msgs"].items():
            prev = all_msgs.get(k)
            if prev and (prev[0]["u"].get("output_tokens") or 0) >= (rec["u"].get("output_tokens") or 0):
                continue
            all_msgs[k] = (rec, parsed["project_dir"])
    for k in list(_file_cache):
        if k not in seen_files:
            del _file_cache[k]

    # A project is the folder Claude Code was launched in (one dir under ~/.claude/projects).
    # Recover its real path from the cwds seen there; the folder name itself is lossy.
    folder_cwds = defaultdict(set)
    for rec, proj_dir in all_msgs.values():
        if rec["cwd"]:
            folder_cwds[proj_dir].add(rec["cwd"])
    folder_path = {}
    for proj_dir, cwds in folder_cwds.items():
        exact = [c for c in cwds if re.sub(r"[^A-Za-z0-9]", "-", c) == proj_dir]
        folder_path[proj_dir] = exact[0] if exact else min(cwds, key=len)

    rows = defaultdict(lambda: [0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0])
    sessions: dict = {}
    projects: dict = {}
    unknown_models = set()
    newest = None

    for rec, proj_dir in all_msgs.values():
        ts = rec["ts"]
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        except ValueError:
            continue
        pkey, pname = project_from_cwd(folder_path.get(proj_dir, ""), proj_dir)
        projects.setdefault(pkey, {"key": pkey, "name": pname})
        fam = model_family(rec["model"])
        c = message_cost(rec["model"], rec["u"])
        if not c["known"]:
            unknown_models.add(fam)
        day = dt.strftime("%Y-%m-%d")
        sid = rec["session"]
        r = rows[(day, dt.hour, pkey, fam, sid)]
        r[0] += 1
        r[1] += c["in"]
        r[2] += c["out"]
        r[3] += c["cr"]
        r[4] += c["cw"]
        r[5] += c["cost"]
        r[6] += c["c_in"]
        r[7] += c["c_out"]
        r[8] += c["c_cr"]
        r[9] += c["c_cw"]
        s = sessions.get(sid)
        iso = dt.isoformat()
        if not s:
            s = sessions[sid] = {"id": sid, "project": pkey, "first": iso, "last": iso,
                                 "branch": rec["branch"], "title": None}
        s["first"] = min(s["first"], iso)
        s["last"] = max(s["last"], iso)
        if not rec["sidechain"] and rec["branch"]:
            s["branch"] = rec["branch"]
        if newest is None or dt > newest:
            newest = dt

    for sid, s in sessions.items():
        s["title"] = titles.get(sid)

    # Disambiguate projects that share a display name.
    by_name = defaultdict(list)
    for p in projects.values():
        by_name[p["name"]].append(p)
    for name, ps in by_name.items():
        if len(ps) > 1:
            for p in ps:
                parent = os.path.basename(os.path.dirname(p["key"].rstrip("/")))
                p["name"] = f"{parent}/{name}"

    out_rows = [
        [day, hour, pkey, fam, sid, r[0], r[1], r[2], r[3], r[4],
         round(r[5], 6), round(r[6], 6), round(r[7], 6), round(r[8], 6), round(r[9], 6)]
        for (day, hour, pkey, fam, sid), r in rows.items()
    ]
    out_rows.sort()
    return {
        "generated": datetime.now().astimezone().isoformat(),
        "newest": newest.isoformat() if newest else None,
        "source": str(PROJECTS_DIR),
        "files": len(seen_files),
        "messages": len(all_msgs),
        "columns": ["day", "hour", "project", "model", "session", "msgs", "in", "out",
                    "cache_read", "cache_write", "cost", "c_in", "c_out", "c_cr", "c_cw"],
        "rows": out_rows,
        "projects": list(projects.values()),
        "sessions": list(sessions.values()),
        "unknown_models": sorted(unknown_models),
        "prices": {k: {"input": v[0], "output": v[1], "cache_read": v[2]} for k, v in PRICES.items()},
    }


if __name__ == "__main__":
    import time
    t = time.time()
    d = collect()
    total = sum(r[10] for r in d["rows"])
    print(f"{d['files']} files, {d['messages']} messages, {len(d['rows'])} rows, "
          f"${total:,.2f} total, {time.time() - t:.2f}s")
    t = time.time()
    collect()
    print(f"warm re-scan {time.time() - t:.2f}s")
