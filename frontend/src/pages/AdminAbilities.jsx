import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, fmt } from '../lib/api.js'

/* Deciding what an ability IS — a person's job, with the evidence in front of
   them.

   Both labels on this page used to be inferred and both were wrong at scale:
   one bare name mistaken for a dumbfire took a whole shadowknight spellbook
   into the pet catalog, and Census's "may cast X" grammar marked `Berserk` and
   `Dragon Stance` — a berserker's and a monk's own buttons — as gear procs. So
   nothing here decides anything on its own. It gathers, says how sure it is,
   and hands everything short of certain to you.

   The class rail is the whole ergonomics of it. Five hundred undecided
   abilities is a wall; fifty under `shadowknight` is an afternoon. An ability
   sits under EVERY class that might own it — who scribes it, whose buff fires
   it, who was seen using it — so the same name appearing three times is
   correct until it is ruled on, and `Unclassed` is where the gear and AA procs
   collect, because no class claims them. */

const UNITS = [['player', 'A player (or their gear/buffs)'], ['pet', "A pet's own kit"]]
const FIRES = [['cast', 'Pressed — it is in a rotation'], ['proc', 'Fires on its own']]
const KIND_LABEL = {
  spell: 'A spell', aa: 'An AA', item: 'Gear / an item',
  deity: 'A deity', pet: 'A pet', unknown: "Don't know yet",
}

/* Confidence is the queue's whole ordering, so it reads as a state, not a
   number: only `ruled` and `curated` are somebody's answer. */
const CONF = {
  ruled: ['ruled', 'Decided by hand — this beats everything else'],
  curated: ['curated', 'In the curated seed, verified against a real raid log'],
  high: ['confident', 'The evidence is one-sided'],
  medium: ['unsure', 'The evidence leans, but it could be read the other way'],
  low: ['no idea', 'Nothing decisive either way'],
}

function Evidence({ a }) {
  /* Only the counts that carry weight, and each one says what it proves.
     `pet_definite` is grammar (`Bobby's blighted horde` — a possessive with a
     lowercase remainder is a summoned thing, always); `pet_guess` is the
     parser guessing at a bare capitalized name, which is the column every bad
     label came out of, so it is labelled as the weak one it is. */
  const bits = [
    a.pet_definite > 0 && ['pet possessives', a.pet_definite,
      "Cast under `<Owner>'s <lowercase pet>` — certain by grammar"],
    a.pet_own > 0 && ["logger's own pet", a.pet_own,
      "Cast under the logger's bare name, which is their pet — their own actions print as YOU"],
    a.pet_guess > 0 && ['bare-name guesses', a.pet_guess,
      'Cast by a single capitalized name the parser GUESSED was a dumbfire — the weak column'],
    a.player_casts > 0 && ['player casts', a.player_casts,
      `${a.distinct_players} different characters`],
    a.mob_casts > 0 && ['mob casts', a.mob_casts, 'Cast by something the raid was fighting'],
    a.prepare_lines > 0 && ['prepare lines', a.prepare_lines,
      'The logger PRESSED it — "You prepare ..." never prints for a proc'],
    a.logger_hits > 0 && a.prepare_lines === 0 && ['uncast logger hits', a.logger_hits,
      'The logger produced it with no prepare line at all — it fired itself'],
  ].filter(Boolean)
  return (
    <div className="evidence">
      {bits.map(([label, n, title]) => (
        <span key={label} className="fact" title={title}>
          <b>{fmt.num(n)}</b> {label}
        </span>
      ))}
      {a.scribed_by && (
        <span className="fact" title="Census holds a spell record for this — a class can press it">
          scribed by <b>{a.scribed_by.replace(/,/g, ', ')}</b>
        </span>
      )}
      {/* What the EQ2 wiki has on the ability itself. `activated` is the only
          thing here the log physically cannot say: EQ2 prints no prepare line
          for an AA activation, so a pressed AA and a gear proc leave the same
          trace. A recast timer settles it. */}
      {a.activated === 1 && (
        <span className="fact good" title="A recast timer means a button. The log can't tell you this — no prepare line is printed for an AA activation.">
          wiki: <b>activated {a.wiki_kind}</b>
          {a.recast_s ? ` · ${a.recast_s}s recast` : ''}
        </span>
      )}
      {a.activated === 0 && (
        <span className="fact" title="No recast and no cost on the wiki page — firing on its own is how it works.">
          wiki: <b>passive {a.wiki_kind}</b>
          {a.wiki_line ? ` · ${a.wiki_line} line` : ''}
        </span>
      )}
      {a.wiki_tiers && (
        <span className="fact" title="The class tier the wiki files it under — EQ2 grants at every tier, so this may be a group">
          granted to <b>{a.wiki_tiers.replace(/,/g, ', ')}</b>
        </span>
      )}
      {!a.scribed_by && !a.grant_kind && a.activated == null && (
        <span className="fact warn" title="No cached spell record names it, nothing casts it, and the wiki has no page. Gear and deity are the remaining gaps — census_items holds 143 rows and the item pull doesn't exist yet.">
          nothing in Census or the wiki names it
        </span>
      )}
    </div>
  )
}

/* A comma list of grant targets, toggled one chip at a time. Kept as the
   string the API stores rather than a Set, so what you see is what is saved. */
function toggle(list, name) {
  const cur = (list || '').split(',').map((s) => s.trim()).filter(Boolean)
  const next = cur.includes(name) ? cur.filter((c) => c !== name) : [...cur, name]
  return next.join(',')
}

function Ruler({ a, targets, onSave, onClear, busy }) {
  /* The form opens on what we already believe, so agreeing is one click and
     only a disagreement costs typing. Census pre-fills the grant when it knows
     it — `Fae Fires` arrives with "Fae Fire", fury, spell already in the boxes. */
  const r = a.ruling
  const [unit, setUnit] = useState(r?.unit ?? (a.suggest === 'pet' ? 'pet' : 'player'))
  const [fires, setFires] = useState(r?.fires ?? (a.suggest === 'proc' ? 'proc' : 'cast'))
  /* The wiki knowing it is an AA outranks Census's guess at the grant, because
     Census was never asked about AAs at all. */
  const [kind, setKind] = useState(
    r?.grant_kind ?? (a.suggest === 'pet' ? 'pet' : a.wiki_kind || a.grant_kind || 'unknown'))
  /* An AA's identity is its LINE ("Rotting", "Intelligence"), which is what a
     curator would write here anyway; Census's source spell fills in otherwise. */
  const [name, setName] = useState(
    r?.grant_name ?? ((a.wiki_line ? `${a.wiki_line} line` : '') || a.grant_name || ''))
  const [cls, setCls] = useState(
    r?.grant_class ?? a.wiki_tiers ?? a.grant_class ?? (a.classes.length === 1 ? a.classes[0] : ''))
  const [note, setNote] = useState(r?.note ?? '')

  const picked = useMemo(
    () => new Set((cls || '').split(',').map((s) => s.trim()).filter(Boolean)), [cls])
  // what the picked tiers actually cover, spelled out — a group chip is only
  // honest if it says who it reaches
  const reach = useMemo(() => {
    const out = new Set()
    for (const t of targets) if (picked.has(t.name)) t.covers.forEach((c) => out.add(c))
    return out
  }, [targets, picked])

  return (
    <div className="ruler">
      <div className="rulerow">
        <label>It belongs to</label>
        <span className="seg">
          {UNITS.map(([v, title]) => (
            <button key={v} type="button" title={title}
                    className={unit === v ? 'on' : ''} onClick={() => setUnit(v)}>{v}</button>
          ))}
        </span>
      </div>
      <div className="rulerow">
        <label>and it</label>
        <span className="seg">
          {FIRES.map(([v, title]) => (
            <button key={v} type="button" title={title}
                    className={fires === v ? 'on' : ''} onClick={() => setFires(v)}>{v}</button>
          ))}
        </span>
      </div>
      <div className="rulerow">
        <label>granted by</label>
        <span className="seg">
          {Object.entries(KIND_LABEL).map(([v, title]) => (
            <button key={v} type="button" title={title}
                    className={kind === v ? 'on' : ''} onClick={() => setKind(v)}>{v}</button>
          ))}
        </span>
      </div>
      <div className="rulerow">
        <label htmlFor={`gn-${a.ability}`}>which is</label>
        <input id={`gn-${a.ability}`} value={name} placeholder="Fae Fire, Overclocked Lifestone, Bertoxxulous…"
               onChange={(e) => setName(e.target.value)} />
      </div>
      {/* Who it reaches. EQ2 grants at every tier of its tree and AAs
          especially do — the Predator line is rangers AND assassins — so this
          is not a class field, it is a tier field. Picking `predator` files
          the ability under both without writing it twice. Groups come first
          because "Predator AA or Ranger one?" is the actual question. */}
      <div className="rulerow">
        <label>granted to</label>
        <span className="seg tiers">
          {targets.map((t) => (
            <button key={t.name} type="button"
                    className={`${picked.has(t.name) ? 'on' : ''} tier-${t.tier}`}
                    title={t.covers.length > 1 ? t.label : `${t.name} only`}
                    onClick={() => setCls(toggle(cls, t.name))}>
              {t.name}
            </button>
          ))}
        </span>
      </div>
      {cls && (
        <div className="rulerow">
          <label />
          <span className="note reaches">
            reaches {[...reach].sort().join(', ')}
          </span>
        </div>
      )}
      <div className="rulerow">
        <label htmlFor={`nt-${a.ability}`}>note</label>
        <input id={`nt-${a.ability}`} value={note} placeholder="optional — why you decided this"
               onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="rulerow acts">
        <p className="decisionpreview">Save: <b>{a.ability}</b> belongs to <b>{unit}</b>, <b>{fires === 'proc' ? 'fires on its own' : 'is pressed'}</b>{kind !== 'unknown' ? `, and is granted by ${kind}${name ? ` ${name}` : ''}` : ', with an unknown grant'}.</p>
        <button className="btn solid" disabled={busy}
                onClick={() => onSave({ unit, fires, grant_kind: kind, grant_name: name, grant_class: cls, note })}>
          {r ? 'Update' : 'Save'}
        </button>
        {r && <button className="btn" disabled={busy} onClick={onClear}>Clear ruling</button>}
      </div>
    </div>
  )
}

function AbilityRow({ a, targets, open, onToggle, onSave, onClear, busy }) {
  const [conf, confTitle] = CONF[a.confidence] ?? [a.confidence, '']
  return (
    <div className={`abrow ${open ? 'open' : ''}`}>
      <button className="abhead" onClick={onToggle} aria-expanded={open}>
        <span className="abname">{a.ability}</span>
        <span className={`badge sug-${a.suggest}`}>{a.suggest}</span>
        <span className={`badge conf-${a.confidence}`} title={confTitle}>{conf}</span>
        {a.grant_name && (
          <span className="muted grant" title={a.trigger}>
            {a.grant_name}{a.grant_class ? ` · ${a.grant_class}` : ''} {a.grant_kind}
          </span>
        )}
        <span className="dmg">{a.total_damage ? fmt.num(a.total_damage) : ''}</span>
      </button>
      {open && (
        <div className="abbody">
          <p className="note">{a.why}</p>
          <Evidence a={a} />
          <Ruler a={a} targets={targets} onSave={onSave} onClear={onClear} busy={busy} />
        </div>
      )}
    </div>
  )
}

export default function AdminAbilities({ user }) {
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState(null)
  const [q, setQ] = useState(params.get('q') ?? '')
  const status = params.get('status') || 'unreviewed'
  const suggestion = params.get('suggestion') || ''
  const confidence = params.get('confidence') || ''
  const className = params.get('class') || ''
  const evidence = params.get('evidence') || ''
  const sort = params.get('sort') || 'most_evidence'
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const [undo, setUndo] = useState(null)

  const refresh = useCallback(() => {
    api.adminAbilities({ q, scope: 'all', status, suggestion, confidence, className, evidence, sort })
      .then((d) => { setData(d); setSelected((s) => d.items.find((a) => a.ability === s?.ability) || d.items[0] || null) })
      .catch((e) => setError(e.message))
  }, [q, status, suggestion, confidence, className, evidence, sort])
  useEffect(() => { refresh() }, [refresh])

  const setFilter = (key, value) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value); else next.delete(key)
    setParams(next, { replace: true })
  }
  useEffect(() => { const t = setTimeout(() => setFilter('q', q.trim()), 250); return () => clearTimeout(t) }, [q]) // eslint-disable-line react-hooks/exhaustive-deps

  async function save(name, body) {
    setBusy(true); setError(null); setMsg(null)
    try {
      const before = selected?.ruling
      await api.adminRuleAbility(name, body)
      setMsg(`${name} — saved`); setUndo({ name, before })
      const i = data.items.findIndex((a) => a.ability === name)
      setSelected(data.items[i + 1] || data.items[i - 1] || null)
      await refresh()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function clear(name, quiet = false) {
    setBusy(true); setError(null); setMsg(null)
    try {
      await api.adminUnruleAbility(name)
      if (!quiet) setMsg(`${name} — ruling cleared`)
      await refresh()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function undoLast() {
    if (!undo) return
    if (undo.before) await api.adminRuleAbility(undo.name, undo.before)
    else await api.adminUnruleAbility(undo.name)
    setMsg(`${undo.name} — change undone`); setUndo(null); refresh()
  }

  return (
    <div className="manage abilities">
      <div className="pagehead">
        <div><h1>Abilities</h1><p className="muted">Review consecutive game-knowledge decisions without losing your queue.</p></div>
        <div className="actions">{user?.role === 'admin' && <Link to="/admin">Admin dashboard</Link>}<Link to="/admin/timers">AoE timers</Link></div>
      </div>

      {error && <p className="error">{error}</p>}
      {msg && <p className="ok">{msg} {undo && <button className="chip" onClick={undoLast}>Undo</button>}</p>}

      <div className="abilitysummary">
        <div><b>{data?.open_count ?? '—'}</b><span>remaining</span></div>
        <div><b>{data?.reviewed_today ?? '—'}</b><span>reviewed today</span></div>
        <div><b>{data?.confidence_counts?.medium ?? '—'}</b><span>medium confidence</span></div>
        <div><b>{data?.confidence_counts?.low ?? '—'}</b><span>low confidence</span></div>
      </div>

      <div className="workbenchfilters abilityfilters">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search all tracked abilities…" />
        <select value={status} onChange={(e) => setFilter('status', e.target.value)}><option value="unreviewed">Unreviewed</option><option value="ruled">Ruled</option><option value="curated">Curated seed</option><option value="all">All</option></select>
        <select value={suggestion} onChange={(e) => setFilter('suggestion', e.target.value)}><option value="">Any suggestion</option><option value="pet">Pet</option><option value="player">Player</option><option value="proc">Proc</option><option value="unclear">Unclear</option></select>
        <select value={confidence} onChange={(e) => setFilter('confidence', e.target.value)}><option value="">Any confidence</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select>
        <input value={className} onChange={(e) => setFilter('class', e.target.value)} placeholder="Class or unclassed" />
        <select value={evidence} onChange={(e) => setFilter('evidence', e.target.value)}><option value="">Any evidence</option><option value="conflicting">Conflicting</option><option value="prepare">Has prepare lines</option><option value="no_reference">No Census/wiki match</option></select>
        <select value={sort} onChange={(e) => setFilter('sort', e.target.value)}><option value="most_evidence">Most evidence</option><option value="least_evidence">Least evidence</option><option value="damage">Highest damage</option><option value="alphabetical">Alphabetical</option></select>
      </div>

      {!data ? <p className="note">Loading…</p> : <div className="masterdetail abilityworkbench">
        <div className="masterlist">{data.items.map((a) => <button key={a.ability} className={selected?.ability === a.ability ? 'on' : ''} onClick={() => setSelected(a)}>
          <span><b>{a.ability}</b><small>{a.classes.join(', ') || 'Unclassed'}</small></span><span><span className={`badge sug-${a.suggest}`}>{a.suggest}</span><small>{a.confidence}</small></span>
        </button>)}</div>
        <div className="abilitydetail">{selected ? <div key={selected.ability}>
          <h2>{selected.ability}</h2>
          <p className="note">{selected.why}</p>
          {selected.ruling && <p className="muted small">Current ruling by {selected.ruling.decided_by || 'unknown'} on {fmt.date(selected.ruling.decided_ts)}.</p>}
          <Evidence a={selected} />
          <p className="decisionpreview">This ability belongs to <b>{selected.ruling?.unit || selected.suggest}</b> and <b>{selected.ruling?.fires || (selected.suggest === 'proc' ? 'fires on its own' : 'is cast')}</b>.</p>
          <Ruler a={selected} targets={data.grant_targets ?? []} onSave={(body) => save(selected.ability, body)} onClear={() => clear(selected.ability)} busy={busy} />
        </div> : <p className="muted">Nothing matches this queue.</p>}</div>
        </div>
      }
    </div>
  )
}
