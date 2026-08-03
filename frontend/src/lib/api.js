async function req(path, opts) {
  const res = await fetch(path, opts)
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not json */ }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.json()
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  me: () => req('/api/auth/me'),
  register: (email, password) => req('/api/auth/register', json({ email, password })),
  login: (email, password) => req('/api/auth/login', json({ email, password })),
  logout: () => req('/api/auth/logout', { method: 'POST' }),
  changePassword: (current, next) => req('/api/auth/password', json({ current, new: next })),
  characters: () => req('/api/characters'),
  addCharacter: (name) => req('/api/characters', json({ name })),
  deleteCharacter: (id) => req(`/api/characters/${id}`, { method: 'DELETE' }),
  tokens: (charId) => req(`/api/characters/${charId}/tokens`),
  mintToken: (charId, label) => req(`/api/characters/${charId}/tokens`, json({ label })),
  revokeToken: (id) => req(`/api/tokens/${id}/revoke`, { method: 'POST' }),
  census: (charId) => req(`/api/characters/${charId}/census`),
  censusRefresh: (charId) => req(`/api/characters/${charId}/census/refresh`, { method: 'POST' }),
  censusSnapshots: (charId) => req(`/api/characters/${charId}/census/snapshots`),
  censusDiff: (charId, snapId) => req(`/api/characters/${charId}/census/snapshots/${snapId}/diff`),
  spell: (id) => req(`/api/spells/${id}`),
  sessions: () => req('/api/sessions'),
  session: (id) => req(`/api/sessions/${id}`),
  zoneRuns: () => req('/api/zone-runs'),
  zoneRun: (id) => req(`/api/zone-runs/${id}`),
  zoneRunReport: (id) => req(`/api/zone-runs/${id}/report`),
  coach: (sessionId) => req(`/api/sessions/${sessionId}/coach`),
  generateCoach: (sessionId) => req(`/api/sessions/${sessionId}/coach`, { method: 'POST' }),
  reparse: (id) => req(`/api/sessions/${id}/reparse`, { method: 'POST' }),
  encounter: (id) => req(`/api/encounters/${id}`),
  encountersAgg: (ids) => req(`/api/encounters/agg?ids=${ids.join(',')}`),
  raidReport: (id) => req(`/api/sessions/${id}/raid-report`),
  setCalibration: (id, calibration) => req(`/api/sessions/${id}/calibration`, json({ calibration })),
  upload: (file, characterName) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('character_name', characterName)
    return req('/api/uploads', { method: 'POST', body: fd })
  },
}

export const fmt = {
  num: (n) => (n == null ? '—' : Math.round(n).toLocaleString()),
  dur: (s) => {
    if (s == null) return '—'
    const m = Math.floor(s / 60), r = s % 60
    return m ? `${m}m ${r}s` : `${r}s`
  },
  time: (epoch) => (epoch == null ? '—' : new Date(epoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })),
  date: (epoch) => (epoch == null ? '—' : new Date(epoch * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })),
  dateLong: (epoch) => (epoch == null ? '—' : new Date(epoch * 1000).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })),
  timeRange: (a, b) => (a == null ? '—' : b == null ? fmt.time(a) : `${fmt.time(a)} – ${fmt.time(b)}`),
  dayKey: (epoch) => new Date(epoch * 1000).toLocaleDateString('en-CA'), // local YYYY-MM-DD
  pct: (a, b) => (b ? `${Math.round((a / b) * 100)}%` : '—'),
}
