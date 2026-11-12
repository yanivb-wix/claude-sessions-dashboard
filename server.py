#!/usr/bin/env python3
"""Local dashboard for running Claude sessions.

Run:  python3 server.py   (then open http://127.0.0.1:8765)
Reads ~/.claude/sessions/*.json and the session transcripts; stdlib only.
"""
import json
import re
import os
import secrets
import shlex
import signal
import subprocess
import sys
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path.home()
SESSIONS_DIR = HOME / ".claude" / "sessions"
PROJECTS_DIR = HOME / ".claude" / "projects"
HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8765"))
TOKEN = secrets.token_hex(16)  # guards the POST endpoints against other sites
TAIL_BYTES = 3_000_000
ORCA_BIN = "/Applications/Orca.app/Contents/Resources/bin/orca"
ATTACH_TABS = HERE / "attach-tabs.json"  # jobId -> Orca tab opened by Attach
ORCA_HOOKS = HOME / "Library" / "Application Support" / "orca" / "agent-hooks" / "last-status.json"


def ps_info(pid):
    """Return (tty, command) for a live pid, or None."""
    try:
        out = subprocess.run(["ps", "-o", "tty=,command=", "-p", str(pid)],
                             capture_output=True, text=True).stdout.strip()
    except Exception:
        return None
    if not out:
        return None
    tty, _, cmd = out.partition(" ")
    return tty.strip(), cmd.strip()


def host_app(pid):
    """Walk up the process tree to the first .app bundle (the terminal hosting the session)."""
    seen = 0
    while pid and pid > 1 and seen < 30:
        seen += 1
        out = subprocess.run(["ps", "-o", "ppid=,command=", "-p", str(pid)],
                             capture_output=True, text=True).stdout.strip()
        if not out:
            return None
        ppid, _, cmd = out.partition(" ")
        if ".app/Contents/MacOS/" in cmd and "Helper" not in cmd and "claude.app" not in cmd:
            return cmd.split(".app/Contents/MacOS/")[0] + ".app"
        pid = int(ppid)
    return None


def transcript_path(cwd, session_id):
    slug = "".join(c if c.isalnum() else "-" for c in cwd)
    p = PROJECTS_DIR / slug / f"{session_id}.jsonl"
    if p.exists():
        return p
    hits = list(PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


PR_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{2,6}\b")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_summary_cache = {}


PASTE_RE = re.compile(r"<pasted_content[^>]*>.*?(</pasted_content>|$)", re.S)


def clip(t, n):
    t = " ".join(PASTE_RE.sub(" [pasted text] ", t).split())
    return t if len(t) <= n else t[:n].rstrip() + "…"


def is_real_prompt(t):
    return t and not t.startswith(("<", "This session is being continued", "[Request interrupted"))


def summarize(path, cwd=""):
    """Pull reminders out of a transcript: title, asks, last reply, branch, files, PRs, activity."""
    empty = {"title": None, "first": None, "last": None, "reply": None, "mtime": None,
             "asks": [], "branch": None, "files": [], "prs": [], "tickets": [],
             "turns": 0, "toolCalls": 0, "topTools": []}
    if not path:
        return empty
    st = path.stat()
    key = (str(path), st.st_mtime, st.st_size)
    if _summary_cache.get(str(path), (None,))[0] == key:
        return _summary_cache[str(path)][1]
    res = dict(empty, mtime=st.st_mtime * 1000)
    try:
        with open(path, "rb") as f:
            head = f.read(min(st.st_size, 400_000)).decode("utf-8", "ignore").splitlines()
            f.seek(max(0, st.st_size - TAIL_BYTES))
            raw_tail = f.read().decode("utf-8", "ignore")
    except OSError:
        return res

    def records(lines):
        for line in lines:
            try:
                yield json.loads(line)
            except ValueError:
                continue

    for d in records(head):
        if d.get("type") == "user" and not d.get("isMeta"):
            t = text_of(d.get("message", {}).get("content")).strip()
            if is_real_prompt(t):
                res["first"] = clip(t, 300)
                break

    asks, files, tools, tickets, prs = [], {}, Counter(), Counter(), []
    for d in records(raw_tail.splitlines()):
        typ = d.get("type")
        res["branch"] = d.get("gitBranch") or res["branch"]
        if typ == "ai-title":
            res["title"] = d.get("aiTitle")
        elif typ == "custom-title":
            res["title"] = d.get("customTitle") or res["title"]
        elif typ == "user" and not d.get("isMeta"):
            t = text_of(d.get("message", {}).get("content")).strip()
            if is_real_prompt(t):
                res["turns"] += 1
                if not asks or asks[-1] != t:
                    asks.append(t)
                tickets.update(TICKET_RE.findall(t))
                prs.extend(PR_RE.findall(t))
        elif typ == "assistant":
            content = d.get("message", {}).get("content")
            t = text_of(content).strip()
            if t:
                res["reply"] = t
                prs.extend(PR_RE.findall(t))
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tools[b.get("name", "?")] += 1
                    fp = (b.get("input") or {}).get("file_path") or (b.get("input") or {}).get("notebook_path")
                    if b.get("name") in EDIT_TOOLS and fp:
                        files.pop(fp, None)
                        files[fp] = True  # keep most-recent order

    if res["branch"]:
        tickets.update(TICKET_RE.findall(res["branch"]))
    res["asks"] = [clip(a, 220) for a in asks[-4:]][::-1]
    res["last"] = res["asks"][0] if res["asks"] else None
    res["reply"] = clip(res["reply"], 500) if res["reply"] else None
    prefix = cwd.rstrip("/") + "/"
    res["files"] = [f[len(prefix):] if f.startswith(prefix) else f.replace(str(HOME), "~")
                    for f in list(files)[::-1][:8]]
    res["prs"] = list(dict.fromkeys(reversed(prs)))[:4]  # only PRs you or Claude mentioned, newest first
    res["tickets"] = [t for t, _ in tickets.most_common(4)]
    res["toolCalls"] = sum(tools.values())
    res["topTools"] = [[n.replace("mcp__claude_ai_", "").replace("mcp__", ""), c] for n, c in tools.most_common(5)]
    _summary_cache[str(path)] = (key, res)
    return res


def project_of(cwd, entrypoint):
    if "/Library/Application Support/Claude/scratch-workspaces/" in cwd:
        return "Claude Desktop · scratch"
    home = str(HOME)
    rel = cwd[len(home):].lstrip("/") if cwd.startswith(home) else cwd
    return "~/" + rel if rel else "~"


def load_sessions():
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            s = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        pid = s.get("pid")
        info = ps_info(pid)
        alive = bool(info and "claude" in info[1].lower())
        tty = info[0] if alive and info[0] not in ("??", "") else None
        summ = summarize(transcript_path(s.get("cwd", ""), s.get("sessionId", "")), s.get("cwd", ""))
        sessions.append({
            "pid": pid,
            "sessionId": s.get("sessionId"),
            "name": s.get("name") or summ["title"] or s.get("sessionId", "")[:8],
            "title": summ["title"],
            "cwd": s.get("cwd", ""),
            "project": project_of(s.get("cwd", ""), s.get("entrypoint")),
            "kind": s.get("kind"),
            "entrypoint": s.get("entrypoint"),
            "status": s.get("status") or "unknown",
            "version": s.get("version"),
            "hostSessionId": s.get("hostSessionId"),
            "jobId": s.get("jobId"),
            "parkedJobId": s.get("parkedJobId"),
            "startedAt": s.get("startedAt"),
            "updatedAt": max(filter(None, [s.get("updatedAt"), summ["mtime"]]), default=None),
            "alive": alive,
            "tty": tty,
            "firstPrompt": summ["first"],
            "lastPrompt": summ["last"],
            "lastReply": summ["reply"],
            "asks": summ["asks"],
            "branch": summ["branch"],
            "files": summ["files"],
            "prs": summ["prs"],
            "tickets": summ["tickets"],
            "turns": summ["turns"],
            "toolCalls": summ["toolCalls"],
            "topTools": summ["topTools"],
        })
    for s in sessions:
        # A bg job nobody has parked in a terminal lives only inside the Claude daemon.
        s["headless"] = s["alive"] and s["kind"] == "bg" and not any(
            o.get("parkedJobId") == s["jobId"] for o in sessions)
    if any(s["headless"] for s in sessions):
        tabs, live = load_attach_tabs(), orca_handles()
        for s in sessions:
            s["attachTab"] = s["headless"] and tabs.get(s["jobId"]) in live
    return sessions


def find(pid):
    return next((s for s in load_sessions() if s["pid"] == pid), None)


def osascript(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True)


def orca_handle(s):
    """Map a session to its Orca terminal handle via Orca's agent-hook pane registry."""
    if not os.path.exists(ORCA_BIN):
        return None
    try:
        entries = json.loads(ORCA_HOOKS.read_text()).get("entries", {})
    except (OSError, ValueError):
        return None
    pane = next((e for e in entries.values()
                 if (e.get("agentPresence") or {}).get("process", {}).get("pid") == s["pid"]), None)
    pane = pane or next((e for e in entries.values()
                         if (e.get("providerSession") or {}).get("id") == s["sessionId"]), None)
    if not pane or ":" not in pane.get("paneKey", ""):
        return None
    tab_id, leaf_id = pane["paneKey"].split(":", 1)
    out = subprocess.run([ORCA_BIN, "terminal", "list", "--json"], capture_output=True, text=True).stdout
    try:
        terms = json.loads(out)["result"]["terminals"]
    except (ValueError, KeyError, TypeError):
        return None
    t = next((t for t in terms if t.get("tabId") == tab_id and t.get("leafId") == leaf_id), None)
    return t and t.get("handle")


def open_session(s):
    if s["alive"] and s["entrypoint"] == "claude-desktop":
        if s.get("hostSessionId"):
            subprocess.run(["open", f"claude://code/continue?session={s['hostSessionId']}&source=dashboard"])
            return "Opened conversation in Claude Desktop"
        subprocess.run(["open", "-a", "Claude"])
        return "Focused Claude Desktop"
    handle = s["alive"] and orca_handle(s)
    if s["alive"] and not handle and s.get("jobId"):
        # Background job: open the terminal session it was parked from.
        owner = next((o for o in load_sessions() if o.get("parkedJobId") == s["jobId"]), None)
        handle = owner and orca_handle(owner)
    if handle:
        subprocess.run([ORCA_BIN, "terminal", "switch", "--terminal", handle], capture_output=True)
        subprocess.run(["open", "-a", "Orca"])
        return "Switched to Orca tab"
    app = s["alive"] and s["tty"] and host_app(s["pid"])
    if app:
        dev = "/dev/" + s["tty"]
        name = Path(app).stem
        if name == "Terminal":
            osascript(f'''tell application "Terminal"
  activate
  repeat with w in windows
    repeat with t in tabs of w
      if tty of t is "{dev}" then
        set selected of t to true
        set index of w to 1
      end if
    end repeat
  end repeat
end tell''')
        elif name in ("iTerm", "iTerm2"):
            osascript(f'''tell application "iTerm"
  activate
  repeat with w in windows
    repeat with t in tabs of w
      repeat with ss in sessions of t
        if tty of ss is "{dev}" then
          select w
          select t
          select ss
        end if
      end repeat
    end repeat
  end repeat
end tell''')
        else:
            subprocess.run(["open", "-a", app])
        return f"Focused {name} ({s['tty']})"
    if s["alive"] and s.get("jobId"):
        # Headless background job (runs inside the Claude daemon, no window): reuse the tab
        # a previous Attach opened, or attach to it in a new one.
        cmd = f"claude attach {shlex.quote(s['jobId'])}"
        tabs = load_attach_tabs()
        handle = tabs.get(s["jobId"])
        if handle and handle in orca_handles():
            if not attach_running(s["jobId"]):  # tab is back at the shell: re-attach in place
                subprocess.run([ORCA_BIN, "terminal", "send", "--terminal", handle, "--text", cmd, "--enter"],
                               capture_output=True)
            subprocess.run([ORCA_BIN, "terminal", "switch", "--terminal", handle], capture_output=True)
            subprocess.run(["open", "-a", "Orca"])
            return "Switched to the attached Orca tab"
        where, handle = launch_in_terminal(s["cwd"], cmd, s["name"])
        if handle:
            tabs[s["jobId"]] = handle
            save_attach_tabs(tabs)
        return f"Attached to background session in a new {where} tab"
    if s["alive"]:
        return "Session is running but has no window to focus"
    where, _ = launch_in_terminal(s["cwd"], f"claude --resume {shlex.quote(s['sessionId'])}", s["name"])
    return f"Resumed in a new {where} tab"


def load_attach_tabs():
    try:
        return json.loads(ATTACH_TABS.read_text())
    except (OSError, ValueError):
        return {}


def save_attach_tabs(tabs):
    live = orca_handles()
    ATTACH_TABS.write_text(json.dumps({j: h for j, h in tabs.items() if h in live}, indent=2))


def orca_handles():
    out = subprocess.run([ORCA_BIN, "terminal", "list", "--json"], capture_output=True, text=True).stdout
    try:
        return {t["handle"] for t in json.loads(out)["result"]["terminals"]}
    except (ValueError, KeyError, TypeError):
        return set()


def attach_running(job_id):
    out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
    return any(line.split()[:3] == ["claude", "attach", job_id] for line in out.splitlines())


def find_handle(obj):
    """Pull the terminal handle out of `orca terminal create --json` output, whatever its nesting."""
    if isinstance(obj, dict):
        if isinstance(obj.get("handle"), str):
            return obj["handle"]
        obj = list(obj.values())
    if isinstance(obj, list):
        return next(filter(None, map(find_handle, obj)), None)
    return None


def launch_in_terminal(cwd, cmd, title):
    """Run cmd in a new Orca tab (preferred), falling back to a Terminal.app window.
    Returns (app used, Orca terminal handle or None)."""
    if os.path.exists(ORCA_BIN):
        r = subprocess.run([ORCA_BIN, "terminal", "create", "--worktree", f"path:{cwd}", "--title", title,
                            "--command", f"cd {shlex.quote(cwd)} && {cmd}", "--focus", "--json"],
                           capture_output=True, text=True)
        try:
            res = json.loads(r.stdout)
        except ValueError:
            res = {}
        if res.get("ok"):
            subprocess.run(["open", "-a", "Orca"])
            return "Orca", find_handle(res.get("result"))
    osascript(f'tell application "Terminal"\n activate\n do script {json.dumps(f"cd {shlex.quote(cwd)} && {cmd}")}\nend tell')
    return "Terminal", None


def close_session(s):
    if not s["alive"]:
        (SESSIONS_DIR / f"{s['pid']}.json").unlink(missing_ok=True)
        return "Removed stale entry"
    os.kill(s["pid"], signal.SIGTERM)
    for _ in range(20):
        time.sleep(0.1)
        if not ps_info(s["pid"]):
            return "Closed"
    return "Sent SIGTERM (still shutting down)"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (HERE / "index.html").read_text().replace("__TOKEN__", TOKEN)
            self.send(200, html, "text/html; charset=utf-8")
        elif self.path == "/api/sessions":
            self.send(200, json.dumps(load_sessions()))
        else:
            self.send(404, '{"error":"not found"}')

    def do_POST(self):
        if self.headers.get("X-Token") != TOKEN:
            return self.send(403, '{"error":"bad token"}')
        parts = self.path.strip("/").split("/")  # api/<action>/<pid>
        if len(parts) != 3 or parts[0] != "api" or not parts[2].isdigit():
            return self.send(404, '{"error":"not found"}')
        s = find(int(parts[2]))
        if not s:
            return self.send(404, '{"error":"session not found"}')
        try:
            if parts[1] == "open":
                msg = open_session(s)
            elif parts[1] == "close":
                msg = close_session(s)
            else:
                return self.send(404, '{"error":"unknown action"}')
        except Exception as e:  # surface failures to the page
            return self.send(500, json.dumps({"error": str(e)}))
        self.send(200, json.dumps({"ok": True, "message": msg}))


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Claude sessions dashboard → {url}")
    if "--no-open" not in sys.argv:
        subprocess.Popen(["open", url])
    srv.serve_forever()
