import OverlayOptions, { useOverlays } from './OverlayOptions.jsx'

/* Stream overlay settings on `/account` — mint a link, revoke it, and the same
   options panel the dashboard opens beside its Mini switch.

   The dashboard is where these are actually reached (that is where somebody
   sits with OBS open), so this page is the long form: it holds more than one
   link, says what a link IS, and is where extra ones are minted. The controls
   themselves are `OverlayOptions`, shared, because two settings lists for one
   feature is how they end up disagreeing.

   STREAM OVERLAYS ONLY (`useOverlays('overlay')`). The in-game window is the
   same kind of link (schema v34) but it is not a thing anybody sets up from an
   account page — it is pasted into EQ2's browser while sitting in the game, so
   it is minted and revoked from the dashboard's In-game button and says so
   below rather than opening a second card here nobody would find. */

export default function OverlaySettings() {
  const { overlays, err, create, change, revoke } = useOverlays('overlay')

  return (
    <div className="card">
      <h2>Stream overlay</h2>
      <p className="note" style={{ marginTop: 4 }}>
        A page showing your live parse, for an OBS browser source. Anyone with
        the link sees the fight you are in — and nothing else, ever. Revoke it
        and the link stops working. The same options open from the{' '}
        <strong>Overlay</strong> button on the raid dashboard — which also has an{' '}
        <strong>In-game</strong> button, for the same parse in EQ2’s own browser
        window at a size that fits beside the game.
      </p>
      {err && <p className="err">{err}</p>}
      {overlays === null && <p className="muted">Loading…</p>}
      {overlays?.map((o) => (
        <OverlayOptions key={o.id} overlay={o}
                        onChange={(cfg) => change(o, cfg)}
                        onRevoke={() => revoke(o)} />
      ))}
      {overlays && !overlays.length && (
        <div className="formcol" style={{ marginTop: 8 }}>
          <button onClick={() => create()}>Create an overlay link</button>
        </div>
      )}
      {overlays?.length > 0 && (
        <div className="formcol" style={{ marginTop: 10 }}>
          <button className="chip" onClick={() => create()}>Add another</button>
        </div>
      )}
    </div>
  )
}
