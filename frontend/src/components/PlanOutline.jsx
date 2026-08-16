import { useEffect, useMemo, useState } from 'react'
import QuestLinks from './QuestLinks.jsx'

const DONE_KEY = 'eq2adv:plan:quest-done'
const KIND_LABEL = { raid: 'Raid', group: 'Group', solo: 'Solo', unknown: 'Unknown' }

function loadDone() {
  try { return new Set(JSON.parse(localStorage.getItem(DONE_KEY)) || []) }
  catch { return new Set() }
}

function Gets({ row }) {
  if (!row.gets?.length) return row.why === 'prerequisite' ? <em>Prerequisite</em> : null
  return <em>{row.gets.map((item) => item.via_set || item.name).join(', ')}</em>
}

export default function PlanOutline({ data }) {
  const [done, setDone] = useState(loadDone)
  useEffect(() => {
    try { localStorage.setItem(DONE_KEY, JSON.stringify([...done])) } catch { /* private mode */ }
  }, [done])
  const zones = useMemo(() => {
    const grouped = new Map()
    data.rows.filter((row) => !row.requirement).forEach((row) => {
      const zone = row.zone || 'Other'
      if (!grouped.has(zone)) grouped.set(zone, { name: zone, mobs: [], quests: [] })
      grouped.get(zone)[row.kind === 'quest' ? 'quests' : 'mobs'].push(row)
    })
    return [...grouped.values()]
  }, [data.rows])
  const requirements = data.rows.filter((row) => row.requirement)
  const toggle = (key) => setDone((held) => {
    const next = new Set(held)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })

  if (!data.rows.length) return <p className="muted outlineempty">Select an item to build the list.</p>
  return (
    <div className="planoutline zonelist">
      {!!requirements.length && (
        <section className="outlinezone outlinerequirements">
          <h3>Epic prerequisites</h3>
          <div className="outlinekindlist">
            {requirements.map((row) => (
              <label className={`outlineentry quest${done.has(row.key) ? ' done' : ''}`} key={row.key}>
                <input type="checkbox" checked={done.has(row.key)} onChange={() => toggle(row.key)} />
                <span>{row.name}</span>
                {row.requirement_text !== row.name && <em>{row.requirement_text}</em>}
                {row.kind === 'quest' && <QuestLinks page={row.key} />}
              </label>
            ))}
          </div>
        </section>
      )}
      {zones.map((zone) => (
        <section className="outlinezone" key={zone.name}>
          <h3>{zone.name}</h3>
          {!!zone.mobs.length && (
            <div className="outlinekindlist">
              <b>Mobs</b>
              {zone.mobs.map((row) => (
                <div className="outlineentry mob" key={row.key}>
                  <span className={`skind ${row.difficulty}`}>{KIND_LABEL[row.difficulty] || row.difficulty}</span>
                  <a href={row.wiki} target="_blank" rel="noreferrer noopener">{row.name}</a>
                  <Gets row={row} />
                </div>
              ))}
            </div>
          )}
          {!!zone.quests.length && (
            <div className="outlinekindlist">
              <b>Quests</b>
              {zone.quests.map((row) => (
                <label className={`outlineentry quest${done.has(row.key) ? ' done' : ''}`} key={row.key}>
                  <input type="checkbox" checked={done.has(row.key)} onChange={() => toggle(row.key)} />
                  <span>{row.name}</span>
                  <Gets row={row} />
                  <QuestLinks page={row.key} />
                </label>
              ))}
            </div>
          )}
        </section>
      ))}
      {!!data.unplaced.length && <p className="muted">{data.unplaced.length} selected item{data.unplaced.length === 1 ? '' : 's'} could not be placed.</p>}
    </div>
  )
}
