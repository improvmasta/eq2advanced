async function req(path, opts) {
  const res = await fetch(path, opts)
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not json */ }
    const err = new Error(detail)
    err.status = res.status
    // Only OUR 413 offers a way through it. A 413 from the proxy in front is an
    // HTML page with no header, and nothing the app does can satisfy it.
    err.parseOnlyAllowed = res.headers.get('x-parse-only-allowed') === '1'
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
  aoes: (ids) => `/api/encounters/aoes?ids=${ids.join(',')}`,
  zoneRun: (id) => `/api/zone-runs/${id}`,
  zoneRunReport: (id) => `/api/zone-runs/${id}/report`,
}

export const api = {
  me: () => req('/api/auth/me'),
  register: (username, password, sqId, answer) => req(
    '/api/auth/register', json({ username, password, sq_id: sqId, answer })),
  login: (username, password) => req('/api/auth/login', json({ username, password })),
  logout: () => req('/api/auth/logout', { method: 'POST' }),
  changePassword: (current, next) => req('/api/auth/password', json({ current, new: next })),
  // no email exists, so the security question is the whole recovery story
  securityQuestions: () => req('/api/auth/questions'),
  setSecurityQuestion: (password, sqId, answer) => req(
    '/api/auth/security-question', json({ password, sq_id: sqId, answer })),
  resetStart: (username) => req('/api/auth/reset/start', json({ username })),
  resetComplete: (username, answer, newPassword) => req(
    '/api/auth/reset/complete', json({ username, answer, new_password: newPassword })),
  characters: () => req('/api/characters'),
  addCharacter: (name) => req('/api/characters', json({ name })),
  deleteCharacter: (id) => req(`/api/characters/${id}`, { method: 'DELETE' }),
  // device tokens are per ACCOUNT (v13) — one pairing covers every character
  tokens: () => req('/api/tokens'),
  mintToken: (label) => req('/api/tokens', json({ label })),
  // Sonarr-style: revokes every live key and mints the replacement
  refreshToken: (label) => req('/api/tokens/refresh', json({ label })),
  revokeToken: (id) => req(`/api/tokens/${id}/revoke`, { method: 'POST' }),
  census: (charId) => req(`/api/characters/${charId}/census`),
  censusRefresh: (charId) => req(`/api/characters/${charId}/census/refresh`, { method: 'POST' }),
  censusSnapshots: (charId) => req(`/api/characters/${charId}/census/snapshots`),
  censusDiff: (charId, snapId) => req(`/api/characters/${charId}/census/snapshots/${snapId}/diff`),
  spell: (id) => req(`/api/spells/${id}`),
  sessions: () => req('/api/sessions'),
  session: (id) => req(`/api/sessions/${id}`),
  zoneRuns: (scope) => req(`/api/zone-runs${scope ? `?scope=${scope}` : ''}`),
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
  encountersAoes: (ids) => cachedGet(url.aoes(ids)),
  raidReport: (id) => req(`/api/sessions/${id}/raid-report`),
  setCalibration: (id, calibration) => req(`/api/sessions/${id}/calibration`, json({ calibration })),
  upload: (file, characterName, retainRaw = true) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('character_name', characterName)
    fd.append('retain_raw', retainRaw ? '1' : '0')
    return mutate(req('/api/uploads', { method: 'POST', body: fd }))
  },
  uploadLimits: () => req('/api/uploads/limits'),
  // the ACT plugin build this server is serving (size/date for the Import page)
  plugin: () => req('/api/plugin'),

  // groups + sharing
  groups: () => req('/api/groups'),
  group: (id) => req(`/api/groups/${id}`),
  createGroup: (name, description, joinCode) => req(
    '/api/groups', json({ name, description, join_code: joinCode })),
  // a free code to show alongside the name being typed; claimed at create time
  newJoinCode: () => req('/api/groups/new-code'),
  updateGroup: (id, body) => req(`/api/groups/${id}`, { ...json(body), method: 'PATCH' }),
  // `confirm` is the group's name typed back exactly — the server checks it too
  deleteGroup: (id, confirm) => mutate(req(
    `/api/groups/${id}?confirm=${encodeURIComponent(confirm)}`, { method: 'DELETE' })),
  joinGroup: (code) => mutate(req('/api/groups/join', json({ code }))),
  // what an invite link resolves to before the visitor has an account
  previewInvite: (code) => req(`/api/groups/preview/${encodeURIComponent(code)}`),
  rotateJoinCode: (id, body) => req(`/api/groups/${id}/code/rotate`, json(body || {})),
  inviteToGroup: (id, username) => req(`/api/groups/${id}/invites`, json({ username })),
  answerInvite: (id, decision) => req(`/api/invites/${id}/${decision}`, { method: 'POST' }),
  leaveGroup: (id) => mutate(req(`/api/groups/${id}/leave`, { method: 'POST' })),
  removeMember: (id, userId) => req(`/api/groups/${id}/members/${userId}`, { method: 'DELETE' }),
  setMemberRole: (id, userId, role) => req(`/api/groups/${id}/members/${userId}/role`, json({ role })),
  runShares: (runId) => req(`/api/zone-runs/${runId}/shares`),
  setRunShares: (runId, groupIds) => mutate(req(
    `/api/zone-runs/${runId}/shares`, { ...json({ group_ids: groupIds }), method: 'PUT' })),
  setRunPublic: (runId, isPublic) => mutate(req(
    `/api/zone-runs/${runId}/public`, { ...json({ public: isPublic }), method: 'PUT' })),
  characterShares: (charId) => req(`/api/characters/${charId}/shares`),
  // shares: [{group_id, history}] — history=false shares only raids recorded
  // while the switch has been on, true includes the back catalogue
  setCharacterShares: (charId, shares) => mutate(req(
    `/api/characters/${charId}/shares`, { ...json({ shares }), method: 'PUT' })),

  // admin console — metadata only, by design (backend/routers/admin_api.py)
  adminOverview: () => req('/api/admin/overview'),
  adminUsers: () => req('/api/admin/users'),
  adminSetDisabled: (id, disabled) => req(`/api/admin/users/${id}/disabled`, json({ disabled })),
  adminResetPassword: (id, password) => req(`/api/admin/users/${id}/password`, json({ password })),
  adminRenameUser: (id, username) => req(`/api/admin/users/${id}/username`, json({ username })),
  // deleted groups, and putting one back for whoever deleted it
  adminDeletedGroups: () => req('/api/admin/groups'),
  adminRestoreGroup: (id) => mutate(req(`/api/admin/groups/${id}/restore`, { method: 'POST' })),
  adminSetLimits: (id, body) => req(`/api/admin/users/${id}/limits`, json(body)),
  adminSettings: (body) => req('/api/admin/settings', { ...json(body), method: 'PUT' }),
  adminAudit: () => req('/api/admin/audit'),
  adminPublicRuns: () => req('/api/admin/public-runs'),
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
  // wall-clock spans: a raid night is "2h 23m", never "143m 34s"
  durH: (s) => {
    if (s == null) return '—'
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60)
    return h ? `${h}h ${m}m` : `${m}m`
  },
  time: (epoch) => (epoch == null ? '—' : new Date(epoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })),
  date: (epoch) => (epoch == null ? '—' : new Date(epoch * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })),
  dateLong: (epoch) => (epoch == null ? '—' : new Date(epoch * 1000).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })),
  timeRange: (a, b) => (a == null ? '—' : b == null ? fmt.time(a) : `${fmt.time(a)} – ${fmt.time(b)}`),
  dayKey: (epoch) => new Date(epoch * 1000).toLocaleDateString('en-CA'), // local YYYY-MM-DD
  pct: (a, b) => (b ? `${Math.round((a / b) * 100)}%` : '—'),
  bytes: (n) => {
    if (n == null) return '—'
    if (n < 1024) return `${n} B`
    if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
    const mb = n / (1024 * 1024)
    return mb < 10 ? `${mb.toFixed(1)} MB` : `${Math.round(mb)} MB`
  },
}
