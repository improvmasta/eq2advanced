import { createContext, useContext } from 'react'

/* Who is reading the page, for components too deep to be handed it.

   Pages take `user` as a prop and should keep doing so — it is explicit and it
   is where the auth decisions live. This exists for the one thing props are
   bad at: `BreakdownTable` renders inside the raid drilldown, the compare
   panel and the Compare page, none of which own the user, and threading it
   through three unrelated call sites to decide whether ONE convenience button
   appears is worse than a context.

   It carries no authority. Everything it gates is a shortcut to a page that
   does its own check server-side (`security.require_curator`), so a stale or
   spoofed value here shows a link that 403s — never data. */
export const SessionContext = createContext(null)

export function useSession() {
  return useContext(SessionContext)
}

/* Can this reader fix an ability's label? Admin implies curator, the same way
   the backend has it. */
export function useCanCurate() {
  const user = useSession()
  return user?.role === 'admin' || user?.role === 'curator'
}
