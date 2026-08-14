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
  loot: (ids) => `/api/encounters/loot?ids=${ids.join(',')}`,
  classStats: (ids) => `/api/encounters/class-stats?ids=${ids.join(',')}`,
  encountersReport: (ids) => `/api/encounters/report?ids=${ids.join(',')}`,
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
  // kind: bug|suggestion. `page` is where they were when they hit the button —
  // "the numbers look wrong" is unanswerable without it
  sendFeedback: (kind, body, page) => req('/api/feedback', json({ kind, body, page })),
  characters: () => req('/api/characters'),
  addCharacter: (name) => req('/api/characters', json({ name })),
  deleteCharacter: (id) => req(`/api/characters/${id}`, { method: 'DELETE' }),
  // device tokens are per ACCOUNT (v13) — one pairing covers every character
  tokens: () => req('/api/tokens'),
  // Sonarr-style: revokes every live key and mints the replacement
  refreshToken: (label) => req('/api/tokens/refresh', json({ label })),
  census: (charId) => req(`/api/characters/${charId}/census`),
  censusRefresh: (charId) => req(`/api/characters/${charId}/census/refresh`, { method: 'POST' }),
  censusSnapshots: (charId) => req(`/api/characters/${charId}/census/snapshots`),
  censusDiff: (charId, snapId) => req(`/api/characters/${charId}/census/snapshots/${snapId}/diff`),
  sessions: () => req('/api/sessions'),
  session: (id) => req(`/api/sessions/${id}`),
  // `roster` asks for each night's names too — the Compare picker facets on
  // them in the browser instead of asking the server per keystroke
  // `dismissed` asks for the raids you swept off the list as well — they are
  // out by default, and come back flagged rather than as a second request
  zoneRuns: (scope, { roster, dismissed } = {}) => req(
    `/api/zone-runs?scope=${scope || 'all'}${roster ? '&roster=1' : ''}`
    + `${dismissed ? '&dismissed=1' : ''}`),
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
  // hiding is the reversible half of the same edit: the fight stays yours and
  // stops reaching anyone you shared the raid with
  hideEncounters: (ids, hidden = true) => mutate(req(
    '/api/encounters/hide', json({ ids, hidden }))),
  hideZoneRun: (id, hidden = true) => mutate(req(
    `/api/zone-runs/${id}/hide`, json({ hidden }))),
  // the reader's half of that: somebody else's raid off your own list, which
  // changes nothing about the raid and nothing about who else can read it
  dismissZoneRun: (id, dismissed = true) => mutate(req(
    `/api/zone-runs/${id}/dismiss`, json({ dismissed }))),
  deleteSession: (id) => mutate(req(`/api/sessions/${id}`, { method: 'DELETE' })),
  coach: (sessionId) => req(`/api/sessions/${sessionId}/coach`),
  generateCoach: (sessionId) => req(`/api/sessions/${sessionId}/coach`, { method: 'POST' }),
  reparse: (id) => mutate(req(`/api/sessions/${id}/reparse`, { method: 'POST' })),
  encounter: (id) => req(`/api/encounters/${id}`),
  encountersAgg: (ids) => cachedGet(url.agg(ids)),
  encountersTimeline: (ids, bucket) => cachedGet(url.timeline(ids, bucket)),
  encountersDeaths: (ids, windowS) => cachedGet(url.deaths(ids, windowS)),
  encountersAoes: (ids) => cachedGet(url.aoes(ids)),
  encountersLoot: (ids) => cachedGet(url.loot(ids)),
  encountersClassStats: (ids) => cachedGet(url.classStats(ids)),
  // the run report, for a caller that has fights and no run — the dashboard
  encountersReport: (ids) => cachedGet(url.encountersReport(ids)),
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

  /* Parses imported from an ACT screenshot. Not cached: reading one takes
     seconds and the answer is only ever fetched once per column. */
  parseshots: () => req('/api/parseshots'),
  parseshot: (id) => req(`/api/parseshots/${id}`),
  importParseshot: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return req('/api/parseshots', { method: 'POST', body: fd })
  },
  /* Naming a shot after the fact: who, where, which fight, when — the metadata
     a cropped screenshot never carried. Never the figures in the table; see
     backend/routers/parseshots_api.py. */
  updateParseshot: (id, patch) => req(`/api/parseshots/${id}`,
    { ...json(patch), method: 'PATCH' }),
  deleteParseshot: (id) => req(`/api/parseshots/${id}`, { method: 'DELETE' }),
  /* The kept screenshot. A plain URL rather than a fetch — it goes straight
     into an <img>, and the session cookie authorises it exactly as it does
     every other request. */
  parseshotImage: (id, thumb) => `/api/parseshots/${id}/image${thumb ? '?thumb=1' : ''}`,

  /* Raid notes, filed from the dashboard against a zone or a named. Not
     cached: the panel is looking at exactly one subject at a time and rewrites
     it as you type. `mob` absent asks for the ZONE's notes, which is a real
     filter — see backend/routers/notes_api.py. */
  notes: (zone, mob, scope) => {
    if (!zone) return req('/api/notes')
    const q = new URLSearchParams({ zone })
    if (mob) q.set('mob', mob)
    // 'zone' widens it to everything filed in the zone, nameds included —
    // what the dashboard column and the outline read.
    if (scope) q.set('scope', scope)
    return req(`/api/notes?${q}`)
  },
  notesOutline: () => req('/api/notes/outline'),
  addNote: (note) => req('/api/notes', json(note)),
  updateNote: (id, patch) => req(`/api/notes/${id}`, { ...json(patch), method: 'PATCH' }),
  deleteNote: (id) => req(`/api/notes/${id}`, { method: 'DELETE' }),
  addNoteShot: (noteId, file) => {
    const fd = new FormData()
    fd.append('file', file)
    return req(`/api/notes/${noteId}/shots`, { method: 'POST', body: fd })
  },
  deleteNoteShot: (noteId, shotId) => req(
    `/api/notes/${noteId}/shots/${shotId}`, { method: 'DELETE' }),
  noteShotImage: (noteId, shotId, thumb) => (
    `/api/notes/${noteId}/shots/${shotId}/image${thumb ? '?thumb=1' : ''}`),

  /* The two hand marks, on the account (`lib/marks.js`). A PATCH, never the
     world: `{joust: {ability: true|false|null}}` names the abilities it has
     something to say about and leaves the rest alone, so a pill clicked on the
     dashboard cannot undo one clicked on the raid page in another tab. `null`
     is the way back to nothing-said. The in-game window and the stream overlay
     never call either of these — they have no cookie, and their marks ride in
     with the token config they already poll. */
  marks: () => req('/api/marks'),
  setMarks: (patch) => req('/api/marks', { ...json({ marks: patch }), method: 'PUT' }),

  /* Stream overlay links. The overlay PAGE never calls these — it is
     authorized by the token in its own URL and fetches `/api/overlay/<token>`
     directly, with no cookie and no session. These are the account side:
     minting one, changing what it shows, taking it back. */
  overlayTokens: () => req('/api/overlay-tokens'),
  createOverlayToken: (body) => req('/api/overlay-tokens', json(body)),
  updateOverlayToken: (id, body) => req(
    `/api/overlay-tokens/${id}`, { ...json(body), method: 'PATCH' }),
  revokeOverlayToken: (id) => req(`/api/overlay-tokens/${id}/revoke`, { method: 'POST' }),
  // the ACT plugin build this server is serving (size/date for the Import page)
  plugin: () => req('/api/plugin'),

  /* The public chat box. Read once for the newest of the archive, then followed
     on `/api/chat/stream?since=<seq>` — never cached, because the answer is
     different every second and the whole point is that it is live.

     `chatHistory` is the date filter, and its window is built in the BROWSER
     from local midnight to local midnight: the server keeps unix seconds and
     has no idea which day the reader means. */
  chatRecent: () => req('/api/chat/recent'),
  /* Just the light: how many people are relaying chat this minute. The header
     carries it on every page, so it must not cost three channels of messages
     to ask. */
  chatStatus: () => req('/api/chat/status'),
  chatRecruiting: () => req('/api/chat/recruiting'),
  chatHistory: (ch, start, end) => req(
    `/api/chat/history?ch=${ch}&start=${start}&end=${end}`),
  /* The Stats panel, over whatever window the box is showing. No window is a
     real argument and means ALL TIME — the box's live state is a few hundred
     lines of tail, and counting those would answer a question nobody asked. */
  chatStats: (ch, start, end) => req(
    start == null ? `/api/chat/stats?ch=${ch}`
      : `/api/chat/stats?ch=${ch}&start=${start}&end=${end}`),
  /* One item's examine card, for a link that did not arrive with a parse — the
     Loot tab is handed its cards with its rows and never calls this. A pure
     read: an id nobody has resolved answers `{card: null}`. */
  itemCard: (id) => req(`/api/items/${id}/card`),

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
  // the guild tags my characters wear, who wears them, and what I've connected
  guildShares: () => req('/api/guild-shares'),
  // shares: [{guild_name, history, group_content}] — the full set for THIS
  // group; my rules for other groups are untouched
  setGroupGuildShares: (groupId, shares) => mutate(req(
    `/api/groups/${groupId}/guild-shares`, { ...json({ shares }), method: 'PUT' })),

  // admin console — metadata only, by design (backend/routers/admin_api.py)
  adminOverview: () => req('/api/admin/overview'),
  // searched, sorted and paged on the server — the accounts table has to hold
  // up with more accounts than fit on a screen
  adminUsers: ({ q = '', sort = 'stored_bytes', dir = 'desc', limit = 50, offset = 0 } = {}) =>
    req(`/api/admin/users?q=${encodeURIComponent(q)}&sort=${sort}&dir=${dir}`
      + `&limit=${limit}&offset=${offset}`),
  adminSetDisabled: (id, disabled) => req(`/api/admin/users/${id}/disabled`, json({ disabled })),
  adminResetPassword: (id, password) => req(`/api/admin/users/${id}/password`, json({ password })),
  adminRenameUser: (id, username) => req(`/api/admin/users/${id}/username`, json({ username })),
  // deleted groups, and putting one back for whoever deleted it
  adminDeletedGroups: () => req('/api/admin/groups'),
  adminRestoreGroup: (id) => mutate(req(`/api/admin/groups/${id}/restore`, { method: 'POST' })),
  adminSetLimits: (id, body) => req(`/api/admin/users/${id}/limits`, json(body)),
  // user | curator | admin — curator opens the Abilities console and nothing else
  adminSetRole: (id, role) => req(`/api/admin/users/${id}/role`, json({ role })),
  adminSettings: (body) => req('/api/admin/settings', { ...json(body), method: 'PUT' }),
  adminAudit: ({ limit = 200, offset = 0 } = {}) =>
    req(`/api/admin/audit?limit=${limit}&offset=${offset}`),
  /* How many people came, by day. A count of visits, never a list of them —
     the table behind this cannot name anybody (`backend/visitors.py`). */
  adminVisitors: (days = 30) => req(`/api/admin/visitors?days=${days}`),
  adminPublicRuns: () => req('/api/admin/public-runs'),
  // bug reports and suggestions: anyone signed in files them, an admin triages
  adminFeedback: ({ status = '', kind = '', limit = 100, offset = 0 } = {}) =>
    req(`/api/admin/feedback?status=${status}&kind=${kind}&limit=${limit}&offset=${offset}`),
  adminSetFeedbackStatus: (id, status) => req(
    `/api/admin/feedback/${id}`, { ...json({ status }), method: 'PATCH' }),
  adminDeleteFeedback: (id) => req(`/api/admin/feedback/${id}`, { method: 'DELETE' }),
  /* Abilities: the one admin surface that edits GAME knowledge rather than
     site state. `scope=open` is the work queue (everything under full
     confidence and unruled); any `q` searches every ability ever tracked, so
     a settled answer can be reopened. Still no player names in the payload —
     evidence is site-wide sums and class names. */
  adminAbilities: ({ q = '', scope = 'open' } = {}) => req(
    `/api/admin/abilities?scope=${scope}${q ? `&q=${encodeURIComponent(q)}` : ''}`),
  adminRuleAbility: (name, body) => req(
    `/api/admin/abilities/${encodeURIComponent(name)}`, { ...json(body), method: 'PUT' }),
  adminUnruleAbility: (name) => req(
    `/api/admin/abilities/${encodeURIComponent(name)}`, { method: 'DELETE' }),
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
  /* A running clock, read the way a clock is read: `2:07`, not `2m 7s`.
     For the live dashboard only — a figure that changes every second while you
     are watching it should not change WIDTH every second too, so it is fixed
     punctuation and two-digit seconds, on `font-variant-numeric: tabular-nums`
     so the digits sit still. Counting DOWN rounds the other way (a countdown
     shows 0:01 until it is actually up), which is AoeTimers' own `mmss`. */
  clock: (s) => {
    if (s == null) return '—'
    const n = Math.max(0, Math.floor(s))
    const h = Math.floor(n / 3600), m = Math.floor((n % 3600) / 60), r = n % 60
    const mm = h ? String(m).padStart(2, '0') : String(m)
    return `${h ? `${h}:` : ''}${mm}:${String(r).padStart(2, '0')}`
  },
  /* The header clocks, in the units a raid night is actually thought about.
     "132m 18s" is a number you have to divide before it means anything; the
     seconds stay because the two clocks beside each other (raid time vs
     combat) are read as a difference. */
  durHMS: (s) => {
    if (s == null) return '—'
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60
    return h ? `${h}h ${m}m ${r}s` : m ? `${m}m ${r}s` : `${r}s`
  },
  // wall-clock spans: a raid night is "2h 23m", never "143m 34s"
  durH: (s) => {
    if (s == null) return '—'
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60)
    return h ? `${h}h ${m}m` : `${m}m`
  },
  /* The clock, with the half of the day as a SUFFIX rather than as part of the
     number. "07:42 PM" is a data field; a raid list is read at a glance, so the
     hour drops its leading zero and the meridiem goes lowercase. Returning the
     two pieces separately is what lets a caller set the meridiem small — CSS
     cannot size half of a text node — and `time` is the plain-string form for
     everywhere that only needs the words. */
  timeParts: (epoch, withSeconds) => {
    if (epoch == null) return { t: '—', ap: '' }
    const d = new Date(epoch * 1000)
    const p = (n) => String(n).padStart(2, '0')
    return {
      t: `${d.getHours() % 12 || 12}:${p(d.getMinutes())}${withSeconds ? `:${p(d.getSeconds())}` : ''}`,
      ap: d.getHours() < 12 ? 'am' : 'pm',
    }
  },
  time: (epoch) => {
    const { t, ap } = fmt.timeParts(epoch)
    return ap ? `${t} ${ap}` : t
  },
  /* An EQ2 log stamps to the second and a wipe happens inside one minute, so
     the death list prints seconds: 9:41p is where four of them read as one. */
  timeS: (epoch) => {
    const { t, ap } = fmt.timeParts(epoch, true)
    return ap ? `${t} ${ap}` : t
  },
  /* The same clock without the AM/PM, for a column of consecutive seconds: the
     card it sits in already said which half of the day this was, and repeating
     it six times costs three characters of a narrow column every row. */
  clockS: (epoch) => {
    if (epoch == null) return '—'
    const d = new Date(epoch * 1000)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getHours() % 12 || 12}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  },
  date: (epoch) => (epoch == null ? '—' : new Date(epoch * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })),
  dateLong: (epoch) => (epoch == null ? '—' : new Date(epoch * 1000).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })),
  timeRange: (a, b) => (a == null ? '—' : b == null ? fmt.time(a) : `${fmt.time(a)} – ${fmt.time(b)}`),
  bytes: (n) => {
    if (n == null) return '—'
    if (n < 1024) return `${n} B`
    if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
    const mb = n / (1024 * 1024)
    return mb < 10 ? `${mb.toFixed(1)} MB` : `${Math.round(mb)} MB`
  },
}

/* What to call a session in a list. An upload has a filename; a live session
   has nothing but its fights, so it is named for where it was fought —
   most-recent zone, because a plugin left running all evening moves. The date
   is deliberately absent: every list showing this has a date column. */
export function sessionLabel(s) {
  if (s.upload_name) return s.upload_name
  if (s.source === 'live') {
    // A live session that logged no fights is common (zoning around, a short
    // test); saying so beats a number, and the date column tells them apart.
    if (!s.last_zone) {
      return s.status === 'receiving' ? 'Live — waiting for fights' : 'Live — no fights'
    }
    const extra = (s.zone_count ?? 1) - 1
    return extra > 0 ? `${s.last_zone} +${extra} zone${extra > 1 ? 's' : ''}` : s.last_zone
  }
  return `session ${s.id}`
}
