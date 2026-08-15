const KIND_LABEL = {
  raid: 'Raid', group: 'Group', solo: 'Solo', unknown: 'Unknown',
}

function Level({ value, text }) {
  const shown = text || value
  return shown ? <span>Level {shown}</span> : null
}

function Gets({ row }) {
  if (row.gets.length) {
    return (
      <span className="outlinegets">
        Gets you {row.gets.map((g) => (
          <span key={`${g.page_title}:${g.via_set || ''}`}>
            {g.via_set ? `${g.via_set} (via ${g.name})` : g.name}
          </span>
        )).reduce((all, part, i) => (
          i ? [...all, ', ', part] : [part]
        ), [])}
      </span>
    )
  }
  if (row.why === 'prerequisite' && row.opens.length) {
    return <span className="outlineopens">Opens {row.opens.join(', ')}</span>
  }
  if (row.why === 'target') {
    return <span className="outlineopens">Kept as a target</span>
  }
  return null
}

export default function PlanOutline({ data, targetsInList, onToggleTarget }) {
  return (
    <div className="planoutline">
      <section className="card outlineprelude">
        <header className="outlinehead">
          <div>
            <div className="seclabel">Start here</div>
            <h2>The expansion prelude</h2>
          </div>
          <span className="muted">Hand-kept game knowledge</span>
        </header>
        <div className="preludelist">
          {data.prelude.map((row, i) => (
            <article className="preluderow" key={`${row.era}:${i}:${row.title}`}>
              <div className="preludeline">
                {row.wiki
                  ? <a href={row.wiki} target="_blank" rel="noreferrer noopener">{row.title}</a>
                  : <strong>{row.title}</strong>}
                <span aria-hidden="true"> — </span>
                <span>{row.why}</span>
              </div>
              <div className="outlinemeta">
                <Level value={row.level} />
                {row.zone && <span>{row.zone}</span>}
                {row.kind && <span>{row.kind}</span>}
              </div>
              {row.detail && <p>{row.detail}</p>}
            </article>
          ))}
        </div>
      </section>

      <section className="outlinebody">
        <header className="outlinehead">
          <div>
            <div className="seclabel">Your outline</div>
            <h2>Prerequisites first, then level</h2>
          </div>
          {!!data.rows.length && (
            <span className="muted">
              {data.counts.quests} quest{data.counts.quests === 1 ? '' : 's'}
              {data.counts.targets ? ` · ${data.counts.targets} target${data.counts.targets === 1 ? '' : 's'}` : ''}
            </span>
          )}
        </header>

        {!data.rows.length ? (
          <div className="card outlineempty">
            <p>Pick gear or adornments to build the quest chain beneath the prelude.</p>
            <span className="muted">
              Tick a body row later to keep that quest or monster as a target of its own.
            </span>
          </div>
        ) : (
          <ol className="outlinelist">
            {data.rows.map((row, i) => (
              <li className={`outlinerow ${row.kind}`} key={row.key}>
                <span className="outlinenum">{i + 1}</span>
                <input type="checkbox" checked={targetsInList.has(row.key)}
                       aria-label={`${targetsInList.has(row.key) ? 'Remove' : 'Keep'} ${row.name} as a target`}
                       onChange={() => onToggleTarget(row)} />
                <div className="outlinecontent">
                  <div className="outlinetitle">
                    <i className={`skind ${row.difficulty}`}>{row.kind}</i>
                    <a href={row.wiki} target="_blank" rel="noreferrer noopener">
                      {row.name}
                    </a>
                    <div className="outlinemeta">
                      <Level value={row.level} text={row.level_text} />
                      {row.zone && <span>{row.zone}</span>}
                      {row.difficulty && (
                        <span>{row.diff || KIND_LABEL[row.difficulty] || row.difficulty}</span>
                      )}
                    </div>
                  </div>
                  <Gets row={row} />
                </div>
              </li>
            ))}
          </ol>
        )}

        {!!data.unplaced.length && (
          <p className="muted outlineunplaced">
            {data.unplaced.length} shortlist entr{data.unplaced.length === 1 ? 'y' : 'ies'}
            {' '}could not be placed in the selected expansion{data.eras.length === 1 ? '' : 's'}.
          </p>
        )}
      </section>
    </div>
  )
}
