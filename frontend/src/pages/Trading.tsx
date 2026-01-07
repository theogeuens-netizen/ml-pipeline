import { useState } from 'react'
import {
  PortfolioHeader,
  TimeRangeSelector,
  StrategyTable,
  EquityCurve,
  PositionsTable,
  TimeRange,
} from '../components/trading'
import { useRefreshAnalystData, useStrategyBalances, useLiveTradingSummary } from '../hooks/useAnalystData'
import type { LiveTradingSummary } from '../api/client'

export default function Trading() {
  const [timeRange, setTimeRange] = useState<TimeRange>('30d')
  const [selectedStrategy, setSelectedStrategy] = useState<string | undefined>(undefined)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const refreshData = useRefreshAnalystData()

  // Get strategies with trades for the dropdown
  const { data: balancesData } = useStrategyBalances()

  // Get live trading summary
  const { data: liveData, isLoading: liveLoading } = useLiveTradingSummary()

  // Filter to strategies that have at least one trade
  const strategiesWithTrades = balancesData?.strategies
    ?.filter((s) => s.trade_count > 0)
    ?.sort((a, b) => (b.realized_pnl + b.unrealized_pnl) - (a.realized_pnl + a.unrealized_pnl))
    ?.map((s) => s.name) || []

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

      {/* Live Trading Panel */}
      <LiveTradingPanel data={liveData} isLoading={liveLoading} />

      {/* Strategy Selector for Equity Curve */}
      <div className="flex items-center gap-2">
        <span className="text-gray-400 text-sm">Show P&L for:</span>
        <select
          value={selectedStrategy || ''}
          onChange={(e) => setSelectedStrategy(e.target.value || undefined)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          <option value="">All Strategies (Global)</option>
          {strategiesWithTrades.map((name: string) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
      </div>

      {/* Portfolio Overview KPI Cards */}
      <PortfolioHeader />

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Equity Curve - Takes 2 columns on XL screens */}
        <div className="xl:col-span-2">
          <EquityCurve timeRange={timeRange} strategy={selectedStrategy} />
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

// Live Trading Panel Component
function LiveTradingPanel({
  data,
  isLoading
}: {
  data: LiveTradingSummary | undefined
  isLoading: boolean
}) {
  if (isLoading) {
    return (
      <div className="bg-gradient-to-r from-green-900/30 to-emerald-900/30 rounded-lg p-6 border border-green-700/50">
        <div className="flex items-center gap-3">
          <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse" />
          <h2 className="text-xl font-bold text-white">Live Trading</h2>
        </div>
        <p className="text-gray-400 mt-2">Loading live trading data...</p>
      </div>
    )
  }

  if (!data || !data.success) {
    return (
      <div className="bg-gradient-to-r from-red-900/30 to-orange-900/30 rounded-lg p-6 border border-red-700/50">
        <div className="flex items-center gap-3">
          <div className="w-3 h-3 bg-red-500 rounded-full" />
          <h2 className="text-xl font-bold text-white">Live Trading</h2>
        </div>
        <p className="text-red-400 mt-2">
          {data?.error || 'Unable to connect to live wallet. Check proxy connection.'}
        </p>
      </div>
    )
  }

  const hasActiveStrategies = data.strategies.length > 0

  return (
    <div className="bg-gradient-to-r from-green-900/30 to-emerald-900/30 rounded-lg p-6 border border-green-700/50">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse" />
          <h2 className="text-xl font-bold text-white">Live Trading</h2>
          <span className="px-2 py-0.5 bg-green-600/30 text-green-400 text-xs rounded-full">
            REAL MONEY
          </span>
        </div>
        <div className="text-gray-400 text-sm">
          Wallet: <span className="font-mono text-green-400">{data.wallet.address.slice(0, 6)}...{data.wallet.address.slice(-4)}</span>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Wallet Balance"
          value={`$${data.wallet.balance.toFixed(2)}`}
          subtext="USDC"
        />
        <StatCard
          label="Position Value"
          value={`$${data.wallet.position_value.toFixed(2)}`}
          subtext={`${data.positions.open} open`}
        />
        <StatCard
          label="Total Value"
          value={`$${data.wallet.total_value.toFixed(2)}`}
          subtext="Balance + Positions"
        />
        <StatCard
          label="P&L"
          value={`${data.pnl.total >= 0 ? '+' : ''}$${data.pnl.total.toFixed(2)}`}
          subtext={`${data.trades_count} trades`}
          valueColor={data.pnl.total >= 0 ? 'text-green-400' : 'text-red-400'}
        />
      </div>

      {/* Strategy Stats */}
      {hasActiveStrategies && (
        <div className="mb-6">
          <h3 className="text-white font-semibold mb-3">Live Strategies</h3>
          <div className="space-y-2">
            {data.strategies.map((strategy) => (
              <div
                key={strategy.name}
                className="bg-gray-800/50 rounded-lg p-4 flex items-center justify-between"
              >
                <div>
                  <div className="text-white font-medium">{strategy.name}</div>
                  <div className="text-gray-400 text-sm">
                    {strategy.open_positions} open / {strategy.closed_positions} closed |
                    Win rate: {strategy.win_rate.toFixed(1)}% ({strategy.wins}W/{strategy.losses}L)
                  </div>
                </div>
                <div className="text-right">
                  <div className={`font-bold ${strategy.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {strategy.total_pnl >= 0 ? '+' : ''}${strategy.total_pnl.toFixed(2)}
                  </div>
                  <div className="text-gray-400 text-xs">
                    Realized: ${strategy.realized_pnl.toFixed(2)} | Unrealized: ${strategy.unrealized_pnl.toFixed(2)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Open Positions Table */}
      {data.open_positions.length > 0 && (
        <div>
          <h3 className="text-white font-semibold mb-3">Open Positions ({data.positions.open})</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 text-left border-b border-gray-700">
                  <th className="pb-2 pr-4">Market</th>
                  <th className="pb-2 pr-4">Side</th>
                  <th className="pb-2 pr-4">Entry</th>
                  <th className="pb-2 pr-4">Current</th>
                  <th className="pb-2 pr-4">Size</th>
                  <th className="pb-2 pr-4 text-right">P&L</th>
                </tr>
              </thead>
              <tbody>
                {data.open_positions.map((pos) => (
                  <tr key={pos.id} className="border-b border-gray-800">
                    <td className="py-2 pr-4 text-white max-w-xs truncate">
                      {pos.market_title || 'Unknown Market'}
                    </td>
                    <td className="py-2 pr-4">
                      <span className={`px-2 py-0.5 rounded text-xs ${
                        pos.token_side === 'YES' ? 'bg-green-600/30 text-green-400' : 'bg-red-600/30 text-red-400'
                      }`}>
                        {pos.token_side}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-gray-300 font-mono">
                      {pos.entry_price ? `${(pos.entry_price * 100).toFixed(1)}c` : '-'}
                    </td>
                    <td className="py-2 pr-4 text-gray-300 font-mono">
                      {pos.current_price ? `${(pos.current_price * 100).toFixed(1)}c` : '-'}
                    </td>
                    <td className="py-2 pr-4 text-gray-300 font-mono">
                      ${pos.cost_basis?.toFixed(2) || '0.00'}
                    </td>
                    <td className={`py-2 pr-4 text-right font-mono ${
                      pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                    }`}>
                      {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                      <span className="text-gray-500 text-xs ml-1">
                        ({pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct.toFixed(1)}%)
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!hasActiveStrategies && data.positions.open === 0 && (
        <div className="text-center py-8 text-gray-400">
          <p>No live strategies active and no open positions.</p>
          <p className="text-sm mt-1">Enable a strategy with <code className="bg-gray-800 px-1 rounded">live: true</code> in strategies.yaml to start trading.</p>
        </div>
      )}
    </div>
  )
}

// Stat card for live trading panel
function StatCard({
  label,
  value,
  subtext,
  valueColor = 'text-white',
}: {
  label: string
  value: string
  subtext: string
  valueColor?: string
}) {
  return (
    <div className="bg-gray-800/50 rounded-lg p-4">
      <div className="text-gray-400 text-sm">{label}</div>
      <div className={`text-2xl font-bold ${valueColor} mt-1`}>{value}</div>
      <div className="text-gray-500 text-xs mt-1">{subtext}</div>
    </div>
  )
}
