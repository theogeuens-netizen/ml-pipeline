import { Routes, Route, Link, useLocation } from 'react-router-dom'
import { clsx } from 'clsx'
import Dashboard from './pages/Dashboard'
import Markets from './pages/Markets'
import MarketDetail from './pages/MarketDetail'
import DataQuality from './pages/DataQuality'
import Tasks from './pages/Tasks'
import Monitoring from './pages/Monitoring'
import Database from './pages/Database'
import Trading from './pages/Trading'

const navLinks = [
  { path: '/', label: 'Dashboard' },
  { path: '/trading', label: 'Trading' },
  { path: '/markets', label: 'Markets' },
  { path: '/data-quality', label: 'Data Quality' },
  { path: '/tasks', label: 'Tasks' },
  { path: '/monitoring', label: 'Monitoring' },
  { path: '/database', label: 'Database' },
]

export default function App() {
  const location = useLocation()

  return (
    <div className="min-h-screen">
      {/* Navigation */}
      <nav className="bg-gray-800 border-b border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center">
              <span className="text-xl font-bold text-indigo-400">
                Polymarket ML
              </span>
              <div className="ml-10 flex items-baseline space-x-4">
                {navLinks.map((link) => (
                  <Link
                    key={link.path}
                    to={link.path}
                    className={clsx(
                      'px-3 py-2 rounded-md text-sm font-medium',
                      location.pathname === link.path
                        ? 'bg-gray-900 text-white'
                        : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                    )}
                  >
                    {link.label}
                  </Link>
                ))}
              </div>
            </div>
            <div className="flex items-center">
              <span className="text-sm text-gray-400">Data Collector</span>
            </div>
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/trading" element={<Trading />} />
          <Route path="/markets" element={<Markets />} />
          <Route path="/markets/:id" element={<MarketDetail />} />
          <Route path="/data-quality" element={<DataQuality />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/monitoring" element={<Monitoring />} />
          <Route path="/database" element={<Database />} />
        </Routes>
      </main>
    </div>
  )
}
