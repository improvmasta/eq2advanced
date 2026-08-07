import { useEffect, useMemo, useState } from 'react'
import Picker from './Picker.jsx'
import { api, fmt } from '../lib/api.js'

/* Pick a fight to play back through the live meter.

   A developer control, shown only to a curator or an admin, because the meter
   is the one part of this site that cannot be worked on by looking at it: it
   only draws while a raid is happening. Replaying last Tuesday's Wuoshi gives
   the same feed, on demand, as often as it takes.

   The list is the nights you can already open — `?roster=1` is the payload the
   Compare picker faceted on, and it already carries every night's named mobs
   with their encounter ids, hidden pulls excluded. So this needs no endpoint of
   its own, and it cannot offer a fight you would not be allowed to replay (the
   server checks that again anyway).

   Named fights only. A replay is worth watching when it has a raid in it, and
   trash is where the meter has the least to say. */

const SPEEDS = [1, 2, 4]

export default function ReplayPicker({ active, onStart, onStop }) {
  const [runs, setRuns] = useState(null)
  const [speed, setSpeed] = useState(1)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let dead = false
    api.zoneRuns('all', { roster: true })
      .then((d) => {
        if (dead) return
        // one row per NIGHT, the raid list's rule: five raiders' parses of one
        // evening are one evening, and your own parse of it wins
        const byNight = new Map()
        for (const r of d.zone_runs || []) {
          const k = r.raid_key ?? r.id
          const cur = byNight.get(k)
          if (!cur || (r.mine && !cur.mine) || (!cur.mine && r.primary)) byNight.set(k, r)
        }
        setRuns([...byNight.values()].sort((a, b) => b.started_ts - a.started_ts))
      })
      .catch((e) => { if (!dead) setErr(e.message) })
    return () => { dead = true }
  }, [])

  /* One row per named mob per night, newest night first. A mob pulled three
     times is three rows rather than one — a wipe and the kill after it are
     different fights to watch, and the pull number is how you tell them
     apart. */
  const options = useMemo(() => {
    const out = []
    for (const run of runs || []) {
      const nameds = run.named || []
      if (!nameds.length) continue
      const group = `${run.zone || 'Unknown zone'} · ${fmt.date(run.started_ts)}`
      for (const n of nameds) {
        n.ids.forEach((id, i) => {
          out.push({
            value: String(id),
            label: n.name,
            menuLabel: n.ids.length > 1 ? `${n.name} (pull ${i + 1})` : n.name,
            hint: n.ids.length > 1 ? `pull ${i + 1} of ${n.ids.length}` : null,
            group,
            key: `r${run.id}-${id}`,
          })
        })
      }
    }
    return out
  }, [runs])

  if (err) return <span className="muted" title={err}>replay unavailable</span>

  if (active) {
    return (
      <span className="replaybar">
        <span className="badge warn">replay</span>
        <button className="chip" onClick={onStop}>Stop</button>
      </span>
    )
  }

  return (
    <span className="replaybar">
      <Picker
        className="replaypick"
        value=""
        options={options}
        label="Replay a fight"
        placeholder={runs === null ? 'Loading fights…'
          : options.length ? 'Replay a fight…' : 'No named fights yet'}
        disabled={!options.length}
        filterHint="Find a fight…"
        onChange={(v) => v && onStart(Number(v), speed)}
      />
      {SPEEDS.map((s) => (
        <button key={s} className={`chip ${s === speed ? 'on' : ''}`}
                onClick={() => setSpeed(s)} title={`Play at ${s}×`}>
          {s}×
        </button>
      ))}
    </span>
  )
}
