import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

/* One URL query param as state. Writes replace history by default so header
   clicks and tree navigation don't bury the back button. */
export function useQueryState(key, fallback = null) {
  const [params, setParams] = useSearchParams()
  const value = params.get(key) ?? fallback
  const set = useCallback((v, { replace = true } = {}) => {
    // Base on the live URL, not the render snapshot: two setters called in the
    // same handler (setSel + setActorQ) would otherwise clobber each other,
    // because react-router's functional updater sees pre-navigation params.
    const p = new URLSearchParams(window.location.search)
    if (v == null || v === '') p.delete(key)
    else p.set(key, String(v))
    setParams(p, { replace })
  }, [key, setParams])
  return [value, set]
}
