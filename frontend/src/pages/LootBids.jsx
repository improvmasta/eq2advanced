import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api.js'
import './LootBids.css'

const TOKEN_KEY = 'eq2a:loot-bids:token'
const NAME_KEY = 'eq2a:loot-bids:name'

function secondsLeft(item, clock) {
  return item.closes_ts == null ? null : Math.max(0, Math.ceil(item.closes_ts - clock))
}

function BidItem({ item, token, clock, mutate }) {
  const [value, setValue] = useState(item.my_bid?.bid || '')
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState(null)
  const left = secondsLeft(item, clock)
  const test = item.mob === 'Training Dummy · test chest'
  const selectedWinners = Object.keys(draft || {}).length

  useEffect(() => {
    if (item.my_bid?.bid != null) setValue(item.my_bid.bid)
  }, [item.my_bid?.bid])

  useEffect(() => {
    if (item.state === 'closed') {
      setDraft(Object.fromEntries(item.projected_winners.map((w) => [w.id, w.price])))
    } else {
      setDraft(null)
    }
  }, [item.state, item.opened_ts])

  const act = async (fn) => {
    setBusy(true)
    try { mutate(await fn()) } finally { setBusy(false) }
  }

  const submit = (e) => {
    e.preventDefault()
    if (String(value).trim()) act(() => api.putLootBid(token, item.id, value))
  }

  return (
    <article className={`biditem ${item.state}`}>
      <div className="biditem-main">
        {item.icon
          ? <img src={`/api/items/icon/${item.icon}.png`} width="34" height="34" alt="" />
          : <span className="biditem-noicon" aria-hidden="true">◆</span>}
        <div className="biditem-name">
          <strong className={item.rarity ? `rarity-${item.rarity.toLowerCase()}` : ''}>
            {item.qty > 1 && `${item.qty} × `}{item.name}
          </strong>
          <span>{item.chest}{item.looter ? ` · linked by ${item.looter}` : ''}</span>
        </div>
        <div className="biditem-status">
          {item.state === 'waiting' && <><b>Waiting</b><span>not linked yet</span></>}
          {item.state === 'open' && <><b>{left}s</b><span>{item.bid_count} bid{item.bid_count === 1 ? '' : 's'}</span></>}
          {item.state === 'closed' && <><b>Closed</b><span>{item.bid_count} bid{item.bid_count === 1 ? '' : 's'}</span></>}
          {item.state === 'awarded' && <><b>Awarded</b><span>{item.awards.map((a) => a.name).join(', ')}</span></>}
        </div>
      </div>

      {item.state === 'waiting' && (
        <div className="biditem-wait">
          {test && item.is_looter ? (
            <button className="solid" disabled={busy}
                    onClick={() => act(() => api.linkTestLootItem(token, item.id))}>
              Link in raid chat
            </button>
          ) : <span>Link this item in raid chat to start its countdown.</span>}
        </div>
      )}

      {item.state === 'open' && (
        <form className="bidentry" onSubmit={submit}>
          <label htmlFor={`bid-${item.id}`}>Your bid</label>
          <input id={`bid-${item.id}`} type="number" value={value} min={5} step={1}
                 placeholder="5 points minimum"
                 onChange={(e) => setValue(e.target.value)} />
          <button className="solid" disabled={busy || !String(value).trim()}>
            {item.my_bid ? 'Update bid' : 'Place bid'}
          </button>
          {item.my_bid && <span className="bidsaved">Bid saved</span>}
        </form>
      )}

      {item.is_officer && item.state === 'open' && (
        <section className="looterbox sealedbids">
          <strong>Sealed bidding</strong>
          <span>{item.bid_count} bid{item.bid_count === 1 ? '' : 's'} received. Names and amounts unlock when the timer ends.</span>
        </section>
      )}

      {item.is_officer && item.state === 'closed' && (
        <section className="looterbox">
          <div className="looterbox-head">
            <strong>Officer award desk</strong>
            <span>Bids remained sealed until the countdown ended.</span>
          </div>
          {!item.bids.length ? <p>No bids yet.</p> : (
            <div className="looterbids">
              {item.bids.map((bid) => (
                <div className={bid.winner ? 'winner' : ''} key={bid.id}>
                  {item.state === 'closed' && (
                    <input type="checkbox" aria-label={`Select ${bid.name} as a winner`}
                           checked={draft?.[bid.id] != null}
                           disabled={draft?.[bid.id] == null && selectedWinners >= item.qty}
                           onChange={(e) => setDraft((current) => {
                             const next = { ...(current || {}) }
                             if (e.target.checked) next[bid.id] = Math.min(bid.bid, 5)
                             else delete next[bid.id]
                             return next
                           })} />
                  )}
                  <b>{bid.name}</b><span>Bid {bid.bid}</span>
                  {item.state === 'closed' && draft?.[bid.id] != null && (
                    <label className="awardprice">Pays
                      <input type="number" min={5} max={bid.bid} step={1}
                             value={draft[bid.id]}
                             onChange={(e) => setDraft((current) => ({
                               ...current, [bid.id]: e.target.value,
                             }))} />
                    </label>
                  )}
                </div>
              ))}
            </div>
          )}
          {item.state === 'closed' && item.projected_winners.length > 0 && (
            <div className="bidresolution">
              <span>Calculated: {item.projected_winners.map((w) => `${w.name} pays ${w.price}`).join(' · ')}. Adjust the winners or prices above if needed.</span>
              <button className="solid" disabled={busy}
                      onClick={() => act(() => api.awardLootBid(token, item.id,
                        Object.entries(draft || {}).map(([bidId, price]) => ({
                          bid_id: Number(bidId), price: Number(price),
                        }))))}>
                Confirm &amp; announce
              </button>
            </div>
          )}
        </section>
      )}

      {!item.is_officer && item.state === 'closed' && (
        <div className="bidresult">Bidding closed · the officers are choosing.</div>
      )}
      {item.state === 'awarded' && (
        <div className="bidresult won">Awarded to {item.awards.map((a, i) => (
          <span key={a.name}>{i > 0 && ', '}<b>{a.name}</b> for {a.price}</span>
        ))}</div>
      )}
      {test && ['closed', 'awarded'].includes(item.state) && (
        <div className="bidrerun">
          <button disabled={busy} onClick={() => act(() => api.linkTestLootItem(token, item.id))}>
            Re-link in raid chat
          </button>
          <span>Clears this result and starts a fresh countdown.</span>
        </div>
      )}
      {!test && ['closed', 'awarded'].includes(item.state) && (
        <div className="bidrerun">Re-link this item in raid chat to clear it and run bidding again.</div>
      )}
    </article>
  )
}

export default function LootBids() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY))
  const [name, setName] = useState(() => localStorage.getItem(NAME_KEY) || '')
  const [invite] = useState(() => new URLSearchParams(window.location.search).get('code') || '')
  const [board, setBoard] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [clock, setClock] = useState(() => Date.now() / 1000)
  const [profileName, setProfileName] = useState('')
  const [account, setAccount] = useState({ username: '', password: '' })

  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now() / 1000), 250)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (token) return
    api.lootBidAccountAccess().then((data) => {
      localStorage.setItem(TOKEN_KEY, data.token)
      localStorage.setItem(NAME_KEY, data.board.player)
      setToken(data.token); setName(data.board.player); setBoard(data.board)
      window.dispatchEvent(new Event('eq2a:portal-access'))
    }).catch(() => {})
  }, [token])

  useEffect(() => {
    if (!token) return undefined
    let stopped = false
    const refresh = () => api.lootBidState(token).then((next) => {
      if (!stopped) { setBoard(next); setError('') }
    }).catch((e) => {
      if (stopped) return
      setError(e.message)
      if (e.status === 401 || e.status === 403) {
        localStorage.removeItem(TOKEN_KEY)
        setToken(null)
      }
    })
    refresh()
    const timer = setInterval(refresh, 1000)
    return () => { stopped = true; clearInterval(timer) }
  }, [token])

  useEffect(() => { if (board?.player) setProfileName(board.player) }, [board?.player])

  const groups = useMemo(() => {
    const out = []
    for (const item of board?.items || []) {
      let group = out.find((g) => g.mob === item.mob && Math.abs(g.ts - item.ts) < 30)
      if (!group) { group = { mob: item.mob, ts: item.ts, items: [] }; out.push(group) }
      group.items.push(item)
    }
    return out
  }, [board])

  const enroll = async (e) => {
    e.preventDefault(); setBusy(true); setError('')
    try {
      const data = await api.enrollLootBids(name, invite)
      localStorage.setItem(TOKEN_KEY, data.token)
      localStorage.setItem(NAME_KEY, data.board.player)
      setToken(data.token); setName(data.board.player); setBoard(data.board)
      window.history.replaceState({}, '', '/guild/skill-issue')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const leave = () => {
    localStorage.removeItem(TOKEN_KEY)
    setToken(null); setBoard(null)
    window.dispatchEvent(new Event('eq2a:portal-access'))
  }

  const openTest = async () => {
    setBusy(true); setError('')
    try { setBoard(await api.openTestLootChest(token)) }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (!token) return (
    <div className="lootbids join">
      <div className="joincard">
        <span className="eyebrow">Skill Issue · private portal</span>
        <h1>Live Loot Bids</h1>
        {invite ? <>
          <p>This invite enrolls this browser once. Choose the EQ2 player name you raid on; you can change it later.</p>
          <form onSubmit={enroll}>
            <label htmlFor="loot-player">Player name</label>
            <div><input id="loot-player" type="text" autoFocus value={name} maxLength={30}
                        onChange={(e) => setName(e.target.value)} />
              <button className="solid" disabled={busy || !name.trim()}>Enter portal</button></div>
          </form>
        </> : <p>This is a private Skill Issue portal. Open the invite link an officer gave you. If your eq2advanced account is already linked, sign in and reload this page.</p>}
        {error && <p className="formerror">{error}</p>}
      </div>
    </div>
  )

  return (
    <div className="lootbids">
      <div className="pagehead">
        <div><span className="eyebrow">Skill Issue · private portal</span><h1>Live Loot Bids</h1></div>
        <div className="raidcontext">
          <span><small>Current zone</small><b>{board?.zone || 'Waiting for officer ACT'}</b></span>
          <span><small>Player</small><b>{board?.player || name}</b> · {board?.access_label}</span>
        </div>
        <div className="actions">
          {board?.role === 'officer' && <button disabled={busy} onClick={openTest}>Open test chest</button>}
          <button className="linklike" onClick={leave}>Forget this browser</button>
        </div>
      </div>
      <div className="lootflow" aria-label="Loot bidding flow">
        <span><b>1</b> Chest opens</span><i>→</i>
        <span><b>2</b> Item linked in raid chat</span><i>→</i>
        <span><b>3</b> Private bids</span><i>→</i>
        <span><b>4</b> Looter awards</span>
      </div>
      {error && <p className="formerror">{error}</p>}
      {board && <details className="portalsettings">
        <summary>Portal access &amp; members</summary>
        <div className="portalsettings-grid">
          <section>
            <h2>Your access</h2>
            <label>Player name</label>
            <div className="inlineform"><input value={profileName} maxLength={30}
              onChange={(e) => setProfileName(e.target.value)} />
              <button onClick={async () => {
                setBusy(true); setError('')
                try {
                  const next = await api.updateLootBidProfile(token, profileName)
                  setBoard(next); setName(next.player); localStorage.setItem(NAME_KEY, next.player)
                } catch (e) { setError(e.message) } finally { setBusy(false) }
              }} disabled={busy || !profileName.trim()}>Change name</button></div>
            <label>Personal ACT token</label><code>{token}</code>
            <small>Paste this once into the separate loot plugin. Its officer permissions update automatically.</small>
          </section>
          {!board.has_account && <section>
            <h2>Optional full account</h2>
            <p>Add a username and password without changing this portal identity, token, or role.</p>
            <label>Site username</label><input value={account.username}
              onChange={(e) => setAccount({ ...account, username: e.target.value })} />
            <label>Password</label><input type="password" value={account.password}
              onChange={(e) => setAccount({ ...account, password: e.target.value })} />
            <button disabled={busy || !account.username || account.password.length < 8}
              onClick={async () => {
                setBusy(true); setError('')
                try { setBoard(await api.convertLootBidAccount(token, account.username, account.password)) }
                catch (e) { setError(e.message) } finally { setBusy(false) }
              }}>Create full eq2advanced account</button>
          </section>}
          {board.can_manage && <section className="memberadmin">
            <h2>Member access</h2>
            <label>Invite link</label><code>{`${window.location.origin}/guild/skill-issue?code=${board.invite_code}`}</code>
            <small>Members only need the coded link once.</small>
            <div className="memberlist">{board.members.map((member) => (
              <div key={member.id}><b>{member.name}</b>
                <span>{member.has_account ? 'full account' : 'portal only'}</span>
                {member.can_manage ? <em>Portal admin</em> : <label>
                  <input type="checkbox" checked={member.role === 'officer'}
                    onChange={async (e) => {
                      try { setBoard(await api.setLootBidOfficer(token, member.id, e.target.checked)) }
                      catch (err) { setError(err.message) }
                    }} /> Officer
                </label>}
              </div>
            ))}</div>
          </section>}
        </div>
      </details>}
      {!board && <div className="card">Loading guild board…</div>}
      {board && <div className="lootworkspace">
        <main>
          {!groups.length && (
            <div className="emptyloot">
              <h2>Waiting for a chest</h2>
              <p>Chest contents will appear here as soon as an officer's ACT plugin reports them.</p>
              {board.role === 'officer' && <button className="solid" disabled={busy} onClick={openTest}>Open a test chest</button>}
            </div>
          )}
          {groups.map((group) => (
            <section className="chestgroup" key={`${group.mob}-${group.ts}`}>
              <header><div><span className="chesticon">▰</span><h2>{group.mob}</h2></div>
                <span>{group.items.length} item{group.items.length === 1 ? '' : 's'}</span></header>
              <div className="chestitems">
                {group.items.map((item) => (
                  <BidItem key={item.id} item={item} token={token} clock={clock}
                           mutate={(next) => { setBoard(next); setError('') }} />
                ))}
              </div>
            </section>
          ))}
        </main>
        <aside className="awardlog">
          <header><h2>Raid loot log</h2><span>{board.award_log?.length || 0} awards</span></header>
          {!board.award_log?.length ? <p>No loot awarded yet.</p> : board.award_log.map((award) => (
            <div className="awardlog-row" key={award.id}>
              <b>{award.name}</b><span>{award.item_name}</span>
              <em>{award.price} pts</em>
              <small>{new Date(award.awarded_ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} · {award.mob}</small>
            </div>
          ))}
        </aside>
      </div>}
      <p className="lootcaveat">Player names are name-only for this experiment; they are not yet verified against site accounts or raid leadership.</p>
    </div>
  )
}
