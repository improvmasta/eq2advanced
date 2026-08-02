// Theme: dark is the default look; explicit choice persists and outranks the OS.
const KEY = 'eq2advanced-theme'

export function initTheme() {
  const saved = localStorage.getItem(KEY)
  if (saved === 'light' || saved === 'dark') {
    document.documentElement.dataset.theme = saved
  }
}

export function currentTheme() {
  const stamped = document.documentElement.dataset.theme
  if (stamped) return stamped
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark'
  document.documentElement.dataset.theme = next
  localStorage.setItem(KEY, next)
  return next
}
