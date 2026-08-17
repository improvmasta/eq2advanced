import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import ActorPanel from './ActorPanel.jsx'
import AoePanel from './AoePanel.jsx'
import ClassPanel from './ClassPanel.jsx'
import ComparePanel from './ComparePanel.jsx'
import DeathList from './DeathList.jsx'
import ErrorBoundary from './ErrorBoundary.jsx'
import { ActorName } from './Identity.jsx'
import LootPanel from './LootPanel.jsx'
import SelectionBar from './SelectionBar.jsx'
import SortableTable from './SortableTable.jsx'
import TankDeaths, { hasTankDeath } from './TankDeaths.jsx'
import Tabs from './Tabs.jsx'
import TimelineChart from './TimelineChart.jsx'
import { api, fmt, peek, url } from '../lib/api.js'
import { CHART_COLORS, ROLES, ROLE_LABEL, roleOf } from '../lib/classes.js'
import {
  MIN_PEERS, autoPct, critPct, damageDerived, deathRows, maxByActor, procPct,
  rankColor, rankScale, rankTitle, reportRollup,
} from '../lib/stats.js'
import { useQueryState } from '../lib/useQueryState.js'

/* THE PARSE: a set of fights, as tabs of tables with a drilldown beside them.

   This is what `/zones/:id` is, minus the page around it — the raid's title,
   its fight rail, the edit and share controls. All of that is the RAID PAGE's
   business, and it is handed in as `rail`, so the same parse can be read
   somewhere that already has its own navigation.

   The other reader is the raid dashboard (`Live.jsx`). A fight that has ended
   there is a record like any other, and it used to render as a cut-down
   "recap" of its own — two tables, no tabs, no drilldown — which meant the
   thing you looked at ten seconds after a pull was NOT the thing you looked at
   the next morning, and the columns everyone argues about (max hit, crit,
   engage, damage lost dead) were only on one of them. One parse, both places.

   What it needs is the selection (`selIds`) and the raid report for it. The
   report is a prop rather than a fetch because its two callers ask different
   questions of the server: the raid page has a run and reads
   `/zone-runs/:id/report` once for the night, the dashboard has fights and
   reads `/encounters/report?ids=`.

   View state — the tab, the filters, what is checked, whose drilldown is open
   — lives in the URL (`useQueryState`), which is how a parse is a link. */

const PET_KINDS = new Set(['own_pet', 'swarm_pet', 'named_pet'])

/* Damage first: it is the tab everyone opens the page for, and an Overview
   that repeated four columns from each of the others was a stop on the way to
   the one you wanted. The metric block above the table carries what the
   Overview was actually read for, retuned per tab. */
/* Labels are the parser's own shorthand — a raider reads DPS/HPS/DEF faster
   than the words, and it is what the columns underneath are already called.
   The KEYS are untouched: ?tab=damage bookmarks and PANEL_KIND still work. */
const TABS = [
  { key: 'damage', label: 'DPS' },
  { key: 'healing', label: 'HPS' },
  { key: 'defense', label: 'DEF' },
  /* Dying is not a defensive statistic — it is the outcome the defensive ones
     were describing, and it reads as a list of events (who, in which fight,
     and what the last few seconds looked like) rather than a column. It had
     the bottom half of DEF; now it has the tab. */
  { key: 'deaths', label: 'DEATHS' },
  { key: 'aoes', label: 'AOE TIMERS' },
  { key: 'timeline', label: 'TIMELINE' },
  /* The stats only one class can answer — a troubador's buff uptime is not a
     column the other twenty-five classes can share. See ClassPanel.jsx. */
  { key: 'class', label: 'CLASS REPORT' },
  /* What the chests gave. Not a rate and not a column on anybody's row — it
     is the other thing a raid did that night, so it gets a tab rather than a
     corner of one, and it sits LAST because every tab before it is the parse.
     Always present, because "did this fight drop anything" is a question, and
     a tab that appears and disappears under the selection cannot answer it. */
  { key: 'loot', label: 'LOOT' },
  /* Insights is HIDDEN, not removed — the panel, the coach endpoint and the
     `tab === 'insights'` render below are all intact, and putting the entry
     back here is the whole of turning it on again. An old ?tab=insights
     bookmark lands on Damage while it is out, because `tab` is validated
     against this list. */
  // { key: 'insights', label: 'Insights' },
]

/* The page tab and a parse's kind tabs are the same question at two scales, so
   opening somebody from Healing opens their heals — landing on Damage and
   having to switch back was one click per raider you looked at. Only the tabs
   with a per-ability view map: from Defense or Timeline the panel keeps
   whatever it is on, which is Damage the first time. */
const PANEL_KIND = { damage: 'damage', healing: 'heal' }

/* Two windows, ONE request. A spike death is over in a couple of seconds, and
   twelve of them buried the moment that mattered under the whole pull — so the
   tank report looks at 5s and the raid list at 3s.
 
   The fetch asks for the wider of the two and the list narrows it in the
   browser, which is exact rather than approximate: `/deaths` caps each event
   list at DEATH_MAX_ENTRIES and keeps the TAIL, so the last 3s of a 5s window
   is always complete even when the 5s list was truncated. Two requests for two
   windows would have doubled the work to learn the same events twice. */
const TANK_WINDOW_S = 5
const RAID_WINDOW_S = 3

/* Offered, not shown. Each parse tab leads with its own rate and carries the
   other one folded away — the Columns menu is where you say you want it, and
   SortableTable remembers that answer for every raid you open next. Module
   scope so the array identity is stable: a fresh one per render would rebuild
   the table's hidden-column memo every time. */
const TAB_HIDDEN = { damage: ['hps'], healing: ['dps'] }

const healedOf = (a) => (a.heals || 0) + (a.wards_absorbed || 0)

/* What the engage clock was stopped by. A healer's fight starts with a heal;
   scoring only hostile actions read a templar's whole pull as absence.
   Exported because the raid page's Insights tab names the same anchors. */
export const ANCHOR_LABEL = {
  cast: 'cast start', ability: 'ability', autoattack: 'swing', pet: 'pet',
  heal: 'heal', cure: 'cure', rez: 'rez',
}

function kindBadge(kind) {
  if (kind === 'mob') return <span className="badge">mob</span>
  if (kind === 'other') return <span className="badge">env</span>
  if (PET_KINDS.has(kind)) return <span className="badge pet">pet</span>
  return null
}

export default function ParseView({
  selIds, report, rail, span, cmpPrefix, insights, notice, pickerSlot,
}) {
  const [detail, setDetail] = useState(null)
  const [detailErr, setDetailErr] = useState(null)
  const [stale, setStale] = useState(false)
  const [actorQ, setActorQ] = useQueryState('actor')
  const [tabQ, setTab] = useQueryState('tab', 'damage')
  // ?tab=overview is a real bookmark someone still has; land it on Damage
  const tab = TABS.some((t) => t.key === tabQ) ? tabQ : 'damage'
  const [cmpQ, setCmpQ] = useQueryState('cmp')
  const [q, setQ] = useQueryState('q')
  const [rolesQ, setRolesQ] = useQueryState('roles')
  // which class the Class tab is showing — in the URL, so "look at what the
  // troubs did on this night" is a link like every other selection here
  const [clsQ, setClsQ] = useQueryState('cls')
  const [healedOpen, setHealedOpen] = useState(false)
  const [showNpcs, setShowNpcs] = useState(false)
  const [showPets, setShowPets] = useState(false)
  const [metric, setMetric] = useState('damage')
  const [timeline, setTimeline] = useState(null)
  const [timelineErr, setTimelineErr] = useState(null)
  const [aoeData, setAoeData] = useState(null)
  const [aoeErr, setAoeErr] = useState(null)
  const [lootData, setLootData] = useState(null)
  const [lootErr, setLootErr] = useState(null)
  const [classData, setClassData] = useState(null)
  const [classErr, setClassErr] = useState(null)
  const [recaps, setRecaps] = useState(null)
  /* The selection as one string: every request, memo and reset key below is
     about a SET of fights, and the array is a fresh one every render. */
  const idKey = (selIds || []).join(',')

  useEffect(() => {
    if (!selIds || !selIds.length) { setDetail(null); return }
    let gone = false
    const hit = peek(url.agg(selIds))
    if (hit) { setDetail(hit); setDetailErr(null); setStale(false); return }
    setStale(true)
    setDetailErr(null)
    api.encountersAgg(selIds)
      .then((d) => { if (!gone) { setDetail(d); setStale(false) } })
      .catch((e) => { if (!gone) { setDetailErr(e.message); setDetail(null); setStale(false) } })
    return () => { gone = true }
  }, [idKey])

  useEffect(() => {
    if (tab !== 'timeline' || !selIds?.length) return
    let gone = false
    const hit = peek(url.timeline(selIds))
    setTimeline(hit)
    setTimelineErr(null)
    if (hit) return
    api.encountersTimeline(selIds)
      .then((d) => { if (!gone) setTimeline(d) })
      .catch((e) => { if (!gone) setTimelineErr(e.message) })
    return () => { gone = true }
  }, [tab, idKey])

  useEffect(() => {
    if (tab !== 'aoes' || !selIds?.length) return
    let gone = false
    const hit = peek(url.aoes(selIds))
    setAoeData(hit)
    setAoeErr(null)
    if (hit) return
    api.encountersAoes(selIds)
      .then((d) => { if (!gone) setAoeData(d) })
      .catch((e) => { if (!gone) setAoeErr(e.message) })
    return () => { gone = true }
  }, [tab, idKey])

  useEffect(() => {
    if (tab !== 'loot' || !selIds?.length) return
    let gone = false
    const hit = peek(url.loot(selIds))
    setLootData(hit)
    setLootErr(null)
    if (hit) return
    api.encountersLoot(selIds)
      .then((d) => { if (!gone) setLootData(d) })
      .catch((e) => { if (!gone) setLootErr(e.message) })
    return () => { gone = true }
  }, [tab, idKey])

  useEffect(() => {
    if (tab !== 'class' || !selIds?.length) return
    let gone = false
    const hit = peek(url.classStats(selIds))
    setClassData(hit)
    setClassErr(null)
    if (hit) return
    api.encountersClassStats(selIds)
      .then((d) => { if (!gone) setClassData(d) })
      .catch((e) => { if (!gone) setClassErr(e.message) })
    return () => { gone = true }
  }, [tab, idKey])

  useEffect(() => {
    if (tab !== 'deaths' || !selIds?.length) return
    let gone = false
    // clear it: the deaths request is slower than /agg, so keeping the old
    // payload would render deaths from fights that are no longer selected
    const hit = peek(url.deaths(selIds, TANK_WINDOW_S))
    setRecaps(hit)
    if (hit) return
    api.encountersDeaths(selIds, TANK_WINDOW_S)
      .then((d) => { if (!gone) setRecaps(d) })
      .catch(() => { if (!gone) setRecaps(null) })
    return () => { gone = true }
  }, [tab, idKey])

  const repRows = useMemo(() => reportRollup(report, selIds), [report, idKey])
  const derived = useMemo(() => damageDerived(detail?.abilities), [detail])
  const maxHeal = useMemo(() => maxByActor(detail?.abilities, 'heal'), [detail])
  /* Self-inflicted damage (the Bloodthirsty Choker's Vampiric Requiem, and
     every other cost-you-HP effect) is NOT damage taken — ACT excludes it from
     both Damage and DamageTaken and so does the roller. It is still real HP a
     healer had to cover, so the DmgTaken column marks it with a * and hands
     over the number on hover rather than pretending it never happened. */
  const selfDamage = useMemo(() => {
    const by = {}
    for (const r of detail?.abilities || []) {
      if (r.kind !== 'self') continue
      const k = r.rollup_key || r.source_key
      if (k) by[k] = (by[k] || 0) + (r.total || 0)
    }
    return by
  }, [detail])
  const deaths = useMemo(() => deathRows(report, selIds), [report, idKey])

  const actors = detail?.actors ?? []
  const duration = Math.max(detail?.encounter?.duration_s || 0, 1)
  const players = useMemo(() => actors.filter((a) => a.kind === 'player'), [actors])
  /* The three raid-wide denominators, together and BEFORE their first reader:
     the selection bar's "% of raid" picks one by tab, and it renders above the
     header block that also uses them. */
  const raidDamage = players.reduce((s, a) => s + (a.damage || 0), 0)
  const raidHealed = players.reduce((s, a) => s + healedOf(a), 0)
  const raidTaken = players.reduce((s, a) => s + (a.damage_taken || 0), 0)

  // checked-off combatants for comparison, order preserved in the URL
  const cmpList = useMemo(() => (cmpQ || '').split(',').filter(Boolean), [cmpQ])
  const cmpKeys = useMemo(() => new Set(cmpList), [cmpList])
  /* The panel follows the checks — an explicit ?actor is only how a mob or a
     pet row (which has no checkbox) opens one. Clicking a name and ticking a
     box are DIFFERENT gestures: a click is "show me this one instead", a tick
     is "and this one too". */
  const soloActor = cmpList.length === 1 && actors.some((a) => a.key === cmpList[0])
    ? cmpList[0] : null
  const selectedActor = actorQ && actors.some((a) => a.key === actorQ) ? actorQ : soloActor
  const selName = actors.find((a) => a.key === selectedActor)?.name

  const toggleCmp = (key) => {
    const had = cmpKeys.has(key)
    const next = had ? cmpList.filter((k) => k !== key) : [...cmpList, key]
    setCmpQ(next.length ? next.join(',') : null)
    /* Hand the panel back to the checks: unticking the raider on screen closes
       theirs, and ticking anyone drops a mob or pet drilldown that was open —
       either way what is checked is what you are looking at. */
    if (!had || actorQ === key) setActorQ(null)
  }
  /* Clicking a raider's name REPLACES what the panel is showing — reading down
     a raid table is one parse after another, and having each click add a
     column meant three names in and the parse you wanted was a third of a
     screen wide. Adding is the checkbox's job, and only its job. Clicking the
     one already open closes it, so a click still undoes itself. */
  const focusActor = (key) => {
    setActorQ(null)
    const only = cmpList.length === 1 && cmpList[0] === key
    setCmpQ(only ? null : key)
  }
  // checking rows sums them in the header box; a second check is already the
  // ask to compare, so the table comes up with it — no extra button
  const checkedActors = useMemo(
    () => cmpList.map((k) => actors.find((a) => a.key === k)).filter(Boolean),
    [cmpList, actors])
  // gate on actors that exist in THIS selection — a checked raider who wasn't
  // in the fight you just clicked must not reserve an empty panel column
  const comparing = checkedActors.length >= 2
  const actorsByKey = useMemo(
    () => Object.fromEntries(actors.map((a) => [a.key, a])), [actors])
  /* The chart plots what you checked; with nothing checked it opens on the top
     few for the METRIC you are looking at — switching to Healing and being
     shown the top five damage dealers (who heal nothing) was an empty chart
     with lines in it. */
  const timelineKeys = useMemo(() => {
    if (cmpList.length) return cmpList.slice(0, CHART_COLORS.length)
    const field = ['heals', 'taken'].includes(metric) ? metric : 'damage'
    return (timeline?.series || [])
      .filter((s) => s.kind === 'player')
      .map((s) => ({ key: s.key, v: (s[field] || []).reduce((a, b) => a + b, 0) }))
      .filter((s) => s.v > 0)
      .sort((a, b) => b.v - a.v)
      .slice(0, 5)
      .map((s) => s.key)
  }, [cmpList, timeline, metric])

  /* The summing calculator: whatever is checked, added up. Selecting the three
     mages answers "are they carrying their share together?" without exporting
     anything to a spreadsheet. */
  const selectionStats = useMemo(() => {
    const sum = (get) => checkedActors.reduce((s, a) => s + (get(a) || 0), 0)
    const dmg = sum((a) => a.damage)
    const heal = sum((a) => (a.heals || 0) + (a.wards_absorbed || 0))
    const taken = sum((a) => a.damage_taken)
    /* "% of raid" is a share of whatever the TAB is about. Checking three
       healers on the Healing tab and being told their share of raid DAMAGE is
       a true number answering a question nobody asked. */
    const shares = {
      damage: ['damage', dmg, raidDamage],
      healing: ['healing', heal, raidHealed],
      defense: ['damage taken', taken, raidTaken],
    }
    const [what, part, whole] = shares[tab] || shares.damage
    const dps = { k: 'DPS', v: dmg ? fmt.num2(dmg / duration) : null }
    const hps = { k: 'HPS', v: heal ? fmt.num2(heal / duration) : null }
    return [
      // the tab's own rate leads; the other one still shows if it is non-zero
      ...(tab === 'healing' ? [hps, dps] : [dps, hps]),
      {
        k: '% of raid', v: part && whole ? `${((part / whole) * 100).toFixed(1)}%` : null,
        title: `Combined share of raid ${what}`,
      },
      { k: 'Deaths', v: sum((a) => a.deaths) || null },
    ]
  }, [checkedActors, duration, tab, raidDamage, raidHealed, raidTaken])

  const roleSet = useMemo(
    () => new Set((rolesQ || '').split(',').filter(Boolean)), [rolesQ])
  const toggleRole = (r) => {
    const next = new Set(roleSet)
    if (next.has(r)) next.delete(r); else next.add(r)
    setRolesQ(next.size ? [...next].join(',') : null)
  }
  const rolesPresent = useMemo(() => {
    const seen = new Set(players.map((a) => roleOf(a)).filter(Boolean))
    return ROLES.filter((r) => seen.has(r))
  }, [players])
  /* Search and role are view filters, not row semantics — they narrow whatever
     the active tab already decided to show. */
  const applyFilters = (rows) => {
    const needle = (q || '').trim().toLowerCase()
    return rows.filter((a) => {
      if (needle && !a.name.toLowerCase().includes(needle)
          && !(a.class || '').includes(needle)) return false
      if (roleSet.size && a.kind === 'player' && !roleSet.has(roleOf(a))) return false
      return true
    })
  }

  const enc = detail?.encounter

  /* Who gets a row, in two independent parts: the SWITCHES say which kinds of
     combatant may appear at all, and the tab says what a row has to carry to
     earn its place. Both off — the default — and every tab is the raid, which
     is what these tables were before the switches reached past Defense.

     A mob is a combatant with a real parse: the boss's damage, what it healed
     itself for, what the raid put into it. Clicking its row opens that parse
     in the panel exactly like a raider's. What a mob does NOT get is a share
     of the raid denominators or a rank color — those are questions about
     raiders — so those cells simply stay blank on its row.

     Pets are the quieter half. An owned pet's damage is already credited to
     its owner (ACT does the same), so its row usually carries only what it
     TOOK — that is why "Tragedy's unswerving hammer" turns up owning nothing
     but a DmgTaken figure. A dumbfire nobody owns keeps its own damage. */
  const kindAllowed = (a) => {
    if (a.kind === 'player') return true
    if (PET_KINDS.has(a.kind)) return showPets
    return showNpcs
  }
  const rowsFor = (carries) =>
    applyFilters(actors.filter((a) => kindAllowed(a) && carries(a)))

  /* What a mob has to CARRY to earn a row on the damage tab, and it is not
     damage dealt.

     A mob that dealt nothing still has a parse — how much the raid put into it
     is the whole reading — and plenty of them deal nothing at all: anything
     that dies before it swings, and anything killed by somebody far enough
     above it that it never lands a hit. Requiring `damage > 0` therefore made
     the NPCs switch look broken exactly where it is easiest to test it (a
     guard in Freeport: 0 dealt, 27k taken, no row). Raiders keep the old test,
     because a raider with no damage on the damage tab IS noise. */
  const carriesDamage = (a) => (a.damage || 0) > 0
    || (a.kind !== 'player' && !PET_KINDS.has(a.kind) && (a.damage_taken || 0) > 0)

  const tabRows = {
    damage: rowsFor(carriesDamage),
    healing: rowsFor((a) => healedOf(a) > 0 || (a.cure_count || 0) > 0
      || (a.power_fed || 0) > 0 || (a.rez_casts || 0) > 0),
    defense: rowsFor((a) => (a.damage_taken || 0) > 0
      || (a.kind === 'player' && (a.deaths || 0) > 0)),
    // who it happened to and who picked them back up — a raider who neither
    // died nor cast a rez has nothing to say on this tab, and neither does a
    // mob: a death is only counted against a player or their pet
    deaths: rowsFor((a) => (a.deaths || 0) > 0 || (a.rez_casts || 0) > 0),
  }
  const currentRows = tabRows[tab] || tabRows.damage

  /* ---------- shared cell helpers (tooltips live here) ---------- */

  /* A number alone says nothing — "34% crit" is good or bad only next to the
     people it should be compared against. Peers are the same-role raiders on
     screen, so the coloring answers to the current filter, and the tint is the
     row's PLACE in that group (see rankScale).

     A row with no role gets no color. It used to fall back to the whole raid's
     median, which meant one column carried up to four different yardsticks at
     once — a healer judged against healers sat beside an unclassified raider
     judged against everybody, and the reader had no way to tell which was
     which. A third of the roster has no class (Census covers about half the
     ability names), so that fallback was not an edge case. Same for a group
     under MIN_PEERS: three tanks are not a standing. */
  const rankPool = (a) => {
    if (a.kind !== 'player') return null
    const role = roleOf(a)
    if (!role) return null
    const pool = currentRows.filter((p) => roleOf(p) === role)
    return pool.length >= MIN_PEERS ? { pool, label: ROLE_LABEL[role].toLowerCase() } : null
  }
  const rankAgainst = (get, opts) => (a) => {
    const group = rankPool(a)
    if (!group) return undefined
    const color = rankColor(rankScale(get(a), group.pool.map(get), opts))
    return color ? { color } : undefined
  }
  /* Say what the color means where it is: "3rd of 7 healers" is checkable
     against the column the reader is already looking at. */
  const rankTitleAgainst = (get) => (a) => {
    const group = rankPool(a)
    return group ? rankTitle(get(a), group.pool.map(get), group.label) : undefined
  }

  const damageTitle = (a) => {
    const d = derived[a.key]
    const parts = []
    if (d?.hits) parts.push(`crit ${Math.round(critPct(d))}%`)
    const ap = autoPct(d)
    if (ap != null) parts.push(`autoattack ${Math.round(ap)}%`)
    return parts.join(' · ') || undefined
  }
  const takenTitle = (a) => {
    const self = selfDamage[a.key]
    const parts = [`${fmt.num(a.damage_taken || 0)} taken from enemies`]
    if (self) parts.push(`${fmt.num(self)} self-inflicted (choker and the like) — not counted`)
    // this cell renders its own tooltip, so the rank has to join it rather
    // than sit on the td underneath where it would never be seen
    const rank = rankTitleAgainst((p) => p.damage_taken || 0)(a)
    if (rank) parts.push(rank)
    return parts.join(' · ')
  }
  const healedTitle = (a) => {
    const n = repRows?.[a.name]
    const parts = [`heals ${fmt.num(a.heals || 0)}`, `wards ${fmt.num(a.wards_absorbed || 0)}`]
    if (n?.overheal_est) parts.push(`overheal ${fmt.num(n.overheal_est)}`)
    if (a.ward_bleedthrough) parts.push(`bleedthrough ${fmt.num(a.ward_bleedthrough)}`)
    return parts.join(' · ')
  }
  const deathsTitle = (a) => {
    const n = repRows?.[a.name]
    const parts = []
    if (n?.deaths) parts.push(`dead ${fmt.dur(n.time_dead_s)} · ~${fmt.num(n.death_dps_lost)} damage lost`)
    /* A number the log did not print has to say so where it is read. EQ2
       announces a death by naming its killer, and a self-inflicted one
       (Lifeburn into a proc) has nobody to name — so the site reads it off the
       hole it left instead (backend `pipeline/downs.py`). */
    if (a.deaths_inferred) {
      parts.push(`${a.deaths_inferred} with no death line in the log — recovered `
        + 'from the gap in what they did, because a self-inflicted death names no killer')
    }
    return parts.join(' · ') || undefined
  }
  /* Engage is a claim about someone's opener, so the tooltip says what it was
     measured from — first hit, first heal, first cure — and how many pulls are
     behind the average. A number this easy to argue with has to be able to
     answer "says who?". */
  const engageTitle = (a) => {
    const n = repRows?.[a.name]
    if (!n?.engage?.length) return undefined
    const mix = Object.entries(n.engage_anchors || {})
      .sort((x, y) => y[1] - x[1])
      .map(([k, v]) => `${v} × ${ANCHOR_LABEL[k] || k}`)
      .join(', ')
    const low = n.engage_low ? ` · ${n.engage_low} inside the opening 2s (may be a proc or a HoT tick)` : ''
    return `first action on ${n.engage.length} named pull${n.engage.length > 1 ? 's' : ''}`
      + `${mix ? `: ${mix}` : ''}${low}`
  }

  /* Fixed: the name is what every other cell is about, so it never moves and
     never hides. Everything else is the reader's to arrange. */
  const nameCol = {
    key: 'name', label: 'Name', align: 'l', fixed: true,
    render: (a) => <ActorName actor={a} badge={kindBadge(a.kind)} />,
    sortValue: (a) => a.name,
  }
  const shareCol = {
    key: 'share', label: 'Dmg %',
    render: (a) => (a.kind === 'player' && a.damage > 0 && raidDamage
      ? `${Math.round((a.damage / raidDamage) * 100)}%` : ''),
    sortValue: (a) => (a.kind === 'player' ? a.damage : -1),
  }
  const rep = (name, label, get, renderVal) => ({
    key: name, label,
    render: (a) => {
      const v = repRows?.[a.name] ? get(repRows[a.name]) : null
      return v != null && v !== 0 ? (renderVal ? renderVal(v) : fmt.num(v)) : ''
    },
    sortValue: (a) => (repRows?.[a.name] ? get(repRows[a.name]) : null),
  })
  const engageCol = {
    key: 'engage', label: 'Engage',
    render: (a) => {
      const v = repRows?.[a.name]?.avg_engage_delay_s
      return v != null ? <span title={engageTitle(a)}>{v}s</span> : ''
    },
    sortValue: (a) => repRows?.[a.name]?.avg_engage_delay_s ?? null,
  }
  const damageCol = {
    key: 'damage', label: 'Damage',
    render: (a) => <span title={damageTitle(a)}>{fmt.num(a.damage)}</span>,
  }
  const healedCol = {
    key: 'healed', menuLabel: 'Healed',
    label: (
      <span>
        Healed{' '}
        <button
          className="expandcol"
          onClick={(e) => { e.stopPropagation(); setHealedOpen((v) => !v) }}
          title={healedOpen ? 'Collapse heal breakdown' : 'Expand into Heals / Wards / Overheal'}
        >{healedOpen ? '⊟' : '⊞'}</button>
      </span>
    ),
    render: (a) => (healedOf(a) ? <span title={healedTitle(a)}>{fmt.num(healedOf(a))}</span> : ''),
    sortValue: healedOf,
  }
  const healedBreakdown = healedOpen ? [
    { key: 'heals', label: '· Heals', render: (a) => (a.heals ? fmt.num(a.heals) : ''), sortValue: (a) => a.heals || 0 },
    { key: 'wards_absorbed', label: '· Wards', render: (a) => (a.wards_absorbed ? fmt.num(a.wards_absorbed) : ''), sortValue: (a) => a.wards_absorbed || 0 },
    rep('overheal', '· Overheal', (n) => n.overheal_est),
  ] : []
  const deathsCol = {
    key: 'deaths', label: 'Deaths',
    render: (a) => (a.deaths
      ? (
        <span title={deathsTitle(a)}>
          {a.deaths}
          {a.deaths_inferred ? <sup className="derived">†</sup> : null}
        </span>
      )
      : ''),
  }
  /* Time dead and rezzes belong next to the deaths that caused them, on the
     tab people actually land on — a death costs the raid twice, once in the
     damage nobody dealt and once in the healer who stopped healing to fix it,
     and neither cost is visible from a Deaths count alone. */
  const timeDeadCol = rep('time_dead', 'Time dead', (n) => n.time_dead_s, (v) => fmt.dur(v))
  const rezCol = { key: 'rez_casts', label: 'Rezzes', render: (a) => a.rez_casts || '' }
  /* "X intercepted some of the damage intended for you!" — a hit somebody
     else was supposed to take. The log never says how much, so this is a
     count and the tooltip has to say why there is no number next to it. */
  const interceptCol = {
    key: 'intercepts', label: 'Intercepts',
    render: (a) => (a.intercepts
      ? <span title="Hits taken for someone else. The log does not say how much damage was moved.">
          {a.intercepts}
        </span>
      : ''),
    sortValue: (a) => a.intercepts || 0,
  }

  const dpsOf = (a) => (a.damage || 0) / duration
  const dpsCol = {
    key: 'dps', label: 'DPS',
    render: (a) => (a.damage ? fmt.num2(dpsOf(a)) : ''),
    sortValue: dpsOf,
  }
  const hpsCol = {
    key: 'hps', label: 'HPS',
    render: (a) => (healedOf(a) ? fmt.num2(healedOf(a) / duration) : ''),
    sortValue: (a) => healedOf(a) / duration,
  }

  const overhealPct = (a) => {
    const n = repRows?.[a.name]
    const healed = (a.heals || 0) + (n?.overheal_est || 0)
    return healed && n?.overheal_est ? (100 * n.overheal_est) / healed : null
  }
  /* Rate first, everywhere. DPS is the number the tables get read for, so it
     sits where the eye lands after the name instead of behind the totals it
     summarizes; the healing tab leads with its own rate for the same reason.
     Class is gone as a column — ActorName already carries the class chip, so
     it was the same fact printed twice across the widest table on the page. */
  const damageCols = [
    nameCol,
    { ...dpsCol, cellStyle: rankAgainst(dpsOf), cellTitle: rankTitleAgainst(dpsOf) },
    /* The other tab's rate, hidden by default and one tick away: a shadowknight
       who healed 400k while topping the parse is a fact about the DAMAGE tab,
       and reading it meant switching tabs and finding the row again. Next to
       the rate it belongs beside, so turning it on reads as one pair. */
    hpsCol,
    damageCol,
    shareCol,
    /* The biggest single hit of the fight — ACT's Max Hit, and the one number
       a rate cannot stand in for: 3M in one nuke and 3M of DoT ticks are the
       same DPS, and only one of them is the thing people are asking about. */
    {
      key: 'max_hit', label: 'Max hit',
      render: (a) => { const v = derived[a.key]?.max; return v ? fmt.num(v) : '' },
      sortValue: (a) => derived[a.key]?.max ?? null,
      cellStyle: rankAgainst((a) => derived[a.key]?.max ?? null),
      cellTitle: rankTitleAgainst((a) => derived[a.key]?.max ?? null),
    },
    {
      key: 'crit', label: 'Crit %',
      render: (a) => { const v = critPct(derived[a.key]); return v != null ? `${Math.round(v)}%` : '' },
      sortValue: (a) => critPct(derived[a.key]),
      cellStyle: rankAgainst((a) => critPct(derived[a.key])),
      cellTitle: rankTitleAgainst((a) => critPct(derived[a.key])),
    },
    {
      key: 'auto', label: 'Auto %',
      render: (a) => { const v = autoPct(derived[a.key]); return v != null ? `${Math.round(v)}%` : '' },
      sortValue: (a) => autoPct(derived[a.key]),
    },
    {
      key: 'proc', label: 'Proc %',
      render: (a) => { const v = procPct(derived[a.key]); return v != null && v > 0 ? `${Math.round(v)}%` : '' },
      sortValue: (a) => procPct(derived[a.key]),
    },
    {
      key: 'avg_delay', label: 'AvgDelay',
      render: (a) => (a.avg_delay_s != null ? a.avg_delay_s.toFixed(2) : ''),
      sortValue: (a) => a.avg_delay_s ?? null,
    },
    /* ACT's AvgDelay is the gap between things LANDING, so a DoT ticking six
       times and an AoE hitting six mobs read as six actions. This one counts
       activations instead — the gap between button presses. */
    {
      key: 'avg_delay_adj', label: 'AvgDelay adj',
      render: (a) => (
        a.avg_delay_adj_s != null
          ? <span title={`${a.presses} activations — DoT ticks and extra AoE targets folded in`}>
              {a.avg_delay_adj_s.toFixed(2)}
            </span>
          : ''),
      sortValue: (a) => a.avg_delay_adj_s ?? null,
    },
    /* The cost of dying reads in that order: it happened, it lasted this long,
       and this is what it took off the parse. */
    deathsCol,
    timeDeadCol,
    rep('dead_loss', 'Dmg lost dead', (n) => n.death_dps_lost),
    engageCol,
  ]

  const healingCols = [
    nameCol,
    hpsCol,
    dpsCol,                       // default-hidden, same bargain as HPS on Damage
    healedCol,
    ...healedBreakdown,
    ...(healedOpen ? [] : [
      { key: 'heals_plain', label: 'Heals', render: (a) => (a.heals ? fmt.num(a.heals) : ''), sortValue: (a) => a.heals || 0 },
      { key: 'wards_plain', label: 'Wards', render: (a) => (a.wards_absorbed ? fmt.num(a.wards_absorbed) : ''), sortValue: (a) => a.wards_absorbed || 0 },
    ]),
    // the healing tab's own Max hit: the biggest single heal landed
    {
      key: 'max_heal', label: 'Max heal',
      render: (a) => (maxHeal[a.key] ? fmt.num(maxHeal[a.key]) : ''),
      sortValue: (a) => maxHeal[a.key] ?? null,
      cellStyle: rankAgainst((a) => maxHeal[a.key] ?? null),
      cellTitle: rankTitleAgainst((a) => maxHeal[a.key] ?? null),
    },
    {
      key: 'overheal_pct', label: 'Overheal %',
      render: (a) => {
        const n = repRows?.[a.name]
        const healed = (a.heals || 0) + (n?.overheal_est || 0)
        return healed && n?.overheal_est ? `${Math.round((100 * n.overheal_est) / healed)}%` : ''
      },
      sortValue: overhealPct,
      cellStyle: rankAgainst(overhealPct, { worse: true }),
      cellTitle: rankTitleAgainst(overhealPct),
    },
    { key: 'ward_bleedthrough', label: 'Bleedthrough', render: (a) => (a.ward_bleedthrough ? fmt.num(a.ward_bleedthrough) : '') },
    { key: 'cure_count', label: 'Cures', render: (a) => a.cure_count || '' },
    { key: 'power_fed', label: 'PowerRepl', render: (a) => (a.power_fed ? fmt.num(a.power_fed) : '') },
    { key: 'rez_casts', label: 'Rezzes', render: (a) => a.rez_casts || '' },
    rep('rez_delay', 'Rez delay', (n) => n.avg_rez_delay_s, (v) => `${v}s`),
  ]

  const takenCol = {
    key: 'damage_taken', label: 'DmgTaken',
    render: (a) => (
      <span title={takenTitle(a)}>
        {a.damage_taken ? fmt.num(a.damage_taken) : ''}
        {selfDamage[a.key] ? <span className="selfmark">*</span> : null}
      </span>
    ),
    sortValue: (a) => a.damage_taken || 0,
    cellStyle: rankAgainst((a) => a.damage_taken || 0, { worse: true }),
  }
  /* Defense is what the raid ATE and what it stopped. What dying then cost is
     the Deaths tab's subject, not a set of columns tacked on the end here. */
  const defenseCols = [
    nameCol,
    takenCol,
    interceptCol,
    { key: 'power_drain', label: 'PowerDrain', render: (a) => (a.power_drain ? fmt.num(a.power_drain) : '') },
  ]

  /* The cost of dying reads in that order: it happened, it lasted this long,
     this is what it took off the parse — and who got them back up. */
  const deathsCols = [
    nameCol,
    deathsCol,
    timeDeadCol,
    rep('dead_loss', 'Dmg lost dead', (n) => n.death_dps_lost),
    rezCol,
    rep('rez_delay', 'Rez delay', (n) => n.avg_rez_delay_s, (v) => `${v}s`),
  ]

  const tabCols = {
    damage: damageCols, healing: healingCols, defense: defenseCols, deaths: deathsCols,
  }
  /* With a drilldown open the raid table is a picker, not a report: it keeps
     the name and the one number the tab is sorted by, and hands the width to
     the player you actually opened. Every column comes back when you close it. */
  const leadCol = {
    damage: dpsCol, healing: hpsCol, defense: takenCol, deaths: deathsCol,
  }
  const tabSort = {
    damage: { key: 'dps', dir: 'desc' },
    healing: { key: 'hps', dir: 'desc' },
    defense: { key: 'damage_taken', dir: 'desc' },
    deaths: { key: 'deaths', dir: 'desc' },
  }

  const panelOpen = comparing || selectedActor
  /* The one close gesture. A raider's parse opens two ways — click the row (or
     its checkbox), which is a CHECK, or ?actor for a mob or pet, which has no
     checkbox — and the ✕ used to only know about the second: clearing actorQ
     did nothing when the panel was standing on a single ticked raider, so the
     button looked broken on the commonest path into the panel. Closing means
     closing, whichever way it was opened. */
  const closePanel = () => {
    setActorQ(null)
    if (cmpList.length) setCmpQ(null)
  }

  /* The header block stays — it is the one place the raid is a single number
     instead of a table — but what it counts follows the tab you are on. */
  const sumRep = (get) => Object.values(repRows || {})
    .reduce((s, n) => s + (get(n) || 0), 0)
  const raidHeals = players.reduce((s, a) => s + (a.heals || 0), 0)
  const raidOverheal = sumRep((n) => n.overheal_est)
  const raidSelf = players.reduce((s, a) => s + (selfDamage[a.key] || 0), 0)
  const raidCures = players.reduce((s, a) => s + (a.cure_count || 0), 0)
  const totalDeaths = players.reduce((s, a) => s + (a.deaths || 0), 0)
  /* Two clocks lead every tab, and the gap between them is the point: how long
     the night took from first pull to last, and how much of that was combat.
     Wall-clock can never be shorter than the fights inside it — a selection of
     fights out of a longer run is still bounded by its own ends. `span` is the
     fallback the caller knows and the parse does not: the raid's own clock,
     for a selection whose aggregate carries no ends of its own. */
  const rawSpan = (enc?.ended_ts ?? span?.ended_ts) - (enc?.started_ts ?? span?.started_ts)
  const raidSpan = Number.isFinite(rawSpan) ? Math.max(rawSpan, duration) : duration
  const timeTiles = [
    {
      k: 'Raid time', v: fmt.durHMS(raidSpan),
      title: 'First pull to last — combat and everything in between',
    },
    { k: 'Combat', v: fmt.durHMS(duration), title: 'The fights themselves, added up' },
  ]
  const extraTiles = selIds.length > 1 ? [{ k: 'Fights', v: selIds.length }] : []
  const damageTiles = [
    ...timeTiles,
    /* Rates carry two decimals everywhere, header included — this is the
       number people paste next to an ACT screenshot, and ACT prints
       EncDPS/EncHPS to two places. */
    { k: 'Raid DPS', v: fmt.num2(raidDamage / duration) },
    { k: 'Raid damage', v: fmt.num(raidDamage) },
    { k: 'Raiders', v: players.filter((p) => p.damage > 0 || p.heals > 0).length },
    ...extraTiles,
  ]
  const aoeRows = aoeData?.aoes || []
  const aoeCasts = aoeRows.reduce((s, a) => s + a.casts, 0)
  const aoeTargets = aoeRows.reduce(
    (s, a) => s + a.cast_list.reduce((t, c) => t + c.targets, 0), 0)
  const aoeBlocked = aoeRows.reduce((s, a) => s + a.blocked, 0)
  const headTiles = {
    damage: damageTiles,
    timeline: damageTiles,
    aoes: [
      ...timeTiles,
      { k: 'AoEs', v: aoeRows.length },
      { k: 'Casts', v: aoeCasts },
      { k: 'AoE damage', v: fmt.num(aoeRows.reduce((s, a) => s + a.damage, 0)) },
      {
        k: 'Covered', title: 'Share of AoE hits avoided or absorbed',
        v: aoeTargets ? `${Math.round((100 * aoeBlocked) / aoeTargets)}%` : '—',
      },
      ...extraTiles,
    ],
    healing: [
      ...timeTiles,
      { k: 'Raid healed', v: fmt.num(raidHealed), title: 'Heals plus wards absorbed' },
      { k: 'Raid HPS', v: fmt.num2(raidHealed / duration) },
      {
        k: 'Overheal', title: 'Estimated from HP-deficit reconstruction',
        v: raidHeals + raidOverheal
          ? `${Math.round((100 * raidOverheal) / (raidHeals + raidOverheal))}%` : '—',
      },
      { k: 'Cures', v: raidCures },
      ...extraTiles,
    ],
    defense: [
      ...timeTiles,
      { k: 'Damage taken', v: fmt.num(raidTaken) },
      ...(raidSelf ? [{
        k: 'Self-inflicted', v: fmt.num(raidSelf),
        title: 'Chokers and other costs you pay yourself — not counted as damage taken',
      }] : []),
      ...extraTiles,
    ],
    deaths: [
      ...timeTiles,
      { k: 'Deaths', v: totalDeaths },
      { k: 'Time dead', v: fmt.dur(sumRep((n) => n.time_dead_s)) },
      {
        k: 'Dmg lost dead', v: fmt.num(sumRep((n) => n.death_dps_lost)),
        title: 'What the raid would have done over the time it spent dead',
      },
      { k: 'Rezzes', v: players.reduce((s, a) => s + (a.rez_casts || 0), 0) },
      ...extraTiles,
    ],
    class: [
      ...timeTiles,
      { k: 'Classes', v: classData?.classes?.length ?? '—' },
      {
        k: 'Raiders', v: classData
          ? classData.classes.reduce((s, c) => s + c.actors.length, 0) : '—',
        title: 'Players whose class this parse could pin',
      },
      ...extraTiles,
    ],
  }

  /* The totals for what is checked — the head of the comparison column, and
     only that. One checked raider has nothing to add up, so it stays away
     until there are two. */
  const selHead = comparing ? (
    <SelectionBar
      head
      label={`${checkedActors.length} selected`}
      stats={selectionStats}
      onClear={() => setCmpQ(null)}
      chips={(
        <div className="selnames">
          {checkedActors.map((a) => (
            <button
              key={a.key}
              className="chip selname"
              title="Remove from selection"
              onClick={() => toggleCmp(a.key)}
            >
              {a.name} <span className="x">✕</span>
            </button>
          ))}
        </div>
      )}
    />
  ) : null

  /* The raider list, the tabs, and every panel that is not the drilldown.

     `pickerSlot` is the dashboard's answer to the layout below: there the
     fight rail belongs to the PAGE, not to the parse, so an open panel docks
     this column under that rail through a portal and the parse takes the
     middle of the screen — the raid page's shape, assembled across two
     components that cannot nest. One drilldown or a comparison, no difference:
     the same gesture puts the list beside the rail and the parse in the middle,
     which is exactly the rule `.workspace.withpanel` follows. */
  const docked = !!(panelOpen && detail && pickerSlot)
  const mainCol = (
    <div className={`wsmain${docked ? ' wsdock' : ''} ${stale && detail ? 'stale' : ''}`}>
      {/* The raid's headline numbers come before the tabs, not after: they
          describe the night itself, and the tabs choose which view of it you
          are reading. With a panel open neither one is here — the column is
          a picker, and a stat grid stacked down it shouts over the parse
          someone opened. */}
      {detail && headTiles[tab] && !panelOpen && (
        <div className="metrics">
          {headTiles[tab].map((t) => (
            <div className="metric" key={t.k} title={t.title}>
              <div className="v">{t.v}</div><div className="k">{t.k}</div>
            </div>
          ))}
        </div>
      )}
      {!panelOpen && (
        <Tabs tabs={TABS} value={tab} onChange={(k) => setTab(k === 'damage' ? null : k)} />
      )}
      {detailErr && <p className="err">{detailErr}</p>}
      {/* A selection with nothing in it — every fight of the raid hidden —
          is a parse with nothing to count, and it must not sit on "Loading…"
          forever pretending otherwise. Only the caller knows why there is
          nothing, so the caller says so. */}
      {notice}
      {!notice && !detail && !detailErr && tab !== 'insights' && tab !== 'aoes' && (
        <p className="muted">Loading…</p>
      )}
      {stale && detail && <div className="stalebar" aria-live="polite">Updating…</div>}

      {detail && tabCols[tab] && (
        <div className="card">
          <SortableTable
            /* the filters ride on the table's own tools line, beside Columns
               — they are all controls for the same table */
            tools={(
              <div className="filterbar">
                <input
                  type="text" value={q || ''} placeholder="Find a raider…"
                  onChange={(e) => setQ(e.target.value || null)}
                  aria-label="Filter combatants by name or class"
                />
                {/* one control, not four loose chips — they stay on a line
                    together however narrow the column gets */}
                <span className="roles">
                  {rolesPresent.map((r) => (
                    <button
                      key={r}
                      className={`chip role ${roleSet.has(r) ? 'on' : ''}`}
                      onClick={() => toggleRole(r)}
                      title={`Show only ${ROLE_LABEL[r].toLowerCase()}`}
                    >
                      {ROLE_LABEL[r]}
                    </button>
                  ))}
                </span>
                {/* Who is in the table, right beside who is filtered out of
                    it — the role chips narrow the raid, these two decide
                    whether anything but the raid is in it at all. On every
                    parse tab, not just Defense, and off by default: a mob is
                    a combatant with a parse worth reading (click its row),
                    but the table opens as the raid. */}
                <label
                  className="chip toggle"
                  title="Show pet rows. An owned pet's damage is credited to its owner, so its row usually carries only what it took."
                >
                  <input
                    type="checkbox"
                    checked={showPets}
                    onChange={(e) => setShowPets(e.target.checked)}
                  /> Pets
                </label>
                <label
                  className="chip toggle"
                  title="Show mob and environment rows. Click one to read its parse."
                >
                  <input
                    type="checkbox"
                    checked={showNpcs}
                    onChange={(e) => setShowNpcs(e.target.checked)}
                  /> NPCs
                </label>
                {(roleSet.size > 0 || q) && (
                  <button className="chip" onClick={() => { setRolesQ(null); setQ(null) }}>Reset</button>
                )}
              </div>
            )}
            columns={panelOpen
              ? [nameCol, leadCol[tab] || dpsCol]
              : (tabCols[tab] || damageCols)}
            /* layout is per tab, and the condensed picker beside an open
               drilldown is not a layout anyone wants remembered */
            prefsKey={panelOpen ? undefined : `zonerun:${tab}`}
            defaultHidden={TAB_HIDDEN[tab]}
            rows={currentRows}
            defaultSort={tabSort[tab] || tabSort.damage}
            rowKey={(a) => a.key}
            selectedKey={selectedActor}
            wrapClass={currentRows.length > 14 ? 'sticky' : ''}
            /* The name column and the header hold still: reading Crit % off
               row nineteen of a raid table means carrying a name across ten
               columns and a label down nineteen rows, and a table that
               scrolls both of them away is read from memory. */
            frozen
            /* Click a raider to READ them — the panel switches to that one
               parse, whatever was in it. Their box is what builds a
               comparison. Mobs and pets have no checkbox, so theirs stays a
               plain drilldown. */
            onRowClick={(a) => {
              if (a.kind === 'player') focusActor(a.key)
              else setActorQ(a.key === selectedActor ? null : a.key)
            }}
            checkable={(a) => a.kind === 'player'}
            checkedKeys={cmpKeys}
            onCheck={toggleCmp}
          />
          {!currentRows.length && (
            <p className="muted">
              {tab === 'deaths' && !q && !roleSet.size
                ? 'Nobody died.' : 'Nothing matches that filter.'}
            </p>
          )}
        </div>
      )}

      {detail && tab === 'aoes' && (
        <ErrorBoundary resetKey={`aoes:${idKey}`}>
          <AoePanel data={aoeData} err={aoeErr}
                    base={enc?.started_ts ?? span?.started_ts} />
        </ErrorBoundary>
      )}

      {detail && tab === 'loot' && (
        <ErrorBoundary resetKey={`loot:${idKey}`}>
          <LootPanel data={lootData} err={lootErr} />
        </ErrorBoundary>
      )}

      {detail && tab === 'class' && (
        <ErrorBoundary resetKey={`class:${idKey}:${clsQ}`}>
          <ClassPanel data={classData} err={classErr} cls={clsQ}
                      onPick={setClsQ} />
        </ErrorBoundary>
      )}

      {detail && tab === 'timeline' && (
        <div className="card">
          <div className="drillhead">
            <h2>Over the fight</h2>
            <span className="muted">
              {checkedActors.length
                ? `${checkedActors.length} checked`
                : `top 5 by ${{ heals: 'healing', taken: 'damage taken' }[metric] || 'damage'}`
                  + ' — check rows on another tab to choose'}
            </span>
          </div>
          {timelineErr && <p className="err">{timelineErr}</p>}
          {!timeline && !timelineErr && <p className="muted">Loading…</p>}
          {timeline?.pruned && (
            <p className="muted">
              No timeline — this run&apos;s raw events were pruned. The other tabs
              read from frozen rollups and are unaffected.
            </p>
          )}
          {timeline && !timeline.pruned && (
            <>
              <TimelineChart
                data={timeline}
                keys={timelineKeys}
                actorsByKey={actorsByKey}
                metric={metric}
                onMetric={setMetric}
              />
              {timeline.pruned_encounters > 0 && (
                <p className="note">
                  {timeline.pruned_encounters} of the selected fights had their events
                  pruned and are missing from the plot.
                </p>
              )}
            </>
          )}
        </div>
      )}

      {/* Two questions, two columns. "How did the tank die" is answered by
          one death in detail and "who died tonight" by all of them in a
          list, and the list is what was eating the page's whole width.
          Narrow when a drilldown is open — the main column is half a page
          then, and two of these inside it is four columns of nothing. */}
      {detail && tab === 'deaths' && recaps?.deaths?.length > 0 && (
        <ErrorBoundary resetKey={`deaths:${idKey}`}>
          <div className={`deathcols${
            hasTankDeath(recaps.deaths, actorsByKey) && !panelOpen ? ' two' : ''}`}>
            <TankDeaths
              key={`tanks:${idKey}`}
              deaths={recaps.deaths}
              windowS={recaps.window_s}
              actorsByKey={actorsByKey}
            />
            <DeathList
              /* what is expanded is indexed into THIS list of deaths, so a
                 new fight selection starts the list closed rather than
                 leaving a recap open on whatever death now sits at that
                 index */
              key={`deaths:${idKey}`}
              deaths={recaps.deaths}
              windowS={Math.min(RAID_WINDOW_S, recaps.window_s)}
              prunedEncounters={recaps.pruned_encounters}
              actorsByKey={actorsByKey}
            />
          </div>
        </ErrorBoundary>
      )}
      {detail && tab === 'deaths' && !recaps?.deaths?.length && deaths.length > 0 && (
        <div className="card">
          <h2>Deaths by fight</h2>
          <div className="tablewrap">
            <table className="data">
              <thead>
                <tr><th className="l">Fight</th><th>Time</th><th className="l">Player</th><th>Deaths</th><th>Time dead</th><th>Dmg lost</th></tr>
              </thead>
              <tbody>
                {deaths.map((d, i) => (
                  <tr key={i}>
                    <td className="name l">{d.encounter.name || 'trash'}</td>
                    <td>{fmt.time(d.encounter.started_ts)}</td>
                    <td className="l">{d.name}</td>
                    <td>{d.deaths}</td>
                    <td>{fmt.dur(d.time_dead_s)}</td>
                    <td>{fmt.num(d.death_dps_lost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Insights is the raid page's own tab — it reads the coach engine,
          which is per SESSION and not part of a parse. So it is handed in as
          a function of the parse it needs (the actor rows carry the class,
          and the ability decomposition is off `derived`), and it is hidden
          besides (see TABS). */}
      {tab === 'insights' && insights?.({ actors, derived })}

    </div>
  )

  /* `norail` is the parse rendered somewhere that already has a fight rail of
     its own (the dashboard): one column, with the drilldown under the table
     instead of beside it. */
  return (
    <div className={`workspace${rail ? '' : ' norail'}`
                    + `${panelOpen ? ' withpanel' : ''}`
                    + `${comparing && detail ? ' withcmp' : ''}`}>
      {rail}
      {docked ? createPortal(mainCol, pickerSlot) : mainCol}
      {/* One column for whatever is open. A comparison and a single drilldown
          are the same move — you picked people and want their parses beside
          the raid — so they get the same element in the same grid slot, and
          the fight rail and the condensed raider list merge into the left
          column either way. */}
      {panelOpen && detail && (
        <div className="panelcol">
          {selHead}
          {comparing ? (
            <ErrorBoundary resetKey={`cmp:${cmpQ}:${idKey}`}>
              <ComparePanel
                actors={actors}
                keys={cmpList}
                abilities={detail.abilities}
                derived={derived}
                duration={duration}
                kind={PANEL_KIND[tab]}
                onRemove={toggleCmp}
              />
            </ErrorBoundary>
          ) : (
            <ErrorBoundary resetKey={`actor:${selectedActor}:${idKey}`}>
              <ActorPanel
                key={selectedActor}
                name={selName}
                actor={actorsByKey[selectedActor]}
                abilities={detail.abilities}
                actorKey={selectedActor}
                duration={duration}
                kind={PANEL_KIND[tab]}
                onClose={closePanel}
                /* `cmpPrefix` is this selection as a Compare-page token,
                   which only the caller can spell: it needs the RUN the
                   fights belong to. Without one there is nothing to compare
                   against, so the link stays away. */
                compareTo={cmpPrefix && actorsByKey[selectedActor]?.kind === 'player'
                  ? `/compare?c=${cmpPrefix}:${selName}` : null}
              />
            </ErrorBoundary>
          )}
        </div>
      )}
    </div>
  )
}
