import { FiMenu, FiSun, FiMoon, FiSidebar } from 'react-icons/fi'
import { useApp } from '../context/AppContext'
import './TopBar.css'

export default function TopBar() {
  const { isDark, toggleDarkMode, isMobile, toggleSidebar } = useApp()

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button className="topbar-btn" onClick={toggleSidebar} aria-label="Toggle sidebar">
          {isMobile ? <FiMenu /> : <FiSidebar />}
        </button>
        <span className="topbar-title">FinanceSignal</span>
      </div>
      <div className="topbar-right">
        <button className="topbar-btn" onClick={toggleDarkMode} aria-label="Toggle theme">
          {isDark ? <FiSun /> : <FiMoon />}
        </button>
      </div>
    </header>
  )
}
