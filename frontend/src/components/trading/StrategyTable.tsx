import { useState, useMemo } from 'react'
import { clsx } from 'clsx'
import { useLeaderboard, useStrategyBalances } from '../../hooks/useAnalystData'

type SortField = 'name' | 'total_pnl' | 'return_pct' | 'sharpe_ratio' | 'max_drawdown_pct' | 'win_rate' | 'trade_count' | 'unrealized_pnl' | 'realized_pnl'
type SortDirection = 'asc' | 'desc'

interface StrategyRow {
  name: string
  allocated: number
  current: number
  portfolio_value: number
  unrealized_pnl: number
  realized_pnl: number
  total_pnl: number
  return_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  win_rate: number
  trade_count: number
  open_positions: number
  hasAlert: boolean
  alertReason?: string
}

function SortHeader({
  label,
  field,
  currentSort,
  direction,
  onSort
}: {
  label: string
  field: SortField
  currentSort: SortField
  direction: SortDirection
  onSort: (field: SortField) => void
}) {
  return (
    <th
      className="pb-3 px-3 text-left cursor-pointer hover:text-white transition-colors"
      onClick={() => onSort(field)}
    >
      <div className="flex items-center gap-1">
        {label}
        {currentSort === field && (
          <span className="text-indigo-400">
            {direction === 'asc' ? '↑' : '↓'}
          </span>
        )}
      </div>
    </th>
  )
}

function formatCurrency(value: number, showSign = false): string {
  const sign = showSign && value >= 0 ? '+' : ''
  return `${sign}$${Math.abs(value).toFixed(2)}`
}

function formatPercent(value: number, showSign = false): string {
  const sign = showSign && value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

export default function StrategyTable() {
  const { data: leaderboard, isLoading: leaderboardLoading } = useLeaderboard('total_pnl')
  const { data: balances, isLoading: balancesLoading } = useStrategyBalances()

  const [sortField, setSortField] = useState<SortField>('total_pnl')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDirection('desc')
    }
  }

  // Combine leaderboard and balance data
  const strategies: StrategyRow[] = useMemo(() => {
    if (!leaderboard?.strategies || !balances?.strategies) return []

    const balanceMap = new Map(balances.strategies.map((b) => [b.name, b]))

    return leaderboard.strategies.map((s) => {
      const balance = balanceMap.get(s.strategy_name)
      const hasLowSharpe = (s.sharpe_ratio ?? 0) < 0
      const hasHighDrawdown = (s.max_drawdown_pct ?? 0) > 10

      return {
        name: s.strategy_name,
        allocated: balance?.allocated_usd ?? s.allocated_usd ?? 400,
        current: balance?.current_usd ?? s.current_usd ?? 400,
        portfolio_value: balance?.portfolio_value ?? (balance?.current_usd ?? s.current_usd ?? 400),
        unrealized_pnl: s.unrealized_pnl ?? 0,
        realized_pnl: s.realized_pnl ?? 0,
        total_pnl: s.total_pnl ?? 0,
        return_pct: s.total_return_pct ?? 0,
        max_drawdown_pct: s.max_drawdown_pct ?? 0,
        sharpe_ratio: s.sharpe_ratio ?? 0,
        win_rate: (s.win_rate ?? 0) * 100,
        trade_count: s.trade_count ?? 0,
        open_positions: s.open_positions ?? 0,
        hasAlert: hasLowSharpe || hasHighDrawdown,
        alertReason: hasLowSharpe
          ? 'Sharpe < 0'
          : hasHighDrawdown
            ? 'Drawdown > 10%'
            : undefined,
      }
    })
  }, [leaderboard, balances])

  // Sort strategies
  const sortedStrategies = useMemo(() => {
    return [...strategies].sort((a, b) => {
      let aVal = a[sortField]
      let bVal = b[sortField]

      if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase()
        bVal = (bVal as string).toLowerCase()
      }

      if (sortDirection === 'asc') {
        return aVal < bVal ? -1 : aVal > bVal ? 1 : 0
      } else {
        return aVal > bVal ? -1 : aVal < bVal ? 1 : 0
      }
    })
  }, [strategies, sortField, sortDirection])

  if (leaderboardLoading || balancesLoading) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <div className="animate-pulse">
          <div className="h-6 bg-gray-700 rounded w-48 mb-4" />
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-10 bg-gray-700 rounded" />
            ))}
          </div>
        </div>
      </div>
    )
  }

  if (strategies.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-8 border border-gray-700 text-center">
        <p className="text-gray-400">No strategy data available</p>
      </div>
    )
  }

  const alertCount = strategies.filter(s => s.hasAlert).length

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      <div className="p-4 border-b border-gray-700 flex items-center justify-between">
        <h3 className="text-white font-semibold">Strategy Performance</h3>
        {alertCount > 0 && (
          <span className="px-2 py-1 bg-red-900/50 text-red-400 text-xs font-medium rounded">
            {alertCount} alert{alertCount > 1 ? 's' : ''}
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="text-gray-400 text-xs border-b border-gray-700">
              <SortHeader label="Strategy" field="name" currentSort={sortField} direction={sortDirection} onSort={handleSort} />
              <th className="pb-3 px-3 text-right">Allocated</th>
              <th className="pb-3 px-3 text-right">Current</th>
              <th className="pb-3 px-3 text-right" title="Cash + Open Positions Value">Total Value</th>
              <SortHeader label="Unrealized" field="unrealized_pnl" currentSort={sortField} direction={sortDirection} onSort={handleSort} />
              <SortHeader label="Realized" field="realized_pnl" currentSort={sortField} direction={sortDirection} onSort={handleSort} />
              <SortHeader label="Total P&L" field="total_pnl" currentSort={sortField} direction={sortDirection} onSort={handleSort} />
              <SortHeader label="Return" field="return_pct" currentSort={sortField} direction={sortDirection} onSort={handleSort} />
              <SortHeader label="Drawdown" field="max_drawdown_pct" currentSort={sortField} direction={sortDirection} onSort={handleSort} />
              <SortHeader label="Sharpe" field="sharpe_ratio" currentSort={sortField} direction={sortDirection} onSort={handleSort} />
              <SortHeader label="Win Rate" field="win_rate" currentSort={sortField} direction={sortDirection} onSort={handleSort} />
              <SortHeader label="Trades" field="trade_count" currentSort={sortField} direction={sortDirection} onSort={handleSort} />
              <th className="pb-3 px-3 text-right">Open</th>
            </tr>
          </thead>
          <tbody className="text-sm">
            {sortedStrategies.map((strategy) => (
              <>
                <tr
                  key={strategy.name}
                  onClick={() => setExpandedRow(expandedRow === strategy.name ? null : strategy.name)}
                  className={clsx(
                    'border-b border-gray-700/50 cursor-pointer transition-colors',
                    expandedRow === strategy.name ? 'bg-gray-700/50' : 'hover:bg-gray-700/30',
                    strategy.hasAlert && 'bg-red-900/10'
                  )}
                >
                  <td className="py-3 px-3">
                    <div className="flex items-center gap-2">
                      <span className="text-gray-300 font-mono text-xs">{strategy.name}</span>
                      {strategy.hasAlert && (
                        <span className="px-1.5 py-0.5 bg-red-900/50 text-red-400 text-xs rounded" title={strategy.alertReason}>
                          ⚠
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 px-3 text-right text-gray-400 font-mono">
                    ${strategy.allocated.toFixed(0)}
                  </td>
                  <td className="py-3 px-3 text-right text-white font-mono">
                    ${strategy.current.toFixed(0)}
                  </td>
                  <td className="py-3 px-3 text-right text-indigo-300 font-mono font-medium">
                    ${strategy.portfolio_value.toFixed(0)}
                  </td>
                  <td className={clsx(
                    'py-3 px-3 text-right font-mono',
                    strategy.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                  )}>
                    {formatCurrency(strategy.unrealized_pnl, true)}
                  </td>
                  <td className={clsx(
                    'py-3 px-3 text-right font-mono',
                    strategy.realized_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                  )}>
                    {formatCurrency(strategy.realized_pnl, true)}
                  </td>
                  <td className={clsx(
                    'py-3 px-3 text-right font-mono font-medium',
                    strategy.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                  )}>
                    {formatCurrency(strategy.total_pnl, true)}
                  </td>
                  <td className={clsx(
                    'py-3 px-3 text-right font-mono',
                    strategy.return_pct >= 0 ? 'text-green-400' : 'text-red-400'
                  )}>
                    {formatPercent(strategy.return_pct, true)}
                  </td>
                  <td className={clsx(
                    'py-3 px-3 text-right font-mono',
                    strategy.max_drawdown_pct > 10 ? 'text-red-400' :
                    strategy.max_drawdown_pct > 5 ? 'text-yellow-400' : 'text-gray-400'
                  )}>
                    -{strategy.max_drawdown_pct.toFixed(1)}%
                  </td>
                  <td className={clsx(
                    'py-3 px-3 text-right font-mono',
                    strategy.sharpe_ratio > 1 ? 'text-green-400' :
                    strategy.sharpe_ratio > 0 ? 'text-gray-300' : 'text-red-400'
                  )}>
                    {strategy.sharpe_ratio.toFixed(2)}
                  </td>
                  <td className={clsx(
                    'py-3 px-3 text-right font-mono',
                    strategy.win_rate > 55 ? 'text-green-400' :
                    strategy.win_rate > 45 ? 'text-gray-300' : 'text-red-400'
                  )}>
                    {strategy.win_rate.toFixed(0)}%
                  </td>
                  <td className="py-3 px-3 text-right text-gray-400 font-mono">
                    {strategy.trade_count}
                  </td>
                  <td className="py-3 px-3 text-right text-gray-300 font-mono">
                    {strategy.open_positions}
                  </td>
                </tr>
                {expandedRow === strategy.name && (
                  <tr key={`${strategy.name}-expanded`} className="bg-gray-900/50">
                    <td colSpan={13} className="py-4 px-6">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                        <div>
                          <p className="text-gray-500 text-xs mb-1">Alert Status</p>
                          <p className={strategy.hasAlert ? 'text-red-400' : 'text-green-400'}>
                            {strategy.hasAlert ? `⚠ ${strategy.alertReason}` : '✓ No alerts'}
                          </p>
                        </div>
                        <div>
                          <p className="text-gray-500 text-xs mb-1">Capital Efficiency</p>
                          <p className="text-white">
                            {((strategy.current / strategy.allocated) * 100).toFixed(0)}% of allocation
                          </p>
                        </div>
                        <div>
                          <p className="text-gray-500 text-xs mb-1">Risk-Adjusted Return</p>
                          <p className="text-white">
                            {strategy.sharpe_ratio > 0
                              ? `${(strategy.return_pct / strategy.max_drawdown_pct).toFixed(2)}x return/drawdown`
                              : 'N/A'}
                          </p>
                        </div>
                        <div>
                          <p className="text-gray-500 text-xs mb-1">Performance</p>
                          <p className={clsx(
                            strategy.sharpe_ratio > 1 && strategy.win_rate > 50 ? 'text-green-400' :
                            strategy.sharpe_ratio > 0 ? 'text-yellow-400' : 'text-red-400'
                          )}>
                            {strategy.sharpe_ratio > 1 && strategy.win_rate > 50 ? '🟢 Strong' :
                             strategy.sharpe_ratio > 0 ? '🟡 Moderate' : '🔴 Weak'}
                          </p>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
