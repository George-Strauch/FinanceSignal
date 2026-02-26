import { NavLink } from 'react-router-dom'
import { FiBarChart2, FiTrendingUp, FiDatabase, FiTag, FiActivity, FiServer, FiUsers, FiBriefcase, FiCpu } from 'react-icons/fi'
import { useApp } from '../context/AppContext'
import './Sidebar.css'

const NAV_ITEMS = [
  { to: '/', icon: FiBarChart2, label: 'Trending' },
  { to: '/tickers', icon: FiTrendingUp, label: 'Tickers' },
  { to: '/entities', icon: FiUsers, label: 'Entities' },
  { to: '/trading', icon: FiBriefcase, label: 'Paper Trading' },
  { to: '/trading/bots', icon: FiCpu, label: 'Trading Bots' },
  { to: '/sources', icon: FiDatabase, label: 'Sources' },
  { to: '/ticker-tags', icon: FiTag, label: 'Ticker Tags' },
  { to: '/processes', icon: FiActivity, label: 'Process Monitor' },
  { to: '/system', icon: FiServer, label: 'System Status' },
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
