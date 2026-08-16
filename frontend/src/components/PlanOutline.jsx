import { useEffect, useMemo, useState } from 'react'
import QuestLinks from './QuestLinks.jsx'
import { Examine, Hover, rarityClass } from './ItemCard.jsx'

const DONE_KEY = 'eq2adv:plan:quest-done:v2'
const KIND_LABEL = { raid: 'Raid', group: 'Group', solo: 'Solo', unknown: 'Unknown' }

function doneKey(ownerKey) {
  return `${DONE_KEY}:${encodeURIComponent(ownerKey || 'unassigned')}`
}

function loadDone(ownerKey) {
  try { return new Set(JSON.parse(localStorage.getItem(doneKey(ownerKey))) || []) }
  catch { return new Set() }
}

function ItemHover({ card, children, block = false }) {
  if (!card) return children
  return (
    <Hover className="examinecard" width={350} block={block}
           card={<Examine row={card} />}>
      {children}
    </Hover>
  )
}

function Gets({ row, cards }) {
  if (!row.gets?.length) return row.why === 'prerequisite' ? <em>Prerequisite</em> : null
  return (
    <span className="outlinegets">
      {row.gets.map((item) => {
        const card = !item.via_set ? cards.get(item.page_title) : null
        const content = (
          <span className="outlineget" tabIndex={card ? 0 : undefined}>
            <OutlineItemIcon item={item} compact />
            <span className={!item.via_set ? rarityClass(item.tier) : ''}>
              {item.via_set || item.name}
            </span>
          </span>
        )
        return (
          <ItemHover card={card} key={`${item.page_title}-${item.via_set || ''}`}>
            {content}
          </ItemHover>
        )
      })}
    </span>
  )
}

function OutlineItemIcon({ item, compact = false }) {
  const src = item?.icon == null ? null : `/api/items/icon/${item.icon}.png`
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])
  return src && !failed
    ? <img className={`outlineitemicon${compact ? ' compact' : ''}`} src={src} alt="" width="34" height="34"
           onError={() => setFailed(true)} />
    : <span className={`outlineitemicon fallback${compact ? ' compact' : ''}`} aria-hidden="true">◆</span>
}

function ItemHeading({ item, context, detail, card }) {
  const content = (
    <div className="outlineitemheading" tabIndex={card ? 0 : undefined}>
      <OutlineItemIcon item={item} />
      <div>
        <span className="outlinetype item">
          Item{item?.slot && <><i aria-hidden="true"> · </i>{item.slot}</>}
        </span>
        <h3 className={rarityClass(item?.tier)}>{item?.name || context}</h3>
        <em>{[context, detail].filter(Boolean).join(' · ')}</em>
      </div>
    </div>
  )
  return <ItemHover card={card} block>{content}</ItemHover>
}

function QuestCopy({ row, number }) {
  const zone = row.zone && row.zone !== '*' ? row.zone : null
  const difficulty = KIND_LABEL[row.difficulty] || row.difficulty
  return (
    <span>
      <small className="questmeta">
        <span>Quest {number}{zone && <><i aria-hidden="true"> · </i>{zone}</>}</span>
        {difficulty && <b>{difficulty}</b>}
      </small>
      <strong>{row.name}</strong>
    </span>
  )
}

function waypointCommand(point) {
  return `/waypoint ${point.x}, ${point.y}, ${point.z}`
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text)
  const field = document.createElement('textarea')
  field.value = text
  field.style.position = 'fixed'
  field.style.opacity = '0'
  document.body.appendChild(field)
  field.select()
  const copied = document.execCommand('copy')
  field.remove()
  if (!copied) throw new Error('copy unavailable')
}

function WaypointCopy({ point, copied, onCopy }) {
  const command = waypointCommand(point)
  return (
    <button type="button" className={`waypointcopy${copied ? ' copied' : ''}`}
            title={`Copy ${command}`} aria-label={`Copy waypoint for ${point.label || 'quest step'}`}
            onClick={(event) => {
              event.preventDefault()
              event.stopPropagation()
              onCopy(command)
            }}>
      {copied ? 'Copied' : 'Copy waypoint'}
    </button>
  )
}

function Questline({ line, rows, done, toggle, cards }) {
  const quests = line.pages.map((key) => rows.get(key)).filter(Boolean)
  const next = quests.find((row) => !done.has(row.key)) || quests[quests.length - 1]
  const complete = quests.filter((row) => done.has(row.key)).length
  return (
    <details className="outlinezone outlinequestline" open={quests.length <= 8}>
      <summary className="questlinehead">
        <ItemHeading item={line.targets[0]} card={cards.get(line.targets[0]?.page_title)} context="Questline"
          detail={`${quests.length} quests · ${complete} complete`} />
      </summary>
      <div className="questlinesteps">
        {quests.map((row, index) => (
          <label className={`epicstep questlinestep${done.has(row.key) ? ' done' : ''}${next?.key === row.key ? ' next' : ''}`}
                 key={row.key}>
            <input type="checkbox" checked={done.has(row.key)}
                   onChange={() => toggle(row.key)} />
            <i>Q</i>
            <QuestCopy row={row} number={index + 1} />
            <QuestLinks page={row.key} />
          </label>
        ))}
      </div>
    </details>
  )
}

export default function PlanOutline({ data, ownerKey, items = [] }) {
  const [done, setDone] = useState(() => loadDone(ownerKey))
  const [copiedWaypoint, setCopiedWaypoint] = useState('')
  useEffect(() => {
    if (!ownerKey) return
    try { localStorage.setItem(doneKey(ownerKey), JSON.stringify([...done])) } catch { /* private mode */ }
  }, [done, ownerKey])
  const rowsByKey = useMemo(
    () => new Map(data.rows.map((row) => [row.key, row])), [data.rows])
  const cards = useMemo(
    () => new Map(items.filter((item) => item.card)
      .map((item) => [item.page_title, item.card])), [items])
  const questlinePages = useMemo(
    () => new Set((data.questlines || []).flatMap((line) => line.pages)), [data.questlines])
  const zones = useMemo(() => {
    const grouped = new Map()
    data.rows.filter((row) => !row.requirement && !row.epic
      && !questlinePages.has(row.key)).forEach((row) => {
      const zone = row.zone || 'Other'
      if (!grouped.has(zone)) grouped.set(zone, { name: zone, mobs: [], quests: [] })
      grouped.get(zone)[row.kind === 'quest' ? 'quests' : 'mobs'].push(row)
    })
    return [...grouped.values()]
  }, [data.rows, questlinePages])
  const requirements = data.rows.filter((row) => row.requirement && !row.epic)
  const epics = useMemo(() => {
    const grouped = new Map()
    data.rows.filter((row) => row.epic).forEach((row) => {
      const title = row.epic_title || `${row.timeline} Timeline`
      if (!grouped.has(title)) grouped.set(title, { title, requirements: [], quests: [] })
      grouped.get(title)[row.requirement ? 'requirements' : 'quests'].push(row)
    })
    return [...grouped.values()].map((epic) => ({
      ...epic,
      requirements: epic.requirements.sort((a, b) => a.epic_order - b.epic_order),
      quests: epic.quests.sort((a, b) => a.epic_order - b.epic_order),
    }))
  }, [data.rows])
  const toggle = (key) => setDone((held) => {
    const next = new Set(held)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })
  const copyWaypoint = (key, command) => {
    copyText(command).then(() => {
      setCopiedWaypoint(key)
      setTimeout(() => setCopiedWaypoint((held) => held === key ? '' : held), 1400)
    }).catch(() => {})
  }

  if (!data.rows.length) return <p className="muted outlineempty">Select an item to build the list.</p>
  return (
    <div className="planoutline zonelist">
      {epics.map((epic) => {
        const targets = [...new Map(epic.quests.flatMap((row) => row.gets || [])
          .map((item) => [item.page_title, item])).values()]
        const target = targets[0]
        const start = epic.quests.find((row) => !done.has(row.key))?.key
        const className = epic.title.replace(/ Epic Weapon Timeline$/i, '')
        return (
          <details className="outlinezone outlineepic" key={epic.title} open>
            <summary className="outlineepichead">
              <ItemHeading item={target} card={cards.get(target?.page_title)} context={`${className} epic weapon`}
                detail={target?.tier ? `${String(target.tier).toLowerCase()} questline` : 'Epic questline'} />
            </summary>
            {!!epic.requirements.length && (
              <details className="epicrequirements">
                <summary>
                  <span>Requirements</span>
                  <small>{epic.requirements.length}</small>
                </summary>
                <div className="epicrequirementlist">
                  {epic.requirements.map((row) => (
                    <label className={`epicrequirement${done.has(row.key) ? ' done' : ''}`}
                           key={row.key}>
                      <input type="checkbox" checked={done.has(row.key)}
                             onChange={() => toggle(row.key)} />
                      <span>{row.requirement_text}</span>
                      {row.kind === 'quest' && <QuestLinks page={row.key} />}
                    </label>
                  ))}
                </div>
              </details>
            )}
            <div className="epicsequence">
              {epic.quests.length > 1 && <b>Quest order</b>}
              {epic.quests.map((row, index) => (
                <label className={`epicstep${done.has(row.key) ? ' done' : ''}${start === row.key ? ' next' : ''}`}
                       key={row.key}>
                  <input type="checkbox" checked={done.has(row.key)}
                         onChange={() => toggle(row.key)} />
                  <i>Q</i>
                  <QuestCopy row={row} number={index + 1} />
                  {start === row.key && row.start_waypoint && (
                    <WaypointCopy point={row.start_waypoint}
                      copied={copiedWaypoint === row.key}
                      onCopy={(command) => copyWaypoint(row.key, command)} />
                  )}
                  <QuestLinks page={row.key} />
                </label>
              ))}
            </div>
          </details>
        )
      })}
      {(data.questlines || []).map((line) => (
        <Questline key={line.key} line={line} rows={rowsByKey}
          done={done} toggle={toggle} cards={cards} />
      ))}
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
                <div className="outlineentry mob zonesource" key={row.key}>
                  <span className="zonesourcehead">
                    <span className={`skind ${row.difficulty}`}>{KIND_LABEL[row.difficulty] || row.difficulty}</span>
                    <a href={row.wiki} target="_blank" rel="noreferrer noopener">{row.name}</a>
                  </span>
                  <Gets row={row} cards={cards} />
                </div>
              ))}
            </div>
          )}
          {!!zone.quests.length && (
            <div className="outlinekindlist">
              <b>Quests</b>
              {zone.quests.map((row) => (
                <label className={`outlineentry quest zonesource${done.has(row.key) ? ' done' : ''}`} key={row.key}>
                  <input type="checkbox" checked={done.has(row.key)} onChange={() => toggle(row.key)} />
                  <span>{row.name}</span>
                  <Gets row={row} cards={cards} />
                  <QuestLinks page={row.key} />
                </label>
              ))}
            </div>
          )}
        </section>
      ))}
      {!!data.unplaced.length && <p className="muted">{data.unplaced.length} selected item{data.unplaced.length === 1 ? '' : 's'} could not be placed.</p>}
      {!!data.ineligible?.length && <p className="muted">{data.ineligible.length} class-incompatible selection{data.ineligible.length === 1 ? ' was' : 's were'} excluded.</p>}
    </div>
  )
}
