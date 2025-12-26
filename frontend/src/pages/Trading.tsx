import { useState } from 'react'
import {
  PortfolioHeader,
  TimeRangeSelector,
  StrategyTable,
  EquityCurve,
  PositionsTable,
  TimeRange,
} from '../components/trading'
import { useRefreshAnalystData } from '../hooks/useAnalystData'

export default function Trading() {
  const [timeRange, setTimeRange] = useState<TimeRange>('30d')
  const [isRefreshing, setIsRefreshing] = useState(false)
  const refreshData = useRefreshAnalystData()

  const handleRefresh = () => {
    setIsRefreshing(true)
    refreshData()
    setTimeout(() => setIsRefreshing(false), 1000)
  }

  return (
    <div className="space-y-6 pb-8">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Trading Dashboard</h1>
          <p className="text-gray-400 text-sm mt-1">Portfolio and strategy performance</p>
        </div>
        <div className="flex items-center gap-3">
          <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
          <button
            onClick={handleRefresh}
            disabled={isRefreshing}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 rounded-lg text-sm font-medium text-white transition-colors disabled:opacity-50"
          >
            {isRefreshing ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Portfolio Overview KPI Cards */}
      <PortfolioHeader />

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Equity Curve - Takes 2 columns on XL screens */}
        <div className="xl:col-span-2">
          <EquityCurve timeRange={timeRange} />
        </div>

        {/* Quick Stats Card */}
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
          <h3 className="text-white font-semibold mb-4">Quick Stats</h3>
          <div className="space-y-4">
            <QuickStatRow
              label="Auto-refresh"
              value="Every 30s"
              status="active"
            />
            <QuickStatRow
              label="Data period"
              value={timeRange.toUpperCase()}
            />
            <QuickStatRow
              label="Last updated"
              value={new Date().toLocaleTimeString()}
            />
            <div className="pt-4 border-t border-gray-700">
              <p className="text-gray-400 text-xs">
                Sharpe &lt; 0 or Drawdown &gt; 10% triggers performance alerts
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Strategy Performance Table */}
      <StrategyTable />

      {/* Positions (Open + Historical tabs) */}
      <PositionsTable />

      {/* Footer info */}
      <div className="text-center text-gray-500 text-xs">
        Data refreshes automatically every 30 seconds
      </div>
    </div>
  )
}

// Quick stat row component
function QuickStatRow({
  label,
  value,
  status,
}: {
  label: string
  value: string
  status?: 'active' | 'inactive'
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-gray-400 text-sm">{label}</span>
      <div className="flex items-center gap-2">
        {status === 'active' && (
          <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
        )}
        <span className="text-white text-sm font-mono">{value}</span>
      </div>
    </div>
  )
}
