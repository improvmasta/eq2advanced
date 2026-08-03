import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

/* One URL query param as state. Writes replace history by default so header
   clicks and tree navigation don't bury the back button. */
export function useQueryState(key, fallback = null) {
  const [params, setParams] = useSearchParams()
  const value = params.get(key) ?? fallback
  const set = useCallback((v, { replace = true } = {}) => {
    setParams((prev) => {
      const p = new URLSearchParams(prev)
      if (v == null || v === '') p.delete(key)
      else p.set(key, String(v))
      return p
    }, { replace })
  }, [key, setParams])
  return [value, set]
}
