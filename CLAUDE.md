@AGENTS.md

## Claude Code notes

- This dashboard can see the Claude Code session you're running in (its card is titled after your conversation). Don't close it from the UI.
- To check the UI, use the Chrome tools on `http://127.0.0.1:8765` in a new tab, then close the tab. Toggling grouping or collapsing groups changes the user's saved `csd:*` preferences in that browser, so put them back afterwards.
- To restart the server, run `python3 server.py --no-open` as a background command after killing the old one; the previous background task then reports exit 143, which is expected.
- When adding a field to a session, update `load_sessions()` (server) and the card rendering (`chips` / `context` / `card` in `index.html`) together, and add it to the search filter in `render()` if it's text worth searching.
