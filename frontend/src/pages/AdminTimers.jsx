import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'
import AdminShell from '../components/AdminShell.jsx'

const STATES = [
  ['needs_review', 'Needs review'], ['all', 'All'], ['disagreement', 'Disagreement'],
  ['learning', 'Learning'], ['healthy', 'Healthy'], ['overridden', 'Overridden'],
  ['excluded', 'Excluded'],
]

const seconds = (n) => n == null ? '—' : `${Number(n).toFixed(Number(n) % 1 ? 1 : 0)}s`

function Bars({ values, tone }) {
  const max = Math.max(...values, 1)
  return <div className={`intervals ${tone}`}>
    {values.map((v, i) => <span key={`${v}-${i}`} style={{ width: `${Math.max(4, v / max * 100)}%` }}>{v}s</span>)}
  </div>
}

function TimerDetail({ row, onChanged }) {
  const [detail, setDetail] = useState(null)
  const [note, setNote] = useState(row.ruling?.note ?? '')
  const [override, setOverride] = useState(row.ruling?.override_s ?? '')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)
  useEffect(() => {
    setDetail(null); setNote(row.ruling?.note ?? ''); setOverride(row.ruling?.override_s ?? '')
    api.adminTimer(row.mob, row.ability).then(setDetail).catch((e) => setMessage(e.message))
  }, [row.mob, row.ability])
  const mutate = async (body) => {
    setBusy(true); setMessage(null)
    try { await api.adminRuleTimer(row.mob, row.ability, body); setMessage('Ruling saved.'); onChanged() }
    catch (e) { setMessage(e.message) } finally { setBusy(false) }
  }
  const clear = async () => {
    setBusy(true)
    try { await api.adminClearTimer(row.mob, row.ability); setMessage('Ruling cleared.'); onChanged() }
    catch (e) { setMessage(e.message) } finally { setBusy(false) }
  }
  if (!detail) return <div className="timerdetail"><p className="muted">Loading evidence…</p></div>
  return <div className="timerdetail">
    <div className="pagehead"><div><h2>{row.ability}</h2><p className="muted">{row.mob}</p></div></div>
    <div className="timerpreview">
      <span>Live preview</span><b>{seconds(row.effective_s)}</b><small>{row.effective_source}</small>
    </div>
    <p>{detail.reason}</p>
    <dl className="timerfacts">
      <div><dt>ACT reported</dt><dd>{seconds(detail.reported_s)}</dd></div>
      <div><dt>Measured clean</dt><dd>{seconds(detail.clean_s)}</dd></div>
      <div><dt>Agreeing intervals</dt><dd>{detail.base_agree}</dd></div>
      <div><dt>Distinct pulls</dt><dd>{detail.base_fights}</dd></div>
      <div><dt>Swipe factor</dt><dd>{detail.swipe_factor ? `×${detail.swipe_factor}` : '—'} {detail.swipe_verdict || ''}</dd></div>
      <div><dt>Last observed</dt><dd>{detail.last_observation_ts ? fmt.date(detail.last_observation_ts) : '—'}</dd></div>
    </dl>
    <h3>Clean intervals</h3><Bars values={detail.clean_intervals} tone="clean" />
    <h3>Swiped intervals</h3><Bars values={detail.swiped_intervals} tone="swiped" />
    <p className="muted small">Adoption requires {detail.thresholds.minimum_agreeing} agreeing clean intervals across {detail.thresholds.minimum_pulls} distinct pulls. No player, character, session, or encounter identity is exposed.</p>
    <div className="timerform formcol">
      <label>Curated timer, seconds<input type="number" min="1" max="3600" value={override} onChange={(e) => setOverride(e.target.value)} /></label>
      <label>Required note<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why this ruling is correct" /></label>
      <div className="row wrap">
        <button disabled={busy || !note.trim() || !override} onClick={() => mutate({ override_s: Number(override), note })}>Set override</button>
        <button disabled={busy || !note.trim() || !detail.clean_s} onClick={() => mutate({ accept_measured: true, note })}>Accept measured</button>
        <button disabled={busy || !note.trim()} onClick={() => mutate({ excluded: true, note })}>Exclude</button>
        <button disabled={busy || !note.trim()} onClick={() => mutate({ split_mob: true, note })}>Mark split mob</button>
        {row.ruling && <button disabled={busy} onClick={clear}>Clear ruling</button>}
      </div>
      {message && <p className={message.includes('saved') || message.includes('cleared') ? 'ok' : 'err'}>{message}</p>}
    </div>
    <details><summary>ACT timer proposal</summary><pre>{`<Spell N="${row.ability}" T="${Math.round(row.effective_s || detail.clean_s || 0)}" />`}</pre><button className="chip" onClick={() => navigator.clipboard?.writeText(`<Spell N="${row.ability}" T="${Math.round(row.effective_s || detail.clean_s || 0)}" />`)}>Copy ACT entry</button></details>
  </div>
}

function AdvancedMechanics() {
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(null)
  const [config, setConfig] = useState({})
  const [note, setNote] = useState('')
  const [message, setMessage] = useState(null)
  const load = () => api.adminTimerMechanics().then((d) => { setData(d); setSelected((s) => d.items.find((r) => r.kind === s?.kind && r.name === s?.name) || d.items[0] || null) })
  useEffect(load, [])
  useEffect(() => { if (selected) { setConfig(selected.config || {}); setNote(selected.note || '') } }, [selected])
  const save = async () => {
    try { await api.adminRuleTimerMechanic(selected.kind, selected.name, { config, note }); setMessage('Mechanic saved.'); load() }
    catch (e) { setMessage(e.message) }
  }
  return <details className="advancedmechanics"><summary>Advanced mechanics: reuse debuffs and reflect windows</summary>
    <div className="mechanicgrid"><div className="masterlist">{data?.items.map((r) => <button key={`${r.kind}|${r.name}`} className={selected?.kind === r.kind && selected?.name === r.name ? 'on' : ''} onClick={() => setSelected(r)}><span><b>{r.name}</b><small>{r.kind.replace('_', ' ')}</small></span>{r.curated && <span><small>overridden</small></span>}</button>)}</div>
      {selected && <div className="formcol mechanicform"><h3>{selected.name}</h3>{selected.kind === 'reuse_debuff' ? <>
        <label>Duration, seconds<input type="number" min="1" value={config.duration_s || ''} onChange={(e) => setConfig({ ...config, duration_s: Number(e.target.value) })} /></label>
        <label>Recast, seconds<input type="number" min="1" value={config.recast_s || ''} onChange={(e) => setConfig({ ...config, recast_s: Number(e.target.value) })} /></label>
        <label>Magnitude, percent<input type="number" min="1" value={config.magnitude_pct || ''} onChange={(e) => setConfig({ ...config, magnitude_pct: Number(e.target.value) })} /></label>
        <label>Grant<input value={config.grant || ''} onChange={(e) => setConfig({ ...config, grant: e.target.value })} /></label>
      </> : <label>Reflect window, seconds<input type="number" min="1" value={config.window_s || ''} onChange={(e) => setConfig({ ...config, window_s: Number(e.target.value) })} /></label>}
      <label>Evidence note<input value={config.note || ''} onChange={(e) => setConfig({ ...config, note: e.target.value })} /></label><label>Required change note<input value={note} onChange={(e) => setNote(e.target.value)} /></label><div className="row"><button disabled={!note.trim()} onClick={save}>Save ruling</button>{selected.curated && <button onClick={async () => { await api.adminClearTimerMechanic(selected.kind, selected.name); setMessage('Reverted to shipped reference.'); load() }}>Revert</button>}</div>{message && <p className="note">{message}</p>}</div>}
    </div>
  </details>
}

export default function AdminTimers({ user }) {
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(params.get('q') || params.get('mob') || '')
  const state = params.get('state') || 'needs_review'
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)
  const load = useCallback(() => api.adminTimers({ q, state }).then((d) => {
    setData(d); setSelected((s) => d.items.find((r) => s && r.mob === s.mob && r.ability === s.ability) || d.items[0] || null)
  }).catch((e) => setError(e.message)), [q, state])
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t) }, [load])
  const setFilter = (next) => { const p = new URLSearchParams(params); Object.entries(next).forEach(([k, v]) => v ? p.set(k, v) : p.delete(k)); setParams(p, { replace: true }) }
  const counts = useMemo(() => data?.items.reduce((a, r) => ({ ...a, [r.state]: (a[r.state] || 0) + 1 }), {}) || {}, [data])
  return <AdminShell user={user}><div className="timersworkbench adminworkspace">
    <div className="adminpagehead compact"><div><p className="adminkicker">Game data</p><h1>AoE timers</h1><p>Reported, measured, and curated timer knowledge.</p></div><div className="adminviewswitch"><Link to="/admin/abilities">Abilities</Link><Link className="on" to="/admin/timers">AoE timers</Link></div></div>
    <div className="workbenchfilters">
      <input value={q} onChange={(e) => { setQ(e.target.value); setFilter({ q: e.target.value }) }} placeholder="Search mob or ability…" />
      <select value={state} onChange={(e) => setFilter({ state: e.target.value })}>{STATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      {data && <span className="muted">{data.total} rows · {counts.disagreement || 0} disagreements</span>}
    </div>
    <AdvancedMechanics />
    {error && <p className="err">{error}</p>}
    <div className="masterdetail">
      <div className="masterlist">{data?.items.map((r) => <button key={`${r.mob}|${r.ability}`} className={selected?.mob === r.mob && selected?.ability === r.ability ? 'on' : ''} onClick={() => setSelected(r)}>
        <span><b>{r.ability}</b><small>{r.mob}</small></span><span><b>{seconds(r.effective_s)}</b><small className={`timerstate ${r.state}`}>{r.state.replace('_', ' ')}</small></span>
      </button>)}</div>
      {selected ? <TimerDetail row={selected} onChanged={load} /> : <div className="timerdetail"><p className="muted">No timer matches this queue.</p></div>}
    </div>
  </div></AdminShell>
}
