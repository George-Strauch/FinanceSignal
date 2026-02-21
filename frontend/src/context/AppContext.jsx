import { createContext, useContext, useCallback, useEffect, useState } from 'react'
import usePersistedState from '../hooks/usePersistedState'

const AppContext = createContext(null)

const THEME = {
  light: {
    '--primary-color': '245, 245, 245',
    '--secondary-color': '255, 255, 255',
    '--tertiary-color': '230, 230, 235',
    '--soft-text': '75, 75, 75',
    '--hard-text': '25, 25, 25',
    '--accent': '59, 130, 246',
    '--soft-border': '200, 200, 205',
    '--soft-border-alpha': '0.3',
    '--color-success': '34, 197, 94',
    '--color-error': '239, 68, 68',
    '--color-warning': '234, 179, 8',
    '--color-info': '99, 102, 241',
  },
  dark: {
    '--primary-color': '24, 24, 27',
    '--secondary-color': '39, 39, 42',
    '--tertiary-color': '52, 52, 56',
    '--soft-text': '161, 161, 170',
    '--hard-text': '228, 228, 231',
    '--accent': '96, 165, 250',
    '--soft-border': '63, 63, 70',
    '--soft-border-alpha': '0.5',
    '--color-success': '34, 197, 94',
    '--color-error': '248, 113, 113',
    '--color-warning': '250, 204, 21',
    '--color-info': '129, 140, 248',
  },
}

function applyTheme(isDark) {
  const root = document.documentElement
  const vars = isDark ? THEME.dark : THEME.light
  for (const [k, v] of Object.entries(vars)) {
    root.style.setProperty(k, v)
  }
  root.setAttribute('data-theme', isDark ? 'dark' : 'light')
  root.style.setProperty('color-scheme', isDark ? 'dark' : 'light')
}

function getIsMobile() {
  return window.innerWidth < 768
}

export function AppProvider({ children }) {
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches
  const [isDark, setIsDark] = usePersistedState('ui:darkMode', prefersDark)
  const [isMobile, setIsMobile] = useState(getIsMobile)
  const [sidebarOpen, setSidebarOpen] = usePersistedState(
    `ui:sidebarOpen:${isMobile ? 'mobile' : 'desktop'}`,
    !isMobile
  )
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedState('ui:sidebarCollapsed', false)

  const toggleDarkMode = useCallback(() => setIsDark(prev => !prev), [setIsDark])
  const toggleSidebar = useCallback(() => {
    if (isMobile) {
      setSidebarOpen(prev => !prev)
    } else {
      setSidebarCollapsed(prev => !prev)
    }
  }, [isMobile, setSidebarOpen, setSidebarCollapsed])

  // Apply theme on change
  useEffect(() => {
    applyTheme(isDark)
  }, [isDark])

  // Handle resize
  useEffect(() => {
    const onResize = () => {
      const mobile = getIsMobile()
      setIsMobile(mobile)
      if (mobile) {
        setSidebarOpen(false)
      }
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [setSidebarOpen])

  // Escape closes sidebar on mobile
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && isMobile && sidebarOpen) {
        setSidebarOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isMobile, sidebarOpen, setSidebarOpen])

  return (
    <AppContext.Provider value={{
      isDark, toggleDarkMode,
      isMobile,
      sidebarOpen, setSidebarOpen,
      sidebarCollapsed, toggleSidebar,
    }}>
      {children}
    </AppContext.Provider>
  )
}

export function useApp() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used within AppProvider')
  return ctx
}
