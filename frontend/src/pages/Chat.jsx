/* The public chat box — EQ2's own chat window, rebuilt as three scrolling
   blocks instead of one window with tabs.

   Everything on this page came out of somebody's ACT plugin sending their log
   while they played (`pipeline/chatbus.py`), and since v36 it is KEPT: the page
   opens on the record rather than on this minute, and each block carries a date
   filter so you can go back and read a night you were not there for. That is
   what makes it a window into the game instead of a window into right now,
   which is also why nothing on the page counts who is uploading — the box is
   worth reading whether or not anybody is playing.

   Each block already names its channel, so a line keeps only the useful
   identity: `[time] Player: "message"`. Repeating `tells General (2)` on every
   row spent the narrow window on information its title had already supplied.

   All three blocks are the same blue on purpose (the request), so the colour
   is one variable in base.css (`--eq2-chat`) and not three. */

import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api.js'
import { lexiconGuild, plannerCharacter } from '../lib/raids.js'
import ChatAlerts from '../components/ChatAlerts.jsx'
import { Examine, Hover } from '../components/ItemCard.jsx'

const CHANNELS = [
  { key: 'general', label: 'General' },
  { key: 'lfg', label: 'LFG' },
  { key: 'auction', label: 'Auction' },
]

const EMPTY = { general: [], lfg: [], auction: [] }
const CAP = 400   // the live tail the page holds; the archive is behind the date
const RECRUIT_MAX_CHARS = 320 // about five lines in the narrow rail
const COLLAPSED_KEY = 'eq2advanced.chat.collapsed'
const SPAM_KEY = 'eq2advanced.chat.hide-powerleveling'

function clock(ts) {
  return clockParts(ts).join(' ')
}

function clockParts(ts) {
  const d = new Date(ts * 1000)
  const h = d.getHours()
  return [`${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')}`, h < 12 ? 'AM' : 'PM']
}

/* `<input type="date">` speaks YYYY-MM-DD in LOCAL time, and so does the day a
   reader means. Both directions go through the Date constructor's local-time
   form rather than through ISO strings, which are UTC and would hand somebody
   in Sydney the wrong evening. Day+1 is how the end bound is built so the two
   hours a year that are not 86400 seconds long still work. */
function isoDay(ts) {
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function dayWindow(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return [Math.floor(new Date(y, m - 1, d).getTime() / 1000),
          Math.floor(new Date(y, m - 1, d + 1).getTime() / 1000)]
}

/* Item cards, asked for once each and kept for the life of the page.

   The Loot tab is handed every card with its rows, because it knows the whole
   table at once. A chat line does not: an item can be linked at any moment and
   arrive over the stream long after the page loaded. So the link fetches its
   own card the first time somebody hovers it — which is also the first moment
   anybody wants it — and the promise is cached, so ten links to the same item
   in an Auction argument are one request.

   `null` is a real, cached answer: an id nobody has looked up yet stays a link
   with no card rather than asking again on every hover. */
const CARDS = new Map()

function itemCard(id) {
  if (!CARDS.has(id)) {
    CARDS.set(id, api.itemCard(id).then((d) => d.card).catch(() => null))
  }
  return CARDS.get(id)
}

function ItemLink({ part }) {
  const [card, setCard] = useState(undefined)
  const label = <span className="lnk item">[{part.s}]</span>

  const load = () => { itemCard(part.item).then((c) => setCard(c ?? null)) }
  // An item somebody has already hovered elsewhere on the page is known
  // without asking, so its card opens on the first frame rather than after a
  // beat of "…" — the request is the thing being avoided, not the read.
  useEffect(() => { if (CARDS.has(part.item)) load() }, [part.item])

  /* The fetch hangs off the card OPENING, not off a mouse event: a link can
     arrive under a cursor that is already sitting there (the box scrolls
     itself), and the card that opens for it still has to fill in. */
  return (
    <Hover className="examinecard" width={350} onOpen={load}
           card={card ? <Examine row={card} />
             : <div className="examineloading">
                 {card === null ? 'Nobody has looked this one up yet.' : '…'}
               </div>}>
      {card?.wiki
        ? <a href={card.wiki} target="_blank" rel="noreferrer noopener">{label}</a>
        : label}
    </Hover>
  )
}

/* One message. `parts` arrives already split (`pipeline/chatbus.py`) into text,
   URLs and link labels, so the page never parses a chat line — it only decides
   how each piece is drawn.

   A URL is somebody's typed text, not the site's endorsement: it opens in a new
   tab, carries `noopener noreferrer nofollow`, and shows the address AS TYPED.
   Nothing shortens or prettifies it, because the one thing a reader needs from
   a link in a public channel is to see where it actually goes. */
function Piece({ p, trade }) {
  if (p.k === 't') {
    const hit = trade && p.s.match(/^(\s*)(WTS|WTB)\b/i)
    if (hit) return <span>{hit[1]}<b className={`tradeword ${trade}`}>{hit[2]}</b>{p.s.slice(hit[0].length)}</span>
    return <span>{p.s}</span>
  }
  if (p.k === 'guild') {
    return (
      <a className="lnk guild" href={lexiconGuild(p.s)} target="_blank"
         rel="noreferrer noopener" title={`View ${p.s} on EQ2 Lexicon`}>
        {p.s}
      </a>
    )
  }
  if (p.k === 'url') {
    return (
      <a className="lnk url" href={p.s.startsWith('www.') ? `https://${p.s}` : p.s}
         target="_blank" rel="noreferrer noopener nofollow">{p.s}</a>
    )
  }
  if (p.k === 'item' && p.item != null) return <ItemLink part={p} />
  return <span className={`lnk ${p.k}`}>[{p.s}]</span>
}

function PlayerLink({ name, className }) {
  return (
    <Link className={className} to={plannerCharacter(name)}
          title={`Plan gear for ${name}`}>
      {name}
    </Link>
  )
}

function tradeKind(m) {
  /* An EQ2 link is its own part. With no typed space, joining labels turns
     `WTS` + `Cloak…` into `WTSCloak…` and loses the token boundary even though
     the rendered item starts with `[`. Classify the leading text run itself so
     a link boundary counts, while ordinary words such as `WTSomething` do not. */
  const first = m.parts.find((p) => p.s.trim())
  if (first?.k !== 't') return ''
  const text = first.s.trimStart()
  return /^WTS\b/i.test(text) ? 'wts' : /^WTB\b/i.test(text) ? 'wtb' : ''
}

function Line({ m, channel }) {
  const trade = channel.key === 'auction' ? tradeKind(m) : ''
  const firstText = m.parts.findIndex((p) => p.k === 't')
  const [at, period] = clockParts(m.ts)
  return (
    <div className="eq2line">
      <time className="ts" dateTime={new Date(m.ts * 1000).toISOString()}>
        <span>{at}</span><small>{period}</small>
      </time>
      <span className="chatcopy">
        <PlayerLink className="who" name={m.who} /><span className="punct">:</span>{' '}
        <span className="speech">&quot;{m.parts.map((p, i) => (
          <Piece key={i} p={p} trade={i === firstText ? trade : ''} />
        ))}&quot;</span>
      </span>
    </div>
  )
}

const said = (m) => `${m.who} ${m.parts.map((p) => p.s).join('')}`.toLowerCase()

/* Powerlevel ads have several stable spellings in the live archive: the full
   word, `1-70 PL` (with or without spaces), and "power 1-70 lvl exp". Keep the
   narrow shapes here rather than filtering every mention of "level" or "exp",
   which are ordinary LFG conversation. A player asking for a PL group is the
   same traffic this switch is meant to remove. */
function isPowerlevelSpam(m) {
  const text = m.parts.map((p) => p.s).join('').toLowerCase()
  return /\bpower\s*-?\s*levels?|\bpowerlevel/.test(text)
    || /\b\d{1,3}\s*[-–]\s*\d{1,3}\s*pl\b/.test(text)
    || /\bpower\s+\d{1,3}\s*[-–]\s*\d{1,3}\s+(?:lvl|level)\s+exp\b/.test(text)
    || /\bpl\s+groups?\b/.test(text)
}

/* ---- Stats ---------------------------------------------------------------

   What the channel LOOKED like, under the channel it describes. Every figure
   comes from `chatbus.stats`, which has four columns to work with and invents
   nothing from them.

   THE PANEL FOLLOWS THE BOX. A box pinned to a day counts that day; a live box
   counts the whole archive, because the live state is a few hundred lines of
   tail and "who talked most in the last 400 messages" is not a question anyone
   has. The heading says which, every time, so a leaderboard is never ambiguous
   about what it is a leaderboard OF.

   Nothing here redraws on a new message. The panel is a reading of a window,
   taken when you opened it — a leaderboard that reshuffles under you while you
   read it is worse than one that is a minute old, and closing and opening it is
   the refresh. */

const AMPM = (h) => `${h % 12 || 12}${h < 12 ? 'a' : 'p'}`
const BIN_LABEL = (i) => `${AMPM(i * 2)}–${AMPM((i * 2 + 2) % 24)}`

/* A day in the summary is read, not sorted, so it is written the short way —
   through the local-time constructor like every other date on this page, never
   by slicing the ISO string, which is UTC. */
function shortDay(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString(undefined,
                                                  { month: 'short', day: 'numeric' })
}

/* The server bins NOTHING: `hours` arrives as `[unix hour, count]` because
   both shapes below are questions about where the reader is sitting, which is
   the same rule the date filter follows. Local time is applied here, which is
   also how the two days a year that are not 24 hours long come out right. */
function twoHourBins(hours) {
  const bins = Array.from({ length: 12 }, () => 0)
  for (const [h, n] of hours) bins[Math.floor(new Date(h * 1000).getHours() / 2)] += n
  return bins
}

function dailyCounts(hours) {
  const by = new Map()
  for (const [h, n] of hours) {
    const k = isoDay(h)
    by.set(k, (by.get(k) ?? 0) + n)
  }
  const keys = [...by.keys()].sort()
  if (!keys.length) return []
  /* Every day from the first to the last, the silent ones included — which is
     what makes "talked on 3 of 14 days" mean anything. Counting only the days
     that HAVE chat would make that read 3 of 3 on every archive there has ever
     been. */
  const [y, m, d] = keys[0].split('-').map(Number)
  const last = keys[keys.length - 1]
  const out = []
  for (let i = 0; i < 4000; i++) {
    const iso = isoDay(new Date(y, m - 1, d + i).getTime() / 1000)
    out.push({ d: iso, n: by.get(iso) ?? 0 })
    if (iso >= last) break
  }
  return out
}

/* A leaderboard is a NUMBERED LIST, not a chart: no bars, no columns, no
   sparkline anywhere in this panel (the request). Everything is the figure
   itself, which in a column this narrow is also the thing that reads fastest —
   a bar would have to share the width with the name it belongs to. */
function Board({ rows, value, playerNames = false }) {
  return (
    <ol className="board">
      {rows.map((r, i) => (
        <li key={r.who}>
          <span className="rk">{i + 1}</span>
          {playerNames
            ? <PlayerLink className="nm" name={r.who} />
            : <span className="nm" title={r.who}>{r.who}</span>}
          <span className="v">{value ? value(r) : r.n}</span>
        </li>
      ))}
    </ol>
  )
}

/* The same list without the rank column: label on the left, figure on the
   right. The summary at the top of the panel used to be a sentence and read
   like one thing among the boards rather than the first of them — four figures
   in the shape the rest of the panel already uses is the same information
   without the prose. Unordered, because these are not places. */
function Facts({ rows }) {
  return (
    <ul className="board facts">
      {rows.map((r) => (
        <li key={r.k}>
          <span className="nm">{r.k}</span>
          <span className="v">{r.v}</span>
        </li>
      ))}
    </ul>
  )
}

/* Size is the encoding and the ONLY one — one colour, because a cloud that is
   also a colour ramp is saying the same thing twice. Square-rooted so the top
   word does not swallow the tail: counts in a chat channel are steep. */
function Cloud({ words }) {
  const max = words[0]?.n ?? 1
  return (
    <div className="cloud">
      {words.map((w) => (
        <span key={w.w} title={`${w.w} — ${w.n}`}
              style={{ fontSize: `${(0.72 + 0.8 * Math.sqrt(w.n / max)).toFixed(2)}rem` }}>
          {w.w}
        </span>
      ))}
    </div>
  )
}

function Section({ title, sub, children }) {
  return (
    <section className="sec">
      <h3>{title}{sub && <span className="sub"> {sub}</span>}</h3>
      {children}
    </section>
  )
}

function StatsPanel({ channel, start, end, when }) {
  const [d, setD] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let dead = false
    setD(null)
    setFailed(false)
    api.chatStats(channel.key, start, end)
      .then((r) => { if (!dead) setD(r) })
      .catch(() => { if (!dead) setFailed(true) })
    return () => { dead = true }
  }, [channel.key, start, end])

  if (failed) return <div className="chatstats"><p className="note">could not count that</p></div>
  if (!d) return <div className="chatstats"><p className="note">counting…</p></div>
  if (!d.total) {
    return (
      <div className="chatstats">
        <p className="note">nothing said {start == null ? 'yet' : when}</p>
      </div>
    )
  }

  // The clock shapes, as sentences rather than as pictures. Three windows is
  // enough to say when the channel is awake; twelve rows of mostly-nothing is
  // the chart this panel deliberately does not draw.
  const bins = twoHourBins(d.hours)
  const peaks = bins
    .map((n, i) => ({ who: BIN_LABEL(i), n }))
    .filter((b) => b.n > 0)
    .sort((a, b) => b.n - a.n)
    .slice(0, 3)
  const days = start == null ? dailyCounts(d.hours) : []
  const busiest = days.length ? days.reduce((a, b) => (b.n > a.n ? b : a)) : null
  const talkedOn = days.filter((x) => x.n > 0).length

  /* An all-time box can say how today compares to a normal day; a box pinned to
     one day cannot, and must not pretend to — "today" and "average" are both
     questions about the archive, so the pinned reading answers the two things a
     single day actually knows instead. The average counts every day in the span
     including the silent ones, for the same reason `dailyCounts` fills them in:
     dividing by the days that HAVE chat would report the busy-night rate as the
     ordinary one. */
  const today = days.length ? (days.find((x) => x.d === isoDay(Date.now() / 1000))?.n ?? 0) : 0
  const facts = start == null
    ? [
        { k: 'Chat lines today', v: today },
        { k: 'Chat lines (avg)', v: Math.round(d.total / Math.max(days.length, 1)) },
        ...(busiest && days.length > 1
          ? [{ k: 'Busiest day', v: `${shortDay(busiest.d)} · ${busiest.n}` }] : []),
        { k: 'One-time chatters', v: d.once },
      ]
    : [
        { k: 'Chat lines', v: d.total },
        { k: d.speakers === 1 ? 'Voice' : 'Voices', v: d.speakers },
        { k: 'One-time chatters', v: d.once },
      ]

  return (
    <div className="chatstats">
      <Section title="At a glance" sub={`(${when})`}>
        <Facts rows={facts} />
      </Section>

      <Section title="Most talkative">
        <Board rows={d.talkers} playerNames />
      </Section>

      {d.spammers.length > 0 && (
        <Section title="Biggest spammers">
          <Board rows={d.spammers.map((s) => ({ who: s.who, n: s.repeats, all: s.n }))}
                 value={(r) => `${r.n} of ${r.all}`} playerNames />
          <p className="note">repeats — messages this person had already sent</p>
        </Section>
      )}

      {d.fame.length > 0 && (
        <Section title="Fame" sub="(named by others)">
          <Board rows={d.fame} playerNames />
        </Section>
      )}

      <Section title="Peak chat windows">
        <Board rows={peaks} />
      </Section>

      {/* The busiest day moved up into the summary, so what is left here is the
          one thing that needs both numbers to mean anything. */}
      {talkedOn > 1 && (
        <Section title="Across the days">
          <p className="fact">
            Talked on <b>{talkedOn}</b> of <b>{days.length}</b> days
          </p>
        </Section>
      )}

      {/* A cloud does not need to be told what it is — the words say it. A rule
          is all the separation it wants from the list above it. */}
      {d.words.length > 0 && (
        <>
          <hr className="sep" />
          <Cloud words={d.words} />
        </>
      )}

      {/* The one thing a leaderboard implies and this archive does not have.
          A quiet hour here can be a quiet server or it can be nobody
          uploading, and the numbers cannot tell those apart. */}
      <p className="caveat">
        Counted from relayed logs — a quiet stretch can mean nobody was
        uploading rather than nobody was talking.
      </p>
    </div>
  )
}

/* Follows the bottom the way a chat window does, and stops following the
   moment you scroll up to read something — otherwise the next line yanks the
   text out from under whoever is mid-sentence. Scrolling back to the bottom
   re-arms it.

   The date and the filter are per BOX and live here rather than on the page:
   three boxes are three different questions ("who was selling plate on Friday",
   "any healer LFG right now"), and one shared control would make them one.

   A date pins the box to that day out of the record; clearing it drops back
   onto the live tail, which kept arriving underneath the whole time. The filter
   narrows whichever of the two is showing. */
function Block({
  channel, messages, bounds, day, setDay, dark, collapsed, onCollapse, hideSpam,
}) {
  const body = useRef(null)
  const stick = useRef(true)
  const [q, setQ] = useState('')
  const [past, setPast] = useState(null)     // null until the day's answer lands
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!day) { setPast(null); setFailed(false); return }
    let dead = false
    setPast(null)
    setFailed(false)
    const [start, end] = dayWindow(day)
    api.chatHistory(channel.key, start, end)
      .then((d) => { if (!dead) { stick.current = true; setPast(d.messages) } })
      .catch(() => { if (!dead) { setFailed(true); setPast([]) } })
    return () => { dead = true }
  }, [day, channel.key])

  const live = !day
  const source = (live ? messages : (past ?? []))
    .filter((m) => !hideSpam || !isPowerlevelSpam(m))
  const needle = q.trim().toLowerCase()
  const shown = needle ? source.filter((m) => said(m).includes(needle)) : source

  useEffect(() => {
    const el = body.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [shown])

  const onScroll = () => {
    const el = body.current
    if (!el) return
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
  }

  const empty = failed ? 'could not read that day'
    : !live && past === null ? 'reading…'
      : needle ? 'no matches'
        : live ? 'nothing here yet' : 'nothing said on this day'

  return (
    <section className={`eq2win ${live ? '' : 'past'}${collapsed ? ' collapsed' : ''}`}>
      <div className="eq2tabs">
        <h2 className="eq2tab">{channel.label}</h2>
        <button type="button" className="eq2collapse" onClick={onCollapse}
                aria-expanded={!collapsed}
                aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${channel.label} chat`}
                title={`${collapsed ? 'Expand' : 'Collapse'} ${channel.label}`}>
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="m3 10 5-5 5 5" />
          </svg>
        </button>
        {!collapsed && <label className="eq2day">
          <input
            type="date"
            value={day}
            min={bounds.min}
            max={bounds.max}
            aria-label={`${channel.label} on a day`}
            onChange={(e) => { stick.current = true; setDay(e.target.value) }}
          />
          {day && (
            <button type="button" className="eq2now" onClick={() => setDay('')}
                    title="Back to live">live</button>
          )}
        </label>}
        {!collapsed && <label className="eq2find">
          <input
            type="search"
            value={q}
            placeholder="filter"
            aria-label={`Filter ${channel.label}`}
            onChange={(e) => {
              // a filter narrows to the newest matches, so it lands you at the
              // bottom of them rather than wherever the unfiltered scroll was
              stick.current = true
              setQ(e.target.value)
            }}
          />
          {needle && <span className="hits">{shown.length}</span>}
        </label>}
      </div>
      {!collapsed && <div className="eq2body" ref={body} onScroll={onScroll}>
        {shown.length === 0
          ? <div className="eq2off">{empty}</div>
          : shown.map((m) => <Line key={m.id} m={m} channel={channel} />)}
        {/* Nothing is arriving, and the last line says so where the next line
            would have been — the box has an archive in it, so this is not an
            empty state and never replaces what was already said. A pinned day
            is not a feed and cannot disconnect from anything. */}
        {live && dark && <div className="eq2gone">disconnected</div>}
      </div>}
    </section>
  )
}

/* One channel: the game's window, and UNDER it — outside the frame, not part of
   it — what that channel looks like counted up.

   Outside the frame is also outside the replica, so the panel wears the SITE's
   theme rather than EQ2's palette. The box is a screenshot of the game and the
   panel is this site talking about it; making the panel dark blue too would
   claim the game drew it, and EQ2 has no such window.

   The panel is mounted only while it is open, so a channel nobody expands never
   asks the server to count anything. */
function Channel({
  channel, messages, bounds, dark, collapsed, onCollapse, hideSpam,
}) {
  const [day, setDay] = useState('')
  const [open, setOpen] = useState(false)
  const live = !day

  return (
    <div className={`eq2col${collapsed ? ' collapsed' : ''}`}>
      <Block channel={channel} messages={messages} bounds={bounds}
             day={day} setDay={setDay} dark={dark}
             collapsed={collapsed} onCollapse={onCollapse} hideSpam={hideSpam} />
      {!collapsed && <button type="button" className="statsbtn" aria-expanded={open}
              onClick={() => setOpen((s) => !s)}>
        <i className={`caret${open ? ' up' : ''}`} />
        Stats
        <span className="of">{channel.label}</span>
      </button>}
      {!collapsed && open && (
        <StatsPanel channel={channel} when={day ? `on ${day}` : 'all time'}
                    start={live ? null : dayWindow(day)[0]}
                    end={live ? null : dayWindow(day)[1]} />
      )}
    </div>
  )
}

/* The latest pitch from each guild, not every hourly repeat of its macro.
   Multi-line adverts stay together because the server groups the lines EQ2
   stamped in the same second. This is outside the replica chat windows: it is
   a directory made FROM the chat, not a fourth in-game channel. */
function clippedPitch(messages, max) {
  let left = max
  const out = []
  for (const message of messages) {
    const parts = []
    for (const part of message.parts) {
      if (left <= 0) break
      // Never manufacture a broken guild, item or URL link just because the
      // fold landed inside its label. Plain prose is the only safe part to cut.
      if (part.k !== 't' && part.s.length > left) { left = 0; break }
      const s = part.s.slice(0, left)
      parts.push({ ...part, s })
      left -= s.length
      if (s.length < part.s.length) break
    }
    if (parts.length) out.push({ ...message, parts })
    if (left <= 0) break
    // A line break is visible space too, even though it is not in either line.
    left -= 1
  }
  return out
}

function RecruitCard({ pitch }) {
  const [open, setOpen] = useState(false)
  const length = pitch.messages.reduce(
    (n, m) => n + m.parts.reduce((sum, p) => sum + p.s.length, 0), 0)
    + Math.max(0, pitch.messages.length - 1)
  const long = length > RECRUIT_MAX_CHARS
  const messages = long && !open
    ? clippedPitch(pitch.messages, RECRUIT_MAX_CHARS)
    : pitch.messages

  return (
    <article className="recruitcard">
      <div className="recruittitle">
        <a href={lexiconGuild(pitch.guild)} target="_blank" rel="noreferrer noopener">
          {pitch.guild}
        </a>
        <time dateTime={new Date(pitch.ts * 1000).toISOString()}
              title={new Date(pitch.ts * 1000).toLocaleString()}>
          {isoDay(pitch.ts) === isoDay(Date.now() / 1000)
            ? clock(pitch.ts) : shortDay(isoDay(pitch.ts))}
        </time>
      </div>
      <div className="recruitcopy">
        {messages.map((m) => (
          <p key={m.id}>{m.parts.map((p, i) => <Piece key={i} p={p} />)}</p>
        ))}
        {long && (
          <button type="button" className="recruitmore" aria-expanded={open}
                  onClick={() => setOpen((value) => !value)}>
            {open ? 'less' : '… more'}
          </button>
        )}
      </div>
      <div className="recruitby">
        posted by <PlayerLink name={pitch.who} />
      </div>
    </article>
  )
}

function Recruiting({ guilds }) {
  return (
    <aside className="recruitrail">
      <div className="recruithead">
        <h2>Recruiting</h2>
        <span>last 3 days</span>
      </div>
      {guilds === null && <p className="muted">Reading General…</p>}
      {guilds?.length === 0 && <p className="muted">No guild adverts found yet.</p>}
      {guilds?.map((g) => <RecruitCard key={g.guild.toLowerCase()} pitch={g} />)}
      <p className="recruitnote">
        Collected from guild recruitment messages in General. Guild and player
        links open EQ2 Lexicon.
      </p>
    </aside>
  )
}

export default function Chat({ user }) {
  const [rooms, setRooms] = useState(EMPTY)
  const [recruiting, setRecruiting] = useState(null)
  // the span the record covers, so the date pickers cannot wander off it
  const [bounds, setBounds] = useState({ min: undefined, max: undefined })
  /* Is chat arriving. TWO things have to be true and either one failing is the
     same fact to a reader: this page has to be connected to the server, and
     somebody in the game has to be relaying. `null` is neither yet — the light
     stays off rather than guessing red for the first second of the page. */
  const [link, setLink] = useState(null)
  const [feeders, setFeeders] = useState(0)
  const flowing = link === null ? null : link && feeders > 0
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || '[]')
        .filter((k) => CHANNELS.some((c) => c.key === k)))
    } catch { return new Set() }
  })
  const [hideSpam, setHideSpam] = useState(
    () => localStorage.getItem(SPAM_KEY) === '1')
  const [alertsOpen, setAlertsOpen] = useState(false)

  useEffect(() => {
    try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed])) } catch { /* private mode */ }
  }, [collapsed])
  useEffect(() => {
    try { localStorage.setItem(SPAM_KEY, hideSpam ? '1' : '0') } catch { /* private mode */ }
  }, [hideSpam])

  const toggleCollapsed = (key) => setCollapsed((before) => {
    const next = new Set(before)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })

  useEffect(() => {
    let dead = false
    let es = null

    api.chatRecent().then((d) => {
      if (dead) return
      setRooms({ ...EMPTY, ...d.channels })
      setFeeders(d.connected)
      if (d.first_ts) {
        // `max` is today rather than the newest line: the box is live, so a
        // line landing at 9pm must not be unreachable because the archive
        // stopped at lunchtime when the page loaded
        setBounds({ min: isoDay(d.first_ts), max: isoDay(Date.now() / 1000) })
      }

      es = new EventSource(`/api/chat/stream?since=${d.seq}`)
      es.addEventListener('chat', (ev) => {
        const fresh = JSON.parse(ev.data)
        if (fresh.some((m) => m.guild)) {
          api.chatRecruiting().then((d) => { if (!dead) setRecruiting(d.guilds) }).catch(() => {})
        }
        setRooms((prev) => {
          const next = { ...prev }
          for (const m of fresh) {
            const room = next[m.ch]
            if (!room) continue
            /* An EventSource reconnects itself, and it reconnects to the
               `since` this page opened with — so a dropped connection replays
               everything said since then. Ids only ever go up, so the last one
               in the room is the whole test. */
            if (room.length && m.id <= room[room.length - 1].id) continue
            next[m.ch] = [...room, m].slice(-CAP)
          }
          return next
        })
      })
      /* The keepalive carries the one number the light needs, and it arrives
         both on a change and every 20s regardless, so an open connection is
         itself the proof that this end is up. */
      es.addEventListener('status', (ev) => {
        setLink(true)
        setFeeders(JSON.parse(ev.data).connected)
      })
      /* EventSource reconnects itself. A gap costs this page the messages sent
         during it and nothing more — they are in the table, so the date filter
         still has them. What it DOES cost is the truth of the box: nothing is
         arriving and the page cannot tell whether anything is being said, so
         the light goes out until a status event says otherwise. */
      es.onerror = () => setLink(false)
    }).catch(() => { if (!dead) setLink(false) })

    return () => { dead = true; es?.close() }
  }, [])

  useEffect(() => {
    let dead = false
    api.chatRecruiting()
      .then((d) => { if (!dead) setRecruiting(d.guilds) })
      .catch(() => { if (!dead) setRecruiting([]) })
    return () => { dead = true }
  }, [])

  return (
    <div className="chatpage">
      <div className="pagehead">
        <div>
          {/* The light belongs on the word, not on a pill beside it: there is
              one feed on this page and "Chat" is its name. Green is arriving,
              red is not — and red is a normal state at 4am, not a fault, which
              is why the boxes keep their archive under it. */}
          <div className="chatheading">
            <h1 className="chattitle">
              Chat
              {flowing !== null && (
                <i className={`chatdot${flowing ? ' on' : ''}`}
                   title={flowing ? 'Chat is arriving now'
                     : 'Nothing is arriving — nobody is relaying, or this page lost the server'} />
              )}
            </h1>
            <label className="chatspam">
              <input type="checkbox" checked={hideSpam}
                     onChange={(e) => setHideSpam(e.target.checked)} />
              Spam filter
            </label>
            <button className={`chatalertbtn${alertsOpen ? ' active' : ''}`}
                    aria-expanded={alertsOpen} onClick={() => setAlertsOpen((v) => !v)}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M9.5 21h5" />
              </svg>
              Alerts
            </button>
          </div>
          {/* Which server, said plainly. Every line here was broadcast on
              Wuoshi, and a reader who does not already know that has no way to
              tell from a page of chat — the channels look the same on every
              server in the game. */}
          <div className="sub">
            Crowdsourced public chat from logged in EQ2Advanced users on the
            Wuoshi server.
          </div>
        </div>
      </div>

      {alertsOpen && <ChatAlerts user={user} onClose={() => setAlertsOpen(false)} />}

      <div className="chatlayout">
        <div className="eq2grid" style={{
          gridTemplateColumns: CHANNELS.map((c) => collapsed.has(c.key)
            ? '104px' : 'minmax(0, 1fr)').join(' '),
        }}>
          {CHANNELS.map((c) => (
            <Channel key={c.key} channel={c} messages={rooms[c.key] ?? []}
                     bounds={bounds} dark={flowing === false}
                     collapsed={collapsed.has(c.key)}
                     onCollapse={() => toggleCollapsed(c.key)} hideSpam={hideSpam} />
          ))}
        </div>
        <Recruiting guilds={recruiting} />
      </div>
    </div>
  )
}
