const SOCKET_ORDER = ['yellow', 'black', 'green', 'orange', 'red', 'blue', 'purple',
  'cyan', 'grey', 'white', 'turquoise']

function socketColors(item) {
  if (!item) return []
  if (item.adornments) return item.adornments.map((adorn) => adorn.color).filter(Boolean)
  return Object.entries(item.adorns || {}).flatMap(([color, count]) =>
    Array.from({ length: count }, () => color))
}

export function itemSockets(item) {
  if (!item) return []
  const installed = [...(item.adornments || [])]
  const colors = item.card?.stats?.adornments?.length
    ? item.card.stats.adornments
    : socketColors(item)
  const rank = (color) => {
    const at = SOCKET_ORDER.indexOf(color)
    return at < 0 ? SOCKET_ORDER.length : at
  }
  return [...colors].sort((a, b) => rank(a) - rank(b)).map((color) => {
    const at = installed.findIndex((adorn) => adorn.color === color)
    const adorn = at >= 0 ? installed.splice(at, 1)[0] : { color }
    return { color, adorn }
  })
}

const SET_SLOT_NAMES = {
  Ear: ['ear', 'ears'],
  Finger: ['finger', 'fingers'],
  Wrist: ['wrist', 'wrists'],
  Shoulder: ['shoulder', 'shoulders'],
  Shoulders: ['shoulder', 'shoulders'],
  Primary: ['primary', 'one handed'],
  Secondary: ['secondary', 'one handed'],
}

export function setPieceForSlot(set, slot) {
  const names = SET_SLOT_NAMES[slot] || [String(slot || '').toLowerCase()]
  return (set?.pieces || []).find((piece) => {
    const suffix = piece.split(':').pop().trim().toLowerCase()
    return names.includes(suffix)
  }) || null
}

export function setFitsHost(set, item, slot) {
  if (!setPieceForSlot(set, slot)) return false
  const hostLevel = Number(item?.level) || null
  const adornLevel = Number(set?.level) || null
  if (!hostLevel || !adornLevel) return true
  const floor = Math.max(1, Math.floor(hostLevel / 10) * 10 - 20)
  return adornLevel <= hostLevel && adornLevel >= floor
}

export function adornmentName(value) {
  if (typeof value === 'string') return value || null
  return value?.set_name || value?.name || null
}

export function adornmentChanged(value, baseline) {
  return adornmentName(value) !== adornmentName(baseline)
}

function carriedWhite(adorn) {
  if (!adorn?.name) return null
  if (adorn.key) return adorn
  return {
    ...adorn,
    key: `carried:${adorn.id || adorn.name}`,
    carried: true,
    projection_stats: adorn.planner_stats || {},
  }
}

/* Adornments belong to the concrete equipment position in a plan, not to a
   candidate host. Moving from equipped gear to a candidate (or between two
   candidates) carries the effective white/turquoise choices into matching
   sockets on the next host. Socket indexes cannot be copied directly because
   an extra yellow or black socket shifts white/turquoise positions. */
export function inheritSlotAdornments({
  slot, fromItem, toItem, setSlots = {}, adornSlots = {},
}) {
  const nextSetSlots = { ...setSlots }
  const nextAdornSlots = { ...adornSlots }
  const fromSockets = itemSockets(fromItem)
  const toSockets = itemSockets(toItem)
  const previousWhites = fromSockets
    .map((socket, index) => ({ ...socket, index }))
    .filter((socket) => socket.color === 'white')
  const previousChoices = adornSlots[slot] || {}
  const whiteChoices = {}

  toSockets.forEach((socket, index) => {
    if (socket.color !== 'white') return
    const previous = previousWhites[Object.keys(whiteChoices).length]
    if (!previous) {
      whiteChoices[index] = null
      return
    }
    whiteChoices[index] = Object.prototype.hasOwnProperty.call(
      previousChoices, previous.index)
      ? previousChoices[previous.index]
      : carriedWhite(previous.adorn)
  })
  if (Object.keys(whiteChoices).length) nextAdornSlots[slot] = whiteChoices
  else delete nextAdornSlots[slot]

  const hasTurquoise = toSockets.some((socket) => socket.color === 'turquoise')
  if (hasTurquoise) {
    const selected = Object.prototype.hasOwnProperty.call(setSlots, slot)
      ? setSlots[slot]
      : fromItem?.set_name || fromSockets.find(
        (socket) => socket.color === 'turquoise')?.adorn?.set_name
        || fromSockets.find((socket) => socket.color === 'turquoise')?.adorn?.name
        || null
    nextSetSlots[slot] = selected
  } else {
    delete nextSetSlots[slot]
  }

  return { setSlots: nextSetSlots, adornSlots: nextAdornSlots }
}

/* A NAMED SET IS A SNAPSHOT, NOT A POINTER BACK TO "EQUIPPED". The working
   loadout normally leaves untouched sockets implicit so a refreshed character
   naturally shows what they now wear. Saving needs the opposite rule:
   materialize each selectable socket so a later load restores the white and
   turquoise adornments (including deliberately empty sockets) visible now. */
export function snapshotAdornmentState(shortlist, gear = []) {
  let setSlots = { ...(shortlist?.set_slots || {}) }
  let adornSlots = { ...(shortlist?.adorn_slots || {}) }
  const active = shortlist?.active || {}
  const items = shortlist?.items || []
  const slots = new Set([
    ...gear.map((item) => item?.key).filter(Boolean),
    ...Object.keys(active),
    ...Object.keys(setSlots),
    ...Object.keys(adornSlots),
  ])

  slots.forEach((slot) => {
    const page = active[slot]
    const item = (page && items.find((candidate) => candidate.page_title === page))
      || gear.find((candidate) => candidate.key === slot)
    /* Keep carried data if an old/incomplete payload no longer describes the
       host. Silently deleting it here would make Save destructive. */
    if (!item) return
    const captured = inheritSlotAdornments({
      slot, fromItem: item, toItem: item, setSlots, adornSlots,
    })
    setSlots = captured.setSlots
    adornSlots = captured.adornSlots
  })

  return { setSlots, adornSlots }
}
