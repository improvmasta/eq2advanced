// Theme: everyone starts on dark. The OS preference is ignored on purpose —
// only the toggle switches it, and that choice persists.
const KEY = 'eq2advanced-theme'

export function initTheme() {
  const saved = localStorage.getItem(KEY)
  if (saved === 'light' || saved === 'dark') {
    document.documentElement.dataset.theme = saved
  }
}

export function currentTheme() {
  return document.documentElement.dataset.theme || 'dark'
}

export function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark'
  document.documentElement.dataset.theme = next
  localStorage.setItem(KEY, next)
  return next
}
