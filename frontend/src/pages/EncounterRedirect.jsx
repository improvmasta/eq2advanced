import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api.js'

/* Deep link: /encounters/:id -> the zone-run page with that encounter
   selected. A dup-marked encounter redirects through its canonical copy;
   an encounter with no run yet (open live session) falls back to the
   per-file workspace. */
export default function EncounterRedirect() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [error, setError] = useState(null)

  useEffect(() => {
    api.encounter(id)
      .then(async (d) => {
        let enc = d.encounter
        if (enc.dup_of) enc = (await api.encounter(enc.dup_of)).encounter
        if (enc.zone_run_id) {
          navigate(`/zones/${enc.zone_run_id}?sel=${enc.id}`, { replace: true })
        } else {
          navigate(`/sessions/${enc.session_id}?sel=${enc.id}`, { replace: true })
        }
      })
      .catch((e) => setError(e.message))
  }, [id, navigate])

  return error ? <p className="err">{error}</p> : <p className="muted">Loading…</p>
}
