# AGENTS.md

Guidance for AI coding agents working on this repo. See `README.md` for what the dashboard does from a user's point of view.

## Project in one paragraph

A local, macOS-only web dashboard for every Claude session running on this machine. `server.py` (Python 3 stdlib HTTP server on `127.0.0.1:8765`) discovers sessions, summarizes their transcripts and performs actions (open, attach, resume, close). `index.html` is a single self-contained page (inline CSS and JS, no build step, no dependencies) that polls `/api/sessions` and renders cards. Keep it that way: **no pip or npm dependencies, no bundler, no frameworks.**

## Layout

| File | Role |
|---|---|
| `server.py` | Session discovery (`load_sessions`), transcript parsing (`summarize`), Orca/Desktop/terminal integration (`orca_handle`, `open_session`, `launch_in_terminal`), `close_session`, HTTP handler |
| `index.html` | UI. `server.py` serves it with `__TOKEN__` replaced by the per-run CSRF token |
| `demo/sessions.json` | Made-up sessions served by `--demo`. Times are `startedAgoMin`/`activeAgoMin`, converted to timestamps at request time |
| `docs/*.png` | README screenshots, captured from demo mode (see below) |
| `attach-tabs.json` | Runtime state: `{jobId: orcaTerminalHandle}` for tabs opened by Attach. Git-ignored, never commit it |

## Running and checking changes

```sh
# Restart the server (it runs in the foreground; kill any old instance first)
lsof -ti tcp:8765 | xargs kill 2>/dev/null; python3 server.py --no-open

# Inspect the data the UI gets, without the server
python3 -c "import server, json; print(json.dumps(server.load_sessions()[:2], indent=2, ensure_ascii=False))"

# Syntax-check the page script
python3 -c "import re;open('/tmp/page.js','w').write(re.search(r'<script>(.*)</script>',open('index.html').read(),re.S).group(1))" && node --check /tmp/page.js
```

- **Demo mode** (`python3 server.py --demo`) serves `demo/sessions.json`, and Open/Close only return a toast. Use it for UI work and screenshots. **Never put real session content (internal tickets, site IDs, transcripts) in screenshots or the demo data.**
- Regenerate the README screenshots from a demo server (`PORT=8790 python3 server.py --demo --no-open`). The `?group=` URL parameter picks the grouping:
  ```sh
  C="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  shot() { "$C" --headless=new --user-data-dir="/tmp/csd-shot-$1" --hide-scrollbars --force-device-scale-factor=2 \
    --window-size=1440,1180 --virtual-time-budget=4000 $3 --screenshot="docs/$1.png" "http://127.0.0.1:8790/$2"; }
  shot dashboard-light "?group=project"
  shot dashboard-dark  "?group=project" --blink-settings=preferredColorScheme=0
  shot dashboard-status "?group=status"
  ```
- There is no test suite. Verify by importing `server` and calling its functions, and by loading the page in a browser.
- **The server must be restarted after editing `server.py`.** `index.html` is re-read on every request, so a page reload is enough for UI changes. The token changes on every restart, so an old open tab gets `403` on actions until it is reloaded.
- **Do not click Open, Attach or Close on real sessions while testing.** Open steals focus or creates Orca tabs in the user's workspace, and Close kills the user's live sessions. Test the logic by calling the helpers (`orca_handle(s)`, `attach_running(job)` and so on) instead.

## Data sources (all local, all read-only except `attach-tabs.json`)

- **`~/.claude/sessions/<pid>.json`**: Claude Code's live-session registry, one file per process. Useful fields: `pid`, `sessionId`, `cwd`, `name`, `kind` (`interactive` | `bg`), `entrypoint` (`cli` | `claude-desktop`), `status` (`idle` | `busy` …), `startedAt` and `updatedAt` (ms), `hostSessionId` (Desktop, `local_…`), `jobId` (bg jobs), `parkedJobId` (an interactive session that has a bg job parked in it). Files can be stale, so always check that the pid is alive and is really `claude` (`ps_info`).
- **`~/.claude/projects/<cwd-slug>/<sessionId>.jsonl`**: the transcript. The slug is the cwd with every non-alphanumeric character replaced by `-`. Record types used: `user`, `assistant` (`message.content` is a string or a list of blocks, and `tool_use` blocks carry `name` and `input.file_path`), `ai-title`, `custom-title`, plus `gitBranch` on most records. Files can be tens of MB: only the first 400 KB and the last `TAIL_BYTES` (3 MB) are read, and results are cached by `(path, mtime, size)`.
- **Orca agent-hook registry**, `~/Library/Application Support/orca/agent-hooks/last-status.json`: `entries[paneKey]` holds `agentPresence.process.pid` and `providerSession.id`. `paneKey` is `"<tabId>:<leafId>"`, which matches `tabId`/`leafId` in `orca terminal list --json` and so gives the terminal `handle`.

## Integrations and how they behave

- **Orca CLI** (`/Applications/Orca.app/Contents/Resources/bin/orca`): `terminal list --json`, `terminal switch --terminal <h>`, `terminal create --worktree path:<cwd> --command … --focus --json`, `terminal send --terminal <h> --text … --enter`. Orca has no deep link to a tab; its `orca://` scheme only handles pairing and skills.
- **Claude Desktop deep link**: `claude://code/continue?session=<hostSessionId>` opens that exact chat (the app checks for `^local_…`). `claude://resume?session=<cli uuid>` imports a CLI session into Desktop (not used yet).
- **Background jobs**: these run under the Claude daemon (`claude daemon run` → `claude bg-pty-host` → `claude bg-spare`) and have no terminal window. A job with no session whose `parkedJobId` points to it is **headless**: Open runs `claude attach <jobId>` in a new Orca tab, saves the handle in `attach-tabs.json`, and on later clicks switches to that tab (re-sending the attach command if `attach_running` is false). Forked jobs show `⑂` in their name.
- **Terminal.app and iTerm2**: an AppleScript selects the tab whose `tty` matches. **ymux** and other apps have no scripting, so they can only be brought to the front with `open -a`.

## Pitfalls already hit (don't re-learn these)

- `ps eww` does **not** show another process's environment on macOS, so you can't read `ORCA_TERMINAL_HANDLE` from a session process. Use the Orca hook registry instead.
- A tty alone doesn't mean there's a window: daemon-hosted jobs have ttys too. Only report "Focused …" when `host_app()` found a `.app`.
- PR links: only take them from user and assistant **text**, never from raw tool output. Otherwise every session that ran `gh pr list` or read a registry shows unrelated PRs.
- Skip synthetic "user" records: anything starting with `<` (command wrappers, reminders), "This session is being continued…" and "[Request interrupted". Replace `<pasted_content …>…</pasted_content>` with `[pasted text]` (`PASTE_RE`, `clip`).
- The git branch `HEAD` means not a git repo or a detached HEAD; the UI hides `HEAD`, `master` and `main`.

## UI conventions (`index.html`)

- **Colors**: all colors are CSS custom properties on `:root`, with a dark-mode override under `prefers-color-scheme: dark`. Add a token rather than a literal color.
- **Escaping**: every interpolated string goes through `esc()`. Transcript text is untrusted (it contains pasted web content).
- **Re-rendering**: `load()` re-renders only when the `/api/sessions` JSON changes (plus a forced render every 60 s to refresh "x ago"), so animations don't replay on every 10 s poll. The `seen` set means only new cards get the `enter` animation. UI state that must survive a re-render is captured before `innerHTML` and restored after: open `<details>`, expanded replies, the `confirming` set.
- **Persistence**: view preferences live in `localStorage` under `csd:*` (`csd:mode`, `csd:collapsed`), always through the `store` wrapper (try/catch).
- **Grouping**: `groupsFor()` handles the `project`, `status`, `app` and `none` modes. The collapsed-group key is `"<mode>:<group name>"`.
- **Status**: one source of truth, `statusOf(s)`, which returns `busy`, `idle`, `stale` (idle 3+ days) or `ended`. The `STATUS` map supplies label, color and sort order. Card classes are `st-<status>`.
- **Dialogs**: no `alert`/`confirm`. Close uses the in-card `.confirm` overlay; feedback goes through `toast()`.
- **Motion**: animations use `var(--ease)`, and `prefers-reduced-motion` turns them off globally. Keep it that way.
- **Text**: add `dir="auto"` to any element showing user or transcript text (Hebrew and other RTL content is common).

## Security rules

- Bind to `127.0.0.1` only.
- Every POST must check `X-Token` against the per-run `TOKEN`.
- Actions look the session up again server-side (`find(pid)`), so the client can't target arbitrary pids.
- Quote every value passed to a shell or AppleScript with `shlex.quote` / `json.dumps`.

## Commits

Small, focused commits with an imperative subject line. Never commit `attach-tabs.json`, transcripts or anything from `~/.claude`.
