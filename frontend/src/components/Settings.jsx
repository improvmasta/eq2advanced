/* The two pieces every settings list on this site is made of.

   A ROW is a name, a line saying what the setting actually does, and the
   control on the right. A SWITCH is the control for the rows that ask "is this
   on" — a checkbox says "pick this", which is a different question, and the
   chips this idiom replaced read as status rather than as something you can
   change.

   They live here rather than in the panel that happened to need them first
   because they are now shared: the stream overlay's options
   (`OverlayOptions.jsx`) and the mini rail's ⚙ (`MiniRail.jsx`) are the same
   kind of list — what is on screen while the raid runs — and two copies of a
   settings row is two settings lists that quietly drift apart. The look is one
   look; where it needs to be tighter, the panel scopes it in CSS
   (`.miniconf .settingrow`), never by rebuilding the row.

   `disabled` on a switch is deliberately not "hidden": a row that vanishes
   takes its setting with it, and coming back to a panel that has forgotten
   what you told it is worse than a greyed row. */

export function Switch({ on, onChange, disabled = false }) {
  return (
    <span className={`switch ${disabled ? 'off' : ''}`}>
      <input type="checkbox" checked={on} disabled={disabled}
             onChange={(ev) => onChange(ev.target.checked)} />
      <i className="track"><i className="knob" /></i>
    </span>
  )
}

/* A row whose control is a SWITCH is a `<label>`, so the name and the sentence
   under it are part of the hit area — at a switch's size that is most of what
   anybody actually clicks. A row whose control is a set of CHIPS must not be:
   a `<label>` wrapping buttons adopts the first one as its control, so clicking
   the word "Theme" would quietly press "Transparent". Hence `as`. */
export function SettingRow({ as: Tag = 'label', label, hint, on, className = '', children }) {
  return (
    <Tag className={`settingrow ${on ? 'on' : ''} ${className}`}>
      <span className="t">{label}<small>{hint}</small></span>
      {children}
    </Tag>
  )
}
