import { useEffect, useMemo, useRef, useState } from 'react'
import QuestLinks from './QuestLinks.jsx'
import { Examine, Hover, rarityClass } from './ItemCard.jsx'

const DONE_KEY = 'eq2adv:plan:quest-done:v2'
const KIND_LABEL = {
  raid: 'Raid', group: 'Group', solo: 'Solo', zone: 'World drop', unknown: 'Unknown',
}

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
        const card = item.card || cards.get(item.page_title)
        const content = (
          <span className="outlineget" tabIndex={card ? 0 : undefined}>
            <OutlineItemIcon item={item} compact />
            <span className={rarityClass(item.tier)}>
              {item.name}
              {item.via_set && <small>carries {item.via_set}</small>}
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

function Questline({ line, rows, done, toggle }) {
  const quests = line.pages.map((key) => rows.get(key)).filter(Boolean)
  const next = quests.find((row) => !done.has(row.key)) || quests[quests.length - 1]
  const complete = quests.filter((row) => done.has(row.key)).length
  return (
    <details className="outlinezone outlinequestline">
      <summary className="questlinehead">
        <div className="outlineitemheading outlinequestheading">
          <span className="outlinequesticon" aria-hidden="true">Q</span>
          <div>
            <span className="outlinetype">Questline</span>
            <h3>{quests[quests.length - 1]?.name || 'Acquisition questline'}</h3>
            <em>{quests.length} quests · {complete} complete</em>
          </div>
        </div>
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

function AcquisitionSource({ source, variants, done, toggle, sequenced }) {
  const levels = [...new Set(source.item_pages.map(
    (page) => variants.get(page)?.level).filter(Boolean))]
  const zoneOnly = source.difficulty === 'zone' && source.name === source.zone
  const content = (
    <>
      {source.kind === 'quest' && !sequenced && (
        <input type="checkbox" checked={done.has(source.key)}
               onChange={() => toggle(source.key)} />
      )}
      {source.kind === 'quest' && sequenced && <i className="acquisitionsourcequest">Q</i>}
      <span>
        {!zoneOnly && (
          <small><b className={`skind ${source.difficulty}`}>{KIND_LABEL[source.difficulty]
            || source.difficulty}</b></small>
        )}
        <a href={source.wiki} target="_blank" rel="noreferrer noopener">
          {zoneOnly ? 'Zone drop' : source.name}
        </a>
      </span>
      {!!levels.length && <em>{levels.map((level) => `L${level}`).join(' / ')}</em>}
      {source.kind === 'quest' && <QuestLinks page={source.key} />}
    </>
  )
  return source.kind === 'quest' && !sequenced ? (
    <label className={`acquisitionsource quest${done.has(source.key) ? ' done' : ''}`}>
      {content}
    </label>
  ) : <div className={`acquisitionsource${source.kind === 'quest' ? ' quest' : ''}`}>
    {content}
  </div>
}

function Acquisition({ acquisition, cards, done, toggle, questlinePages,
                       open, onOpen }) {
  const item = acquisition.item
  const variants = new Map((acquisition.variants || [])
    .map((variant) => [variant.page_title, variant]))
  const sourceCount = acquisition.sources.length
  const setPiece = acquisition.via_set_piece?.split(':').pop()?.trim()
  const sourceZones = [...acquisition.sources.reduce((grouped, source) => {
    const zone = source.zone || 'Other'
    if (!grouped.has(zone)) grouped.set(zone, [])
    grouped.get(zone).push(source)
    return grouped
  }, new Map())]
  return (
    <details className="outlinezone outlineacquisition" open={open}
             onToggle={(event) => onOpen(event.currentTarget.open)}>
      <summary className="outlineacquisitionhead">
        <ItemHeading item={item} card={item.card || cards.get(item.page_title)}
          context={`${sourceCount} source${sourceCount === 1 ? '' : 's'}`}
          detail={acquisition.via_set
            ? `carries ${acquisition.via_set}${setPiece ? ` · ${setPiece}` : ''}` : null} />
      </summary>
      <div className="acquisitionsources">
        <b>Where to get it</b>
        {sourceZones.map(([zone, sources]) => (
          <section className="acquisitionzone" key={zone}>
            <h4>{zone}</h4>
            {sources.map((source) => (
              <AcquisitionSource key={source.key} source={source} variants={variants}
                done={done} toggle={toggle} sequenced={questlinePages.has(source.key)} />
            ))}
          </section>
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
  const acquisitions = useMemo(
    () => (data.acquisitions || []).filter((acquisition) => !acquisition.epic),
    [data.acquisitions])
  const acquisitionKeys = useMemo(
    () => acquisitions.map((acquisition) => acquisition.key), [acquisitions])
  const previousAcquisitions = useRef(new Set(acquisitionKeys))
  const [openAcquisitions, setOpenAcquisitions] = useState(
    () => new Set(acquisitionKeys.length === 1 ? acquisitionKeys : []))
  const acquisitionKeyLine = acquisitionKeys.join('\u0000')
  useEffect(() => {
    const currentKeys = new Set(acquisitionKeys)
    const added = acquisitionKeys.filter((key) => !previousAcquisitions.current.has(key))
    setOpenAcquisitions((held) => {
      if (added.length) return new Set(added)
      return new Set([...held].filter((key) => currentKeys.has(key)))
    })
    previousAcquisitions.current = currentKeys
  // The joined line changes only when identities change; the array itself is
  // recreated with every API response and must not collapse a manually-opened row.
  }, [acquisitionKeyLine]) // eslint-disable-line react-hooks/exhaustive-deps
  const zones = useMemo(() => {
    const grouped = new Map()
    data.rows.filter((row) => !row.requirement && !row.epic && !row.gets?.length
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
              <ItemHeading item={target} card={target?.card || cards.get(target?.page_title)} context={`${className} epic weapon`}
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
      {acquisitions.map((acquisition) => (
          <Acquisition key={acquisition.key} acquisition={acquisition} cards={cards}
            done={done} toggle={toggle} questlinePages={questlinePages}
            open={openAcquisitions.has(acquisition.key)}
            onOpen={(isOpen) => setOpenAcquisitions((held) => {
              const next = new Set(held)
              if (isOpen) next.add(acquisition.key); else next.delete(acquisition.key)
              return next
            })} />
        ))}
      {(data.questlines || []).map((line) => (
        <Questline key={line.key} line={line} rows={rowsByKey}
          done={done} toggle={toggle} />
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
