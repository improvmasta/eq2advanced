import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api.js'

/* Legacy deep link: /encounters/:id -> the session workspace with that
   encounter selected. */
export default function EncounterRedirect() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [error, setError] = useState(null)

  useEffect(() => {
    api.encounter(id)
      .then((d) => navigate(`/sessions/${d.encounter.session_id}?sel=${id}`, { replace: true }))
      .catch((e) => setError(e.message))
  }, [id, navigate])

  return error ? <p className="err">{error}</p> : <p className="muted">Loading…</p>
}
