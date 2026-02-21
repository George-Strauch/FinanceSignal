import { NavLink } from 'react-router-dom'
import { FiHome, FiTrendingUp, FiList, FiActivity } from 'react-icons/fi'
import { useApp } from '../context/AppContext'
import './Sidebar.css'

const NAV_ITEMS = [
  { to: '/', icon: FiHome, label: 'Dashboard' },
  { to: '/tickers', icon: FiTrendingUp, label: 'Tickers' },
  { to: '/subreddits', icon: FiList, label: 'Subreddits' },
  { to: '/scraper', icon: FiActivity, label: 'Scraper Monitor' },
]

export default function Sidebar() {
  const { isMobile, sidebarOpen, setSidebarOpen, sidebarCollapsed } = useApp()

  const isVisible = isMobile ? sidebarOpen : true
  const isCollapsed = !isMobile && sidebarCollapsed

  return (
    <>
      {isMobile && sidebarOpen && (
        <div className="backdrop" onClick={() => setSidebarOpen(false)} />
      )}
      <aside className={`sidebar ${!isVisible ? 'hidden' : ''} ${isCollapsed ? 'collapsed' : ''}`}>
        <nav>
          <ul className="nav-links">
            {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={to === '/'}
                  className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                  onClick={() => isMobile && setSidebarOpen(false)}
                  title={isCollapsed ? label : undefined}
                >
                  <Icon className="nav-icon" />
                  {!isCollapsed && <span>{label}</span>}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
    </>
  )
}
