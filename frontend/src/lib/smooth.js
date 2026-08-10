import { useEffect, useRef, useState } from 'react'

/* The dashboard's clocks.

   This is a CLOCK module, not an animation one, and the distinction is the
   whole point of it. Nothing on the live parse is animated: figures and bars
   change when a payload changes them. Tweened numbers and sliding bars were
   both built and both removed — a rate counting up to its new value cannot be
   read while it does it, and the first seconds of a pull, where every payload
   is an enormous relative change, turned into a slot machine. The cure for
   numbers that feel stale is a shorter ingest cadence, and that is what was
   done instead (docs/live.md).

   What remains is the two readings that are genuinely functions of TIME rather
   than of the last payload: the elapsed clock, and the AoE countdowns draining
   toward their next cast. Those keep counting between payloads because that is
   what a clock does — a countdown that only moved when the server spoke would
   be wrong about the thing it exists to say.

   ONE requestAnimationFrame loop drives them. React batches every `setState`
   made inside one tick into a single render pass — which only happens if they
   all land in the same tick.

   It drives DIGITS. Anything that has to move CONTINUOUSLY is handed to the
   compositor instead (the AoE drain bar is a CSS animation seeked to its
   position, `AoeTimers.jsx`): a JS loop repainting a length is sampled by
   whatever is compositing the page, and an overlay in OBS composites at 60fps
   against a 20Hz loop — three frames, four frames, three — which reads as
   judder on the stream even when every value is right. */

/* 20Hz. What runs on this is a countdown's digits, which change once a second;
   the tick only has to be fine enough to catch the crossing, and the cost is
   per subscriber per tick. Anything that must move every frame does not belong
   here — see above. */
const FRAME_MS = 50

const subs = new Set()
let raf = 0
let last = 0

function pump(t) {
  raf = subs.size ? requestAnimationFrame(pump) : 0
  if (t - last < FRAME_MS) return
  last = t
  for (const fn of Array.from(subs)) fn(t)
}

function subscribe(fn) {
  subs.add(fn)
  if (!raf) { last = 0; raf = requestAnimationFrame(pump) }
  return () => {
    subs.delete(fn)
    if (!subs.size && raf) { cancelAnimationFrame(raf); raf = 0 }
  }
}

/* Run `cb` on the shared loop while `active`. The loop stops itself when the
   last subscriber leaves, so a dashboard between pulls costs nothing. */
export function useTicker(cb, active = true) {
  const held = useRef(cb)
  useEffect(() => { held.current = cb })
  useEffect(() => {
    if (!active) return undefined
    return subscribe((t) => held.current(t))
  }, [active])
}

/* Wall-clock seconds. `performance.now()` cannot be compared across the tab
   being suspended; `Date.now()` can, and a raid dashboard sits behind the game
   for minutes at a time. */
const wall = () => Date.now() / 1000

/* Behind us by more than this and the payload is describing a different fight,
   not a slow batch. */
const SNAP_S = 3

/* The LOG clock, read as a function rather than as a value.

   The AoE countdowns are in log time and the browser only has its own, so the
   panel needs to know what the log clock says RIGHT NOW, between payloads. It
   used to anchor flat — every payload reset the clock to the `log_ts` that
   payload carried — and that is a sawtooth: `log_ts` is the newest line the
   plugin has SENT, so it is always behind the log clock by however long that
   batch took to arrive, and the amount varies. Every couple of seconds the
   countdowns jumped BACKWARD by a fraction of a second and then drained
   forward again, which is what "the bars jerk" was, and the same yank is what
   made the digits print a second twice.

   So the correction is the elapsed clock's, for the elapsed clock's reasons
   (below): a payload AHEAD of our prediction is real and is taken; a payload a
   fraction BEHIND it is batch latency and our count stands; only one well
   behind (`SNAP_S`) is a different fight and re-anchors.

   Returns a getter, not a number, because the caller wants the time at the
   moment it draws — a value would be one more thing to re-render for. */
export function useLogClock(logTs) {
  const anchor = useRef(null)
  if (!anchor.current) anchor.current = { base: logTs || 0, at: wall() }

  useEffect(() => {
    if (typeof logTs !== 'number' || !Number.isFinite(logTs)) return
    const predicted = anchor.current.base + (wall() - anchor.current.at)
    const take = logTs > predicted || logTs < predicted - SNAP_S
    anchor.current = { base: take ? logTs : predicted, at: wall() }
  }, [logTs])

  return useRef(() => anchor.current.base + (wall() - anchor.current.at)).current
}

/* The elapsed clock, ticking once a second and never skipping one.

   `elapsed_s` is LOG time and it arrives in whatever steps the uploader's
   batches happen to be, so printing it directly gives "0:04, 0:04, 0:07" — a
   clock that stutters reads as a broken clock even when the parse behind it is
   perfect. This counts in the browser and takes each payload as a correction.

   The correction is ASYMMETRIC by design, because the two directions are two
   different events. A payload AHEAD of our count means the fight really did
   advance further than we counted — a batch we had not seen, or a replay
   running faster than real time — so it is taken as is. A payload a fraction
   BEHIND our count is batch latency, which varies either way; following it
   down is precisely how a clock prints the same second twice or drops one, so
   our count stands. Only a payload well behind us (a new pull, a rewound
   replay) is a different fight, and that one starts the clock again.

   `running` is the stale flag: when the picture has stopped moving the clock
   stops with it, because a frozen fight whose timer keeps climbing is the one
   thing on this screen that would be actively wrong. */
export function useSmoothSeconds(seconds, running = true) {
  const has = typeof seconds === 'number' && Number.isFinite(seconds)
  const anchor = useRef({ base: has ? seconds : 0, at: wall() })
  const [shown, setShown] = useState(() => Math.floor(has ? seconds : 0))
  const shownRef = useRef(shown)

  useEffect(() => {
    if (!has) return
    const predicted = anchor.current.base + (wall() - anchor.current.at)
    const take = seconds > predicted || seconds < predicted - SNAP_S
    anchor.current = { base: take ? seconds : predicted, at: wall() }
  }, [has, seconds])

  useTicker(() => {
    const v = Math.floor(anchor.current.base + (wall() - anchor.current.at))
    if (v !== shownRef.current) { shownRef.current = v; setShown(v) }
  }, running && has)

  return has ? shown : seconds
}
