import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api.js'
import { Switch } from './Settings.jsx'

const BLANK = { channel: 'any', query: '', speaker: '', exclude_query: '', cooldown_s: 300 }
const CHANNEL = { any: 'Any channel', general: 'General', lfg: 'LFG', auction: 'Auction' }
const COOL = { 60: '1 minute', 300: '5 minutes', 900: '15 minutes', 3600: '1 hour' }

function RuleRow({ rule, onToggle, onDelete, busy }) {
  const details = [CHANNEL[rule.channel]]
  if (rule.speaker) details.push(`from ${rule.speaker}`)
  if (rule.exclude_query) details.push(`except “${rule.exclude_query}”`)
  details.push(`${COOL[rule.cooldown_s]} cooldown`)
  return (
    <div className={`chatalertrule${rule.enabled ? '' : ' mutedrule'}`}>
      <Switch on={rule.enabled} disabled={busy}
              onChange={(enabled) => onToggle(rule.id, enabled)} />
      <span className="rulecopy">
        <b>“{rule.query}”</b>
        <small>{details.join(' · ')}</small>
      </span>
      <button className="ruledelete" disabled={busy} onClick={() => onDelete(rule.id)}
              title="Delete this alert" aria-label={`Delete alert for ${rule.query}`}>×</button>
    </div>
  )
}

export default function ChatAlerts({ user, onClose }) {
  const [data, setData] = useState(null)
  const [pair, setPair] = useState(null)
  const [form, setForm] = useState(BLANK)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)

  const load = async (quiet = false) => {
    if (!user) return
    try {
      const next = await api.chatAlerts()
      setData(next)
      if (next.discord) setPair(null)
      if (!quiet) setError(null)
      return next
    } catch (err) {
      if (!quiet) setError(err.message)
      return null
    }
  }

  useEffect(() => { load() }, [user])

  /* A successful `/link` arrives at Discord, not this browser. Poll only while
     a live code is on screen, then stop the moment the account has a DM. */
  useEffect(() => {
    if (!pair || data?.discord) return undefined
    const tick = async () => {
      if (Date.now() / 1000 >= pair.expires_ts) {
        setPair(null)
        setError('That code expired. Generate a new one when you are ready.')
        return
      }
      const next = await load(true)
      if (next?.discord) setMessage('Discord connected. Add an alert rule below.')
    }
    const timer = window.setInterval(tick, 2500)
    return () => window.clearInterval(timer)
  }, [pair, data?.discord])

  async function run(fn, success) {
    setBusy(true); setError(null); setMessage(null)
    try {
      await fn()
      await load(true)
      if (success) setMessage(success)
      return true
    } catch (err) { setError(err.message); return false }
    finally { setBusy(false) }
  }

  async function makePair() {
    setBusy(true); setError(null); setMessage(null)
    try { setPair(await api.createChatAlertPair()) }
    catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  async function addRule(ev) {
    ev.preventDefault()
    const body = { ...form, cooldown_s: Number(form.cooldown_s) }
    if (await run(() => api.createChatAlertRule(body), 'Alert added.')) setForm(BLANK)
  }

  if (!user) {
    return (
      <section className="chatalertpanel">
        <div className="chatalerthead">
          <div><span className="eyebrow">Private delivery</span><h2>Discord chat alerts</h2></div>
          <button className="close" onClick={onClose} aria-label="Close chat alerts">×</button>
        </div>
        <div className="chatalertempty">
          <b>Chat stays public. Alerts belong to an account.</b>
          <p>Sign in with EQ2Advanced, then pair Discord with a one-time code. You never add the app to a server.</p>
          <Link className="button" to="/login">Sign in to set alerts</Link>
        </div>
      </section>
    )
  }

  return (
    <section className="chatalertpanel">
      <div className="chatalerthead">
        <div><span className="eyebrow">Private delivery</span><h2>Discord chat alerts</h2></div>
        <button className="close" onClick={onClose} aria-label="Close chat alerts">×</button>
      </div>

      {!data && !error && <p className="muted">Loading alerts…</p>}
      {error && <p className="err chatalertmsg">{error}</p>}
      {message && <p className="status-ready chatalertmsg">{message}</p>}

      {data && !data.configured && (
        <div className="chatalertempty">
          <b>Discord alerts are not configured on this server yet.</b>
          <p>The bot application credentials still need to be added by the site owner.</p>
        </div>
      )}

      {data?.configured && !data.discord && (
        <div className="discordpair">
          <div className="pairstep"><i>1</i><span><b>Add EQ2Advanced to your Discord apps</b><small>This installs it for you—not for a server.</small></span>
            <a className="button" href={data.install_url} target="_blank" rel="noreferrer noopener">Add to my apps</a>
          </div>
          <div className="pairstep"><i>2</i><span><b>Generate your pairing code</b><small>It works once and expires after ten minutes.</small></span>
            {!pair && <button disabled={busy} onClick={makePair}>Generate code</button>}
          </div>
          {pair && (
            <div className="pairfinish">
              <div className="pairstep"><i>3</i><span><b>Open a private chat with the bot</b><small>On the Discord profile that opens, select <strong>Message</strong>.</small></span>
                <a className="button" href={data.bot_profile_url} target="_blank" rel="noreferrer noopener">Open EQ2Advanced in Discord</a>
              </div>
              <div className="paircode">
                <span>Copy this entire line, paste it into the new bot chat, then press Enter:</span>
                <code>/link code:{pair.code}</code>
                <small>Waiting for Discord to connect…</small>
              </div>
            </div>
          )}
          <p className="pairnote">You are messaging the EQ2Advanced bot directly—not adding it to a server. It cannot read your servers and Discord is not used to sign you in.</p>
        </div>
      )}

      {data?.discord && (
        <>
          <div className="discordlinked">
            <span className="discordidentity"><i className={data.discord.paused ? 'paused' : ''} />
              <span><b>{data.discord.display_name}</b><small>{data.discord.paused ? 'Alerts paused' : 'Private DM connected'}</small></span>
            </span>
            <div className="discordactions">
              <button disabled={busy} onClick={() => run(
                () => api.setChatAlertPaused(!data.discord.paused),
                data.discord.paused ? 'Alerts resumed.' : 'Alerts paused.')}>{data.discord.paused ? 'Resume' : 'Pause'}</button>
              <button disabled={busy || data.discord.paused}
                      onClick={() => run(api.testChatDiscord, 'Test message sent.')}>Send test</button>
              <button className={confirmDisconnect ? 'danger' : ''} disabled={busy}
                      onClick={() => confirmDisconnect
                        ? run(api.disconnectChatDiscord, 'Discord disconnected.')
                        : setConfirmDisconnect(true)}>{confirmDisconnect ? 'Confirm disconnect' : 'Disconnect'}</button>
            </div>
          </div>
          {data.discord.last_error && <p className="err chatalertmsg">{data.discord.last_error}</p>}

          <div className="chatalertrules">
            <div className="chatalertsectionhead">
              <div><h3>Alert rules</h3><p>Messages arrive here even when the chat page is closed.</p></div>
              <span>{data.rules.length} / 20</span>
            </div>
            <form className="chatalertform" onSubmit={addRule}>
              <label><span>Channel</span><select value={form.channel}
                onChange={(e) => setForm({ ...form, channel: e.target.value })}>
                {Object.entries(CHANNEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select></label>
              <label className="match"><span>Contains</span><input type="text" value={form.query}
                maxLength="100" placeholder="item, guild, class or phrase" required
                onChange={(e) => setForm({ ...form, query: e.target.value })} /></label>
              <label><span>Speaker <i>optional</i></span><input type="text" value={form.speaker}
                maxLength="40" placeholder="Exact character"
                onChange={(e) => setForm({ ...form, speaker: e.target.value })} /></label>
              <label><span>Exclude <i>optional</i></span><input type="text" value={form.exclude_query}
                maxLength="100" placeholder="Ignore if it contains…"
                onChange={(e) => setForm({ ...form, exclude_query: e.target.value })} /></label>
              <label><span>Cooldown</span><select value={form.cooldown_s}
                onChange={(e) => setForm({ ...form, cooldown_s: Number(e.target.value) })}>
                {Object.entries(COOL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select></label>
              <button type="submit" disabled={busy || form.query.trim().length < 2
                || data.rules.length >= 20}>Add alert</button>
            </form>
            <div className="chatalertrulelist">
              {data.rules.length === 0 && <p className="muted">No rules yet. Add a phrase to watch for.</p>}
              {data.rules.map((rule) => <RuleRow key={rule.id} rule={rule} busy={busy}
                onToggle={(id, enabled) => run(() => api.updateChatAlertRule(id, { enabled }))}
                onDelete={(id) => run(() => api.deleteChatAlertRule(id), 'Alert deleted.')} />)}
            </div>
          </div>
        </>
      )}
    </section>
  )
}
