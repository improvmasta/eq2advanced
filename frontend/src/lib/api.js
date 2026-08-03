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

/* Read-through cache for the GETs a raid night asks for over and over.

   Clicking down the fight rail re-requests the same aggregate every time you
   come back to a pull you already opened, and `/encounters/agg` recomputes
   medians from raw events — a whole-run request on a 60-fight night is not
   cheap. Answers are immutable until something is uploaded or edited, so they
   are kept for the session and every mutation below empties the map.
   `peek` hands the cached answer back synchronously, which is what lets the
   page repaint on a click instead of flashing "Loading…" at you. */
const cache = new Map()          // url -> payload
const inflight = new Map()       // url -> promise

function cachedGet(url) {
  if (cache.has(url)) return Promise.resolve(cache.get(url))
  let p = inflight.get(url)
  if (!p) {
    p = req(url).then(
      (data) => { cache.set(url, data); inflight.delete(url); return data },
      (e) => { inflight.delete(url); throw e })
    inflight.set(url, p)
  }
  return p
}

export const peek = (url) => (cache.has(url) ? cache.get(url) : null)
export const clearCache = () => { cache.clear(); inflight.clear() }

/* Mutations invalidate everything: an upload re-segments zone runs, a delete
   moves fights between them, and a reparse rewrites the stats under ids that
   did not change. Targeted invalidation here would be a bug farm. */
const mutate = (p) => Promise.resolve(p).finally(clearCache)

export const url = {
  agg: (ids) => `/api/encounters/agg?ids=${ids.join(',')}`,
  timeline: (ids, bucket) => `/api/encounters/timeline?ids=${ids.join(',')}${bucket ? `&bucket=${bucket}` : ''}`,
  deaths: (ids, windowS) => `/api/encounters/deaths?ids=${ids.join(',')}${windowS ? `&window=${windowS}` : ''}`,
  zoneRun: (id) => `/api/zone-runs/${id}`,
  zoneRunReport: (id) => `/api/zone-runs/${id}/report`,
}

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
  zoneRun: (id) => cachedGet(url.zoneRun(id)),
  zoneRunReport: (id) => cachedGet(url.zoneRunReport(id)),
  // raid-list editing: every edit is remembered by fight, so it survives the
  // next reparse or backfill of the same night
  deleteZoneRun: (id) => mutate(req(`/api/zone-runs/${id}`, { method: 'DELETE' })),
  mergeZoneRuns: (ids) => mutate(req('/api/zone-runs/merge', json({ ids }))),
  unmergeZoneRun: (id) => mutate(req(`/api/zone-runs/${id}/unmerge`, { method: 'POST' })),
  splitZoneRun: (id, encounterId) => mutate(req(`/api/zone-runs/${id}/split`, json({ encounter_id: encounterId }))),
  deleteEncounters: (ids) => mutate(req('/api/encounters/delete', json({ ids }))),
  restoreEncounters: (fingerprints, characterId) => mutate(req(
    '/api/encounters/restore', json({ fingerprints, character_id: characterId }))),
  deleteSession: (id) => mutate(req(`/api/sessions/${id}`, { method: 'DELETE' })),
  coach: (sessionId) => req(`/api/sessions/${sessionId}/coach`),
  generateCoach: (sessionId) => req(`/api/sessions/${sessionId}/coach`, { method: 'POST' }),
  reparse: (id) => mutate(req(`/api/sessions/${id}/reparse`, { method: 'POST' })),
  encounter: (id) => req(`/api/encounters/${id}`),
  encountersAgg: (ids) => cachedGet(url.agg(ids)),
  encountersTimeline: (ids, bucket) => cachedGet(url.timeline(ids, bucket)),
  encountersDeaths: (ids, windowS) => cachedGet(url.deaths(ids, windowS)),
  raidReport: (id) => req(`/api/sessions/${id}/raid-report`),
  setCalibration: (id, calibration) => req(`/api/sessions/${id}/calibration`, json({ calibration })),
  upload: (file, characterName) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('character_name', characterName)
    return mutate(req('/api/uploads', { method: 'POST', body: fd }))
  },
}

export const fmt = {
  num: (n) => (n == null ? '—' : Math.round(n).toLocaleString()),
  // ACT prints rate columns (EncDPS/EncHPS) with two decimals
  num2: (n) => (n == null ? '—'
    : n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })),
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
