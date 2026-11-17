---
name: sessions-dashboard
description: Open the local Claude sessions dashboard, a web page listing every running Claude session (CLI, background jobs, Claude Desktop) with what each one is about, and buttons to open, attach to, resume or close them. Use when the user asks to see, list, find, switch to, or clean up their running Claude sessions, or asks for the sessions dashboard. macOS only.
---

# Sessions dashboard

The dashboard is `server.py` in this plugin's root folder: two folders up from this skill's base directory (`<base>/../../server.py`). It needs only `python3` (standard library) and runs on macOS.

## Open it

1. Check whether it's already running:
   `curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8765/api/sessions`
2. If that prints `200`, run `open http://127.0.0.1:8765`.
3. Otherwise start it detached, so it keeps running after this session ends. It opens the browser by itself once it's listening:
   `nohup python3 "<plugin root>/server.py" > /tmp/claude-sessions-dashboard.log 2>&1 < /dev/null &`
   Wait about a second, then repeat step 1 to confirm. If it isn't up, show the user the end of `/tmp/claude-sessions-dashboard.log`.

Tell the user the URL. They can also type `/sessions` to open it directly (`/sessions stop` stops it).

## Stop it

`lsof -ti tcp:8765 -sTCP:LISTEN | xargs kill`

## Answering questions about sessions without the page

`python3 -c "import sys; sys.path.insert(0, '<plugin root>'); import server, json; print(json.dumps(server.load_sessions(), indent=2, ensure_ascii=False))"` prints the same data the page shows: name, project, status, the first and latest asks, Claude's last reply, branch, tickets, PRs and files edited. Use it to answer "what is session X doing?" or "which sessions are idle?" in chat.

Never close a session (Close button, `kill`) unless the user asked for that specific one; it ends their work in progress.
