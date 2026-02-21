import { Outlet } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import TopBar from './TopBar'
import Sidebar from './Sidebar'
import './Layout.css'

export default function Layout() {
  const { isMobile, sidebarCollapsed } = useApp()

  const contentClass = [
    'page-content',
    isMobile ? 'mobile' : 'desktop',
    !isMobile && sidebarCollapsed ? 'sidebar-collapsed' : '',
  ].filter(Boolean).join(' ')

  return (
    <div className="app-shell">
      <TopBar />
      <Sidebar />
      <main className={contentClass}>
        <Outlet />
      </main>
    </div>
  )
}
