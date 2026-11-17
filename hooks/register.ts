import type { Register } from 'claude-code'

// /sessions opens the dashboard, starting server.py (detached, so it outlives
// this session) when nothing answers on the port yet. /sessions stop shuts it down.
const PORT = 8765
const URL = `http://127.0.0.1:${PORT}`
const LOG = '/tmp/claude-sessions-dashboard.log'

const quote = (s: string) => `'${s.replaceAll("'", `'\\''`)}'`

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'sessions',
      description: 'Open the Claude sessions dashboard (starts it if needed)',
      argumentHint: '[stop]',
      immediate: true,
    })
    return next(e)
  })

  on('command.run', { command: 'sessions' }, async ($, e) => {
    const running = async () => {
      const r = await $.process.run(
        ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '2', `${URL}/api/sessions`],
        { timeoutMs: 5000 },
      ).catch(() => undefined)
      return r?.stdout.trim() === '200'
    }

    if (e.args.trim() === 'stop') {
      await $.process.run(['/bin/sh', '-c', `lsof -ti tcp:${PORT} -sTCP:LISTEN | xargs kill 2>/dev/null; true`])
      return { text: 'Sessions dashboard stopped.' }
    }

    if (await running()) {
      await $.process.run(['open', URL])
      return { text: `Sessions dashboard: ${URL}` }
    }

    // server.py opens the browser itself once it is listening.
    const server = `${$.plugin.root}/server.py`
    await $.process.run([
      '/bin/sh', '-c',
      `nohup python3 ${quote(server)} > ${LOG} 2>&1 < /dev/null &`,
    ])
    for (let i = 0; i < 20; i++) {
      if (await running()) return { text: `Sessions dashboard started: ${URL}` }
      await $.clock.sleep(250)
    }
    return { text: `The dashboard server didn't start. See ${LOG}.` }
  })
}
