import { useState, useMemo } from 'react'
import { clsx } from 'clsx'
import { formatDistanceToNow } from 'date-fns'
import { usePositions, useClosePosition } from '../../hooks/useExecutorData'
import { useLeaderboard } from '../../hooks/useAnalystData'
import { api } from '../../api/client'

type SortField = 'strategy' | 'market' | 'pnl' | 'age' | 'size'
type SortDirection = 'asc' | 'desc'

export default function PositionsTable() {
  const { data: positionsData, isLoading } = usePositions({ status: 'open', limit: 500 })
  const { data: leaderboard } = useLeaderboard('total_pnl')
  const closeMutation = useClosePosition()

  const [strategyFilter, setStrategyFilter] = useState<string>('all')
  const [sortField, setSortField] = useState<SortField>('pnl')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')

  // Get unique strategy names
  const strategies = useMemo(() => {
    if (!leaderboard?.strategies) return []
    return leaderboard.strategies.map((s) => s.strategy_name).sort()
  }, [leaderboard])

  const positions = positionsData?.items || []

  // Filter and sort positions
  const filteredPositions = useMemo(() => {
    let filtered = positions

    if (strategyFilter !== 'all') {
      filtered = filtered.filter((p: any) => p.strategy_name === strategyFilter)
    }

    return [...filtered].sort((a: any, b: any) => {
      let aVal: any, bVal: any

      switch (sortField) {
        case 'strategy':
          aVal = a.strategy_name || ''
          bVal = b.strategy_name || ''
          break
        case 'market':
          aVal = a.market_title || ''
          bVal = b.market_title || ''
          break
        case 'pnl':
          aVal = a.unrealized_pnl ?? 0
          bVal = b.unrealized_pnl ?? 0
          break
        case 'age':
          aVal = new Date(a.entry_time || 0).getTime()
          bVal = new Date(b.entry_time || 0).getTime()
          break
        case 'size':
          aVal = a.cost_basis ?? 0
          bVal = b.cost_basis ?? 0
          break
      }

      if (sortDirection === 'asc') {
        return aVal < bVal ? -1 : aVal > bVal ? 1 : 0
      } else {
        return aVal > bVal ? -1 : aVal < bVal ? 1 : 0
      }
    })
  }, [positions, strategyFilter, sortField, sortDirection])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDirection('desc')
    }
  }

  const handleExportCSV = () => {
    window.open(api.getPositionsExportUrl({ strategy: strategyFilter !== 'all' ? strategyFilter : undefined }), '_blank')
  }

  // Calculate totals
  const totals = useMemo(() => {
    return filteredPositions.reduce((acc: any, p: any) => ({
      costBasis: acc.costBasis + (p.cost_basis ?? 0),
      unrealizedPnl: acc.unrealizedPnl + (p.unrealized_pnl ?? 0),
    }), { costBasis: 0, unrealizedPnl: 0 })
  }, [filteredPositions])

  if (isLoading) {
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

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      <div className="p-4 border-b border-gray-700">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h3 className="text-white font-semibold">Open Positions</h3>
            <span className="text-gray-400 text-sm">
              {filteredPositions.length} position{filteredPositions.length !== 1 ? 's' : ''}
            </span>
          </div>

          <div className="flex items-center gap-3">
            {/* Strategy filter */}
            <select
              value={strategyFilter}
              onChange={(e) => setStrategyFilter(e.target.value)}
              className="bg-gray-700 text-white text-sm rounded-lg px-3 py-1.5 border border-gray-600 focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            >
              <option value="all">All Strategies</option>
              {strategies.map((s: string) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>

            {/* CSV Export */}
            <button
              onClick={handleExportCSV}
              className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg border border-gray-600 transition-colors"
            >
              Export CSV
            </button>
          </div>
        </div>
      </div>

      {filteredPositions.length === 0 ? (
        <div className="p-8 text-center">
          <p className="text-gray-500">
            {strategyFilter !== 'all'
              ? `No open positions for ${strategyFilter}`
              : 'No open positions'
            }
          </p>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-gray-400 text-xs border-b border-gray-700">
                  <th
                    className="py-3 px-3 text-left cursor-pointer hover:text-white"
                    onClick={() => handleSort('strategy')}
                  >
                    Strategy {sortField === 'strategy' && (sortDirection === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    className="py-3 px-3 text-left cursor-pointer hover:text-white"
                    onClick={() => handleSort('market')}
                  >
                    Market {sortField === 'market' && (sortDirection === 'asc' ? '↑' : '↓')}
                  </th>
                  <th className="py-3 px-3 text-left">Side</th>
                  <th className="py-3 px-3 text-left">Opened</th>
                  <th className="py-3 px-3 text-right">Entry</th>
                  <th className="py-3 px-3 text-right">Current</th>
                  <th
                    className="py-3 px-3 text-right cursor-pointer hover:text-white"
                    onClick={() => handleSort('size')}
                  >
                    Size {sortField === 'size' && (sortDirection === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    className="py-3 px-3 text-right cursor-pointer hover:text-white"
                    onClick={() => handleSort('pnl')}
                  >
                    P&L {sortField === 'pnl' && (sortDirection === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    className="py-3 px-3 text-right cursor-pointer hover:text-white"
                    onClick={() => handleSort('age')}
                  >
                    Age {sortField === 'age' && (sortDirection === 'asc' ? '↑' : '↓')}
                  </th>
                  <th className="py-3 px-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="text-sm">
                {filteredPositions.map((pos: any) => {
                  const opened = pos.entry_time ? new Date(pos.entry_time) : null
                  const pnlPct = pos.cost_basis > 0
                    ? ((pos.unrealized_pnl ?? 0) / pos.cost_basis) * 100
                    : 0

                  return (
                    <tr
                      key={pos.id}
                      className="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors"
                    >
                      <td className="py-3 px-3">
                        <span className="text-gray-300 font-mono text-xs">
                          {pos.strategy_name}
                        </span>
                      </td>
                      <td className="py-3 px-3">
                        <span
                          className="text-gray-300 text-xs max-w-[200px] truncate block"
                          title={pos.market_title}
                        >
                          {pos.market_title?.substring(0, 40) || pos.market_id}...
                        </span>
                      </td>
                      <td className="py-3 px-3">
                        <span className={clsx(
                          'px-2 py-0.5 rounded text-xs font-medium',
                          pos.token_side === 'YES'
                            ? 'bg-green-900/50 text-green-400'
                            : 'bg-red-900/50 text-red-400'
                        )}>
                          {pos.token_side || 'BUY'}
                        </span>
                      </td>
                      <td className="py-3 px-3 text-gray-300 text-xs font-mono">
                        {opened ? opened.toLocaleDateString() : '-'}
                      </td>
                      <td className="py-3 px-3 text-right text-gray-400 font-mono">
                        {((pos.entry_price ?? 0) * 100).toFixed(1)}¢
                      </td>
                      <td className="py-3 px-3 text-right text-white font-mono">
                        {((pos.current_price ?? 0) * 100).toFixed(1)}¢
                      </td>
                      <td className="py-3 px-3 text-right text-gray-300 font-mono">
                        ${(pos.cost_basis ?? 0).toFixed(2)}
                      </td>
                      <td className={clsx(
                        'py-3 px-3 text-right font-mono',
                        (pos.unrealized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                      )}>
                        <div>
                          {(pos.unrealized_pnl ?? 0) >= 0 ? '+' : ''}${(pos.unrealized_pnl ?? 0).toFixed(2)}
                        </div>
                        <div className="text-xs opacity-75">
                          ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
                        </div>
                      </td>
                      <td className="py-3 px-3 text-right text-gray-400 text-xs">
                        {opened
                          ? formatDistanceToNow(opened, { addSuffix: false })
                          : '-'}
                      </td>
                      <td className="py-3 px-3 text-right">
                        <button
                          onClick={() => closeMutation.mutate({
                            positionId: pos.id,
                            exitPrice: pos.current_price
                          })}
                          disabled={closeMutation.isPending}
                          className="px-2 py-1 bg-red-600 hover:bg-red-700 rounded text-xs text-white disabled:opacity-50 transition-colors"
                        >
                          Close
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Totals row */}
          <div className="p-4 border-t border-gray-700 bg-gray-900/50">
            <div className="flex items-center justify-between">
              <span className="text-gray-400 text-sm">Total</span>
              <div className="flex items-center gap-6 text-sm font-mono">
                <span className="text-gray-300">
                  Size: ${totals.costBasis.toFixed(2)}
                </span>
                <span className={totals.unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                  P&L: {totals.unrealizedPnl >= 0 ? '+' : ''}${totals.unrealizedPnl.toFixed(2)}
                </span>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
