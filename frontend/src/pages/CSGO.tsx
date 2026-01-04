import { useState, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, CSGOPipelineEvent, CSGOEngineSpread, CSGOEngineTrade, CSGOEngineStrategyState, CSGOEnginePosition } from '../api/client'
import { clsx } from 'clsx'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceDot,
  ReferenceLine,
} from 'recharts'

type TabType = 'pipeline' | 'positions' | 'chart' | 'engine'

export default function CSGO() {
  const [activeTab, setActiveTab] = useState<TabType>('engine')
  const [pipelineUpcomingOnly, setPipelineUpcomingOnly] = useState(true)
  const [engineStrategyFilter, setEngineStrategyFilter] = useState<string>('')
  const [engineStatusFilter, setEngineStatusFilter] = useState<string>('open')

  // Fetch CSGO engine positions (from dedicated CSGO engine)
  const [enginePosStatusFilter, setEnginePosStatusFilter] = useState<string>('open')
  const [enginePosStrategyFilter, setEnginePosStrategyFilter] = useState<string>('')
  const { data: enginePositions, isLoading: enginePosLoading } = useQuery({
    queryKey: ['csgo-engine-positions', enginePosStatusFilter, enginePosStrategyFilter],
    queryFn: () => api.getCSGOEnginePositions(enginePosStatusFilter || undefined, enginePosStrategyFilter || undefined, 100),
    refetchInterval: 3000,
  })

  // Fetch pipeline events (real-time view)
  const { data: pipelineData, isLoading: pipelineLoading } = useQuery({
    queryKey: ['csgo-pipeline-events', pipelineUpcomingOnly],
    queryFn: () => api.getCSGOPipelineEvents({
      upcoming_only: pipelineUpcomingOnly,
      hours_ahead: 24,
      limit: 50,
    }),
    refetchInterval: 5000,
  })

  // CSGO Engine queries (new real-time trading system)
  const { data: engineHealth } = useQuery({
    queryKey: ['csgo-engine-health'],
    queryFn: () => api.getCSGOEngineHealth(),
    refetchInterval: 5000,
  })

  const { data: engineStats, isLoading: engineStatsLoading } = useQuery({
    queryKey: ['csgo-engine-stats'],
    queryFn: () => api.getCSGOEngineStats(),
    refetchInterval: 3000,
  })

  const { data: engineStrategies, isLoading: engineStrategiesLoading } = useQuery({
    queryKey: ['csgo-engine-strategies'],
    queryFn: () => api.getCSGOEngineStrategies(),
    refetchInterval: 5000,
  })

  const { data: engineSpreads, isLoading: engineSpreadsLoading } = useQuery({
    queryKey: ['csgo-engine-spreads', engineStatusFilter, engineStrategyFilter],
    queryFn: () => api.getCSGOEngineSpreads(engineStatusFilter || undefined, engineStrategyFilter || undefined, 100),
    refetchInterval: 3000,
  })

  const { data: engineTrades, isLoading: engineTradesLoading } = useQuery({
    queryKey: ['csgo-engine-trades'],
    queryFn: () => api.getCSGOEngineTrades(undefined, 50),
    refetchInterval: 3000,
  })

  const { data: engineStream } = useQuery({
    queryKey: ['csgo-engine-stream'],
    queryFn: () => api.getCSGOEngineStream(),
    refetchInterval: 3000,
  })

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">CS:GO Volatility Strategy</h1>
          <p className="text-gray-400 text-sm mt-1">
            Hedge-based volatility trading on esports markets
          </p>
        </div>
        <RefreshButton />
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-700">
        <div className="flex gap-6">
          <TabButton
            active={activeTab === 'engine'}
            onClick={() => setActiveTab('engine')}
            label="Engine"
            count={engineStats?.positions_open}
          />
          <TabButton
            active={activeTab === 'positions'}
            onClick={() => setActiveTab('positions')}
            label="Positions"
            count={enginePositions?.length}
          />
          <TabButton
            active={activeTab === 'pipeline'}
            onClick={() => setActiveTab('pipeline')}
            label="Pipeline"
            count={pipelineData?.total}
          />
          <TabButton
            active={activeTab === 'chart'}
            onClick={() => setActiveTab('chart')}
            label="Price Chart"
          />
        </div>
      </div>

      {/* Tab Content */}
      {activeTab === 'engine' && (
        <EnginePanel
          health={engineHealth}
          stats={engineStats}
          statsLoading={engineStatsLoading}
          strategies={engineStrategies ?? []}
          strategiesLoading={engineStrategiesLoading}
          spreads={engineSpreads ?? []}
          spreadsLoading={engineSpreadsLoading}
          trades={engineTrades ?? []}
          tradesLoading={engineTradesLoading}
          streamLength={engineStream?.length ?? 0}
          statusFilter={engineStatusFilter}
          strategyFilter={engineStrategyFilter}
          onStatusFilterChange={setEngineStatusFilter}
          onStrategyFilterChange={setEngineStrategyFilter}
        />
      )}

      {activeTab === 'positions' && (
        <EnginePositionsPanel
          positions={enginePositions ?? []}
          loading={enginePosLoading}
          statusFilter={enginePosStatusFilter}
          onStatusFilterChange={setEnginePosStatusFilter}
          strategyFilter={enginePosStrategyFilter}
          onStrategyFilterChange={setEnginePosStrategyFilter}
        />
      )}

      {activeTab === 'pipeline' && (
        <EventsPanel
          events={pipelineData?.events ?? []}
          loading={pipelineLoading}
          upcomingOnly={pipelineUpcomingOnly}
          onUpcomingOnlyChange={setPipelineUpcomingOnly}
        />
      )}

      {activeTab === 'chart' && (
        <PriceChartPanel />
      )}
    </div>
  )
}

// KPI Card Component
function KPICard({
  label,
  value,
  format,
  color = 'white',
  loading,
  subtitle,
}: {
  label: string
  value: number
  format: 'currency' | 'percent' | 'number'
  color?: 'green' | 'red' | 'yellow' | 'blue' | 'purple' | 'white'
  loading?: boolean
  subtitle?: string
}) {
  const formatValue = () => {
    if (loading) return '...'
    if (format === 'currency') return `$${value.toFixed(2)}`
    if (format === 'percent') return `${value.toFixed(1)}%`
    return value.toString()
  }

  const colorClasses = {
    green: 'text-green-400',
    red: 'text-red-400',
    yellow: 'text-yellow-400',
    blue: 'text-blue-400',
    purple: 'text-purple-400',
    white: 'text-white',
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <p className="text-gray-400 text-xs uppercase tracking-wide">{label}</p>
      <p className={clsx('text-2xl font-bold mt-1 font-mono', colorClasses[color])}>
        {formatValue()}
      </p>
      {subtitle && <p className="text-gray-500 text-xs mt-1">{subtitle}</p>}
    </div>
  )
}

// Tab Button Component
function TabButton({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean
  onClick: () => void
  label: string
  count?: number
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'pb-3 text-sm font-medium border-b-2 transition-colors',
        active
          ? 'text-indigo-400 border-indigo-400'
          : 'text-gray-400 border-transparent hover:text-gray-300'
      )}
    >
      {label}
      {count !== undefined && (
        <span className="ml-2 px-2 py-0.5 bg-gray-700 rounded-full text-xs">
          {count}
        </span>
      )}
    </button>
  )
}

// Refresh Button Component
function RefreshButton() {
  const [isRefreshing, setIsRefreshing] = useState(false)

  const handleRefresh = async () => {
    setIsRefreshing(true)
    try {
      await api.refreshCSGOData()
    } catch (e) {
      console.error('Failed to refresh:', e)
    }
    setTimeout(() => setIsRefreshing(false), 1000)
  }

  return (
    <button
      onClick={handleRefresh}
      disabled={isRefreshing}
      className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 rounded-lg text-sm font-medium text-white transition-colors disabled:opacity-50"
    >
      {isRefreshing ? 'Refreshing...' : 'Refresh Data'}
    </button>
  )
}

// Engine Positions Panel - Positions from CSGO engine with spread column
function EnginePositionsPanel({
  positions,
  loading,
  statusFilter,
  onStatusFilterChange,
  strategyFilter,
  onStrategyFilterChange,
}: {
  positions: CSGOEnginePosition[]
  loading: boolean
  statusFilter: string
  onStatusFilterChange: (s: string) => void
  strategyFilter: string
  onStrategyFilterChange: (s: string) => void
}) {
  // Extract unique strategies
  const strategies = useMemo(() => {
    const unique = new Set(positions.map(p => p.strategy_name))
    return Array.from(unique).sort()
  }, [positions])

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-4 flex-wrap">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Status</label>
          <select
            value={statusFilter}
            onChange={(e) => onStatusFilterChange(e.target.value)}
            className="bg-gray-700 text-white rounded-lg px-3 py-2 text-sm border border-gray-600"
          >
            <option value="">All</option>
            <option value="open">Open</option>
            <option value="partial">Partial</option>
            <option value="closed">Closed</option>
          </select>
        </div>

        <div>
          <label className="block text-xs text-gray-400 mb-1">Strategy</label>
          <select
            value={strategyFilter}
            onChange={(e) => onStrategyFilterChange(e.target.value)}
            className="bg-gray-700 text-white rounded-lg px-3 py-2 text-sm border border-gray-600"
          >
            <option value="">All Strategies</option>
            {strategies.map(s => (
              <option key={s} value={s}>
                {s.replace('csgo_', '')}
              </option>
            ))}
          </select>
        </div>

        {(statusFilter !== 'open' || strategyFilter) && (
          <button
            onClick={() => { onStatusFilterChange('open'); onStrategyFilterChange(''); }}
            className="mt-5 text-xs text-gray-400 hover:text-white"
          >
            Reset filters
          </button>
        )}
      </div>

      {/* Table */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-700/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Match</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Strategy</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Side</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Entry</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Now</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Spread</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Cost</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">P&L</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {loading ? (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-gray-400">
                    Loading...
                  </td>
                </tr>
              ) : positions.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-gray-400">
                    No positions found
                  </td>
                </tr>
              ) : (
                positions.map((p) => <EnginePositionRow key={p.id} position={p} />)
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// Engine Position Row
function EnginePositionRow({ position }: { position: CSGOEnginePosition }) {
  const pnl = position.status === 'closed' ? position.realized_pnl : (position.unrealized_pnl ?? 0)
  const isProfit = pnl >= 0

  const statusColors: Record<string, string> = {
    open: 'bg-yellow-500/20 text-yellow-400',
    partial: 'bg-blue-500/20 text-blue-400',
    closed: 'bg-gray-500/20 text-gray-400',
  }

  // Format team name to be shorter
  const shortTeam = (name: string | null) => {
    if (!name) return '?'
    return name.length > 15 ? name.slice(0, 15) + '...' : name
  }

  const strategyColors: Record<string, string> = {
    scalp: 'bg-purple-500/20 text-purple-400',
    favorite_hedge: 'bg-blue-500/20 text-blue-400',
    swing_rebalance: 'bg-orange-500/20 text-orange-400',
    map_longshot: 'bg-green-500/20 text-green-400',
  }

  const getStrategyColor = (name: string) => {
    for (const key of Object.keys(strategyColors)) {
      if (name.includes(key)) return strategyColors[key]
    }
    return 'bg-gray-500/20 text-gray-400'
  }

  return (
    <tr className="hover:bg-gray-700/30">
      {/* Match: Team YES vs Team NO */}
      <td className="px-4 py-3">
        <div className="text-sm">
          <span className={clsx(
            position.token_type === 'YES' ? 'text-green-400 font-semibold' : 'text-gray-300'
          )}>
            {shortTeam(position.team_yes)}
          </span>
          <span className="text-gray-500 mx-2">vs</span>
          <span className={clsx(
            position.token_type === 'NO' ? 'text-green-400 font-semibold' : 'text-gray-300'
          )}>
            {shortTeam(position.team_no)}
          </span>
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          {position.format && (
            <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-gray-600 text-gray-300">
              {position.format}
            </span>
          )}
          <span className="text-gray-500 text-xs">
            {position.opened_at ? new Date(position.opened_at).toLocaleString() : ''}
          </span>
        </div>
      </td>

      {/* Strategy */}
      <td className="px-4 py-3">
        <span className={clsx(
          'px-2 py-1 rounded text-xs font-medium',
          getStrategyColor(position.strategy_name)
        )}>
          {position.strategy_name.replace('csgo_', '')}
        </span>
      </td>

      {/* Side: YES or NO */}
      <td className="px-4 py-3 text-center">
        <span className={clsx(
          'px-2 py-1 rounded text-xs font-bold',
          position.token_type === 'YES' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
        )}>
          {position.token_type}
        </span>
      </td>

      {/* Entry Price */}
      <td className="px-4 py-3 text-right text-white font-mono text-sm">
        {(position.avg_entry_price * 100).toFixed(1)}%
      </td>

      {/* Current Price */}
      <td className="px-4 py-3 text-right font-mono text-sm">
        {position.current_price !== null ? (
          <span className={clsx(
            position.current_price > position.avg_entry_price ? 'text-green-400' :
            position.current_price < position.avg_entry_price ? 'text-red-400' : 'text-white'
          )}>
            {(position.current_price * 100).toFixed(1)}%
          </span>
        ) : (
          <span className="text-gray-500">-</span>
        )}
      </td>

      {/* Entry Spread */}
      <td className="px-4 py-3 text-right font-mono text-sm">
        {position.entry_spread !== null ? (
          <span className={clsx(
            position.entry_spread <= 0.02 ? 'text-green-400' :
            position.entry_spread <= 0.05 ? 'text-yellow-400' : 'text-red-400'
          )}>
            {(position.entry_spread * 100).toFixed(1)}%
          </span>
        ) : (
          <span className="text-gray-500">-</span>
        )}
      </td>

      {/* Cost Basis */}
      <td className="px-4 py-3 text-right text-gray-300 font-mono text-sm">
        ${position.cost_basis.toFixed(2)}
      </td>

      {/* P&L */}
      <td className={clsx('px-4 py-3 text-right font-mono text-sm font-bold', isProfit ? 'text-green-400' : 'text-red-400')}>
        {isProfit ? '+' : ''}${pnl.toFixed(2)}
      </td>

      {/* Status */}
      <td className="px-4 py-3 text-center">
        <span className={clsx('px-2 py-1 rounded text-xs font-medium', statusColors[position.status] ?? statusColors.open)}>
          {position.status.toUpperCase()}
        </span>
      </td>
    </tr>
  )
}

// Events Panel - CS:GO events with expandable nested markets
function EventsPanel({
  events,
  loading,
  upcomingOnly,
  onUpcomingOnlyChange,
}: {
  events: CSGOPipelineEvent[]
  loading: boolean
  upcomingOnly: boolean
  onUpcomingOnlyChange: (v: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [syncing, setSyncing] = useState(false)
  const [expandedEvents, setExpandedEvents] = useState<Set<string>>(new Set())

  const handleSync = async () => {
    setSyncing(true)
    try {
      await api.syncCSGOPipeline()
      queryClient.invalidateQueries({ queryKey: ['csgo-pipeline-events'] })
    } catch (e) {
      console.error('Sync failed:', e)
    }
    setSyncing(false)
  }

  const toggleExpand = (eventKey: string) => {
    setExpandedEvents(prev => {
      const next = new Set(prev)
      if (next.has(eventKey)) {
        next.delete(eventKey)
      } else {
        next.add(eventKey)
      }
      return next
    })
  }

  const getTimeUntil = (isoString: string | null) => {
    if (!isoString) return null
    const now = new Date()
    const target = new Date(isoString)
    const diffMs = target.getTime() - now.getTime()
    const diffMins = Math.round(diffMs / 60000)

    if (diffMins < 0) return 'LIVE'
    if (diffMins < 60) return `${diffMins}m`
    if (diffMins < 1440) return `${Math.round(diffMins / 60)}h`
    return `${Math.round(diffMins / 1440)}d`
  }

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-gray-400">
            <input
              type="checkbox"
              checked={upcomingOnly}
              onChange={(e) => onUpcomingOnlyChange(e.target.checked)}
              className="rounded bg-gray-700 border-gray-600"
            />
            Upcoming only (24h)
          </label>
          <span className="text-gray-500 text-sm">
            {events.length} events
          </span>
        </div>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 rounded text-sm text-white disabled:opacity-50"
        >
          {syncing ? 'Syncing...' : 'Sync Now'}
        </button>
      </div>

      {/* Events List */}
      <div className="space-y-2">
        {loading ? (
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-8 text-center text-gray-400">
            Loading...
          </div>
        ) : events.length === 0 ? (
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-8 text-center text-gray-400">
            No events found. Click "Sync Now" to discover CS:GO markets.
          </div>
        ) : (
          events.map((event) => {
            const isExpanded = expandedEvents.has(event.event_key)
            const timeUntil = getTimeUntil(event.game_start_time)
            const isLive = event.is_live || timeUntil === 'LIVE'

            return (
              <div key={event.event_key} className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
                {/* Event Header (Clickable) */}
                <button
                  onClick={() => toggleExpand(event.event_key)}
                  className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-700/30 transition-colors"
                >
                  <div className="flex items-center gap-4">
                    {/* Expand/Collapse Icon */}
                    <span className={clsx(
                      'text-gray-400 transition-transform',
                      isExpanded && 'rotate-90'
                    )}>
                      {'>'}
                    </span>

                    {/* Teams */}
                    <div className="text-left">
                      <div className="text-white font-medium">
                        {event.team_yes} <span className="text-gray-500">vs</span> {event.team_no}
                      </div>
                      {event.tournament && (
                        <div className="text-gray-500 text-xs mt-0.5">{event.tournament}</div>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-6">
                    {/* Format */}
                    {event.format && (
                      <span className="px-2 py-0.5 bg-gray-600 rounded text-xs text-white">
                        {event.format}
                      </span>
                    )}

                    {/* Time Until */}
                    <span className={clsx(
                      'text-sm font-mono min-w-[50px] text-right',
                      isLive ? 'text-green-400 font-bold' :
                      timeUntil?.includes('m') ? 'text-yellow-400' : 'text-gray-300'
                    )}>
                      {timeUntil || '-'}
                    </span>

                    {/* Main Price */}
                    <span className="text-white font-mono text-sm min-w-[50px] text-right">
                      {event.main_price !== null ? `${(event.main_price * 100).toFixed(0)}%` : '-'}
                    </span>

                    {/* Spread */}
                    <span className={clsx(
                      'font-mono text-sm min-w-[40px] text-right',
                      event.main_spread !== null && event.main_spread > 0.15 ? 'text-red-400' :
                      event.main_spread !== null && event.main_spread > 0.10 ? 'text-yellow-400' : 'text-green-400'
                    )}>
                      {event.main_spread !== null ? `${(event.main_spread * 100).toFixed(0)}%` : '-'}
                    </span>

                    {/* Market Count */}
                    <span className="px-2 py-0.5 bg-indigo-500/20 text-indigo-400 rounded text-xs min-w-[60px] text-center">
                      {event.market_count} {event.market_count === 1 ? 'market' : 'markets'}
                    </span>
                  </div>
                </button>

                {/* Expanded Markets */}
                {isExpanded && event.markets.length > 0 && (
                  <div className="border-t border-gray-700">
                    <table className="w-full">
                      <thead className="bg-gray-700/30">
                        <tr>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Market</th>
                          <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Price</th>
                          <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Spread</th>
                          <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Volume</th>
                          <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Liquidity</th>
                          <th className="px-4 py-2 text-center text-xs font-medium text-gray-500 uppercase">WS</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-700/50">
                        {event.markets.map((market) => (
                          <tr key={market.id} className="hover:bg-gray-700/20">
                            <td className="px-4 py-2">
                              <div className="flex items-center gap-2">
                                <span className={clsx(
                                  'px-1.5 py-0.5 rounded text-xs font-medium',
                                  market.market_type === 'moneyline' ? 'bg-blue-500/20 text-blue-400' :
                                  market.market_type === 'map_winner' ? 'bg-purple-500/20 text-purple-400' :
                                  'bg-gray-500/20 text-gray-400'
                                )}>
                                  {market.market_type}
                                </span>
                                {market.group_item_title && (
                                  <span className="text-gray-400 text-sm">
                                    {market.group_item_title}
                                  </span>
                                )}
                              </div>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <span className="text-white font-mono text-sm">
                                {market.current_price !== null ? `${(market.current_price * 100).toFixed(1)}%` : '-'}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <span className={clsx(
                                'font-mono text-sm',
                                market.spread !== null && market.spread > 0.15 ? 'text-red-400' :
                                market.spread !== null && market.spread > 0.10 ? 'text-yellow-400' : 'text-green-400'
                              )}>
                                {market.spread !== null ? `${(market.spread * 100).toFixed(1)}%` : '-'}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <div className="flex flex-col items-end">
                                <span className={clsx(
                                  'font-mono text-sm',
                                  market.volume !== null && market.volume >= 1000 ? 'text-green-400' :
                                  market.volume !== null && market.volume >= 100 ? 'text-white' : 'text-gray-500'
                                )}>
                                  {market.volume !== null
                                    ? market.volume >= 1000
                                      ? `$${(market.volume / 1000).toFixed(1)}k`
                                      : `$${market.volume.toFixed(0)}`
                                    : '-'}
                                </span>
                                {market.volume_24h !== null && market.volume_24h > 0 && (
                                  <span className="text-xs text-gray-500">
                                    24h: ${market.volume_24h >= 1000 ? `${(market.volume_24h / 1000).toFixed(1)}k` : market.volume_24h.toFixed(0)}
                                  </span>
                                )}
                              </div>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <span className={clsx(
                                'font-mono text-sm',
                                market.liquidity !== null && market.liquidity >= 5000 ? 'text-green-400' :
                                market.liquidity !== null && market.liquidity >= 1000 ? 'text-white' : 'text-gray-500'
                              )}>
                                {market.liquidity !== null
                                  ? market.liquidity >= 1000
                                    ? `$${(market.liquidity / 1000).toFixed(1)}k`
                                    : `$${market.liquidity.toFixed(0)}`
                                  : '-'}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-center">
                              <span className={clsx(
                                'w-2 h-2 rounded-full inline-block',
                                market.subscribed ? 'bg-green-500' : 'bg-gray-600'
                              )} title={market.subscribed ? 'Subscribed to WebSocket' : 'Not subscribed'} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

// Price Chart Panel - Minute-level price with trade execution markers
function PriceChartPanel() {
  const [selectedMarketId, setSelectedMarketId] = useState<number | null>(null)

  // Fetch positions list for dropdown
  const { data: positionList, isLoading: listLoading } = useQuery({
    queryKey: ['csgo-position-list'],
    queryFn: () => api.getCSGOPositionList(),
  })

  // Fetch price history when a market is selected
  // Time range is automatic: game_start - 2h to game_start + 5h
  const { data: priceHistory, isLoading: historyLoading } = useQuery({
    queryKey: ['csgo-price-history', selectedMarketId],
    queryFn: () => api.getCSGOPriceHistory(selectedMarketId!),
    enabled: !!selectedMarketId,
    refetchInterval: 5000,
  })

  // Format chart data - use numeric timestamps for X axis
  const chartData = useMemo(() => {
    if (!priceHistory?.price_data) return []
    return priceHistory.price_data.map((p) => ({
      time: new Date(p.timestamp).getTime(),  // Numeric timestamp for X axis
      timeLabel: new Date(p.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
      yes: p.yes_price ? p.yes_price * 100 : null,
      no: p.no_price ? p.no_price * 100 : null,
      bid: p.best_bid ? p.best_bid * 100 : null,
      ask: p.best_ask ? p.best_ask * 100 : null,
    }))
  }, [priceHistory])

  // Format trade markers with x position (numeric timestamp)
  const tradeMarkers = useMemo(() => {
    if (!priceHistory?.trades || !chartData.length) return []
    const firstTime = chartData[0].time
    const lastTime = chartData[chartData.length - 1].time

    return priceHistory.trades
      .map((t) => {
        const tradeTime = new Date(t.timestamp).getTime()
        // Find closest data point
        const closest = chartData.reduce((prev, curr) =>
          Math.abs(curr.time - tradeTime) < Math.abs(prev.time - tradeTime) ? curr : prev
        )
        return {
          ...t,
          x: tradeTime,  // Use actual trade time (numeric)
          y: t.price ? t.price * 100 : (t.bet_on_side === 'YES' ? closest.yes : closest.no),
          tradeTime,
          inRange: tradeTime >= firstTime && tradeTime <= lastTime,
        }
      })
      .filter(t => t.inRange)  // Only show trades within chart range
  }, [priceHistory, chartData])

  // Game start time x position (numeric timestamp)
  const gameStartX = useMemo(() => {
    if (!priceHistory?.match_info?.game_start_time || !chartData.length) return null
    const gameStartTime = new Date(priceHistory.match_info.game_start_time).getTime()
    // Only show if game start is within the chart range
    const firstTime = chartData[0].time
    const lastTime = chartData[chartData.length - 1].time
    if (gameStartTime < firstTime || gameStartTime > lastTime) return null
    return gameStartTime  // Return numeric timestamp
  }, [priceHistory, chartData])

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-center gap-4">
        <div className="flex-1">
          <label className="block text-xs text-gray-400 mb-1">Select Match/Position</label>
          <select
            value={selectedMarketId ?? ''}
            onChange={(e) => setSelectedMarketId(e.target.value ? Number(e.target.value) : null)}
            className="w-full max-w-md bg-gray-700 text-white rounded-lg px-3 py-2 text-sm border border-gray-600"
            disabled={listLoading}
          >
            <option value="">-- Select a position --</option>
            {positionList?.positions.map((p) => (
              <option key={p.id} value={p.market_id}>
                {p.label} ({p.status})
              </option>
            ))}
          </select>
        </div>

      </div>

      {/* Match Info */}
      {priceHistory?.match_info && (
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
          <div className="flex items-center gap-4">
            <h3 className="text-lg font-semibold text-white">
              {priceHistory.match_info.team_yes} vs {priceHistory.match_info.team_no}
            </h3>
            {priceHistory.match_info.group_item_title && (
              <span className="px-2 py-1 bg-purple-500/20 text-purple-400 rounded text-sm">
                {priceHistory.match_info.group_item_title}
              </span>
            )}
            {priceHistory.match_info.format && (
              <span className="px-2 py-1 bg-gray-600 text-gray-300 rounded text-sm">
                {priceHistory.match_info.format}
              </span>
            )}
          </div>
          {priceHistory.match_info.tournament && (
            <p className="text-gray-400 text-sm mt-1">{priceHistory.match_info.tournament}</p>
          )}
          <p className="text-gray-500 text-xs mt-1">
            {priceHistory.data_points} data points | {priceHistory.trades.length} trades |
            Source: {priceHistory.data_source} |
            Game Start: {gameStartX ? new Date(gameStartX).toLocaleTimeString() : 'out of range'} |
            Markers: {tradeMarkers.length} in range
          </p>
        </div>
      )}

      {/* Chart */}
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        {!selectedMarketId ? (
          <div className="h-80 flex items-center justify-center text-gray-400">
            Select a position to view price chart
          </div>
        ) : historyLoading ? (
          <div className="h-80 flex items-center justify-center text-gray-400">
            Loading price data...
          </div>
        ) : chartData.length === 0 ? (
          <div className="h-80 flex items-center justify-center text-gray-400">
            No price data available
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={400}>
            <LineChart data={chartData} margin={{ top: 20, right: 30, left: 0, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis
                dataKey="time"
                type="number"
                scale="time"
                domain={['dataMin', 'dataMax']}
                stroke="#9CA3AF"
                tick={{ fontSize: 12 }}
                tickFormatter={(ts) => new Date(ts).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
              />
              <YAxis
                domain={[0, 100]}
                stroke="#9CA3AF"
                tick={{ fontSize: 12 }}
                tickFormatter={(v) => `${v}%`}
              />
              <Tooltip
                contentStyle={{ backgroundColor: '#1F2937', border: '1px solid #374151', borderRadius: '8px' }}
                labelStyle={{ color: '#9CA3AF' }}
                formatter={(value: number) => [`${value?.toFixed(1)}%`, '']}
              />
              <Legend />

              {/* YES price line */}
              <Line
                type="monotone"
                dataKey="yes"
                name={priceHistory?.match_info?.team_yes || 'YES'}
                stroke="#10B981"
                strokeWidth={2}
                dot={false}
                connectNulls
              />

              {/* NO price line */}
              <Line
                type="monotone"
                dataKey="no"
                name={priceHistory?.match_info?.team_no || 'NO'}
                stroke="#EF4444"
                strokeWidth={2}
                dot={false}
                connectNulls
              />

              {/* Game start time vertical line */}
              {gameStartX && (
                <ReferenceLine
                  x={gameStartX}
                  stroke="#FBBF24"
                  strokeWidth={2}
                  strokeDasharray="5 5"
                  label={{
                    value: 'Game Start',
                    position: 'top',
                    fill: '#FBBF24',
                    fontSize: 12,
                  }}
                />
              )}

              {/* Trade execution vertical lines */}
              {tradeMarkers.map((trade, idx) => {
                const strategyLabel = trade.strategy_name
                  ? trade.strategy_name.replace('csgo_', '').replace('_default', '')
                  : `Trade ${idx + 1}`
                return (
                  <ReferenceLine
                    key={`line-${idx}`}
                    x={trade.x}
                    stroke={trade.bet_on_side === 'YES' ? '#10B981' : '#EF4444'}
                    strokeWidth={2}
                    label={{
                      value: strategyLabel,
                      position: 'insideTopRight',
                      fill: trade.bet_on_side === 'YES' ? '#10B981' : '#EF4444',
                      fontSize: 10,
                    }}
                  />
                )
              })}

              {/* Trade execution dot markers */}
              {tradeMarkers.filter(t => t.y !== null).map((trade, idx) => (
                <ReferenceDot
                  key={`dot-${idx}`}
                  x={trade.x}
                  y={trade.y as number}
                  r={8}
                  fill={trade.bet_on_side === 'YES' ? '#10B981' : '#EF4444'}
                  stroke="#fff"
                  strokeWidth={2}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Trade Legend */}
      {tradeMarkers.length > 0 && (
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
          <h4 className="text-sm font-medium text-gray-400 mb-3">Trade Executions</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-xs">
                  <th className="text-left pb-2">Time</th>
                  <th className="text-left pb-2">Strategy</th>
                  <th className="text-left pb-2">Team</th>
                  <th className="text-right pb-2">Price</th>
                  <th className="text-right pb-2">Spread</th>
                  <th className="text-right pb-2">Slippage</th>
                  <th className="text-right pb-2">Size</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {tradeMarkers.map((trade, idx) => {
                  const strategyName = trade.strategy_name?.replace('csgo_', '').replace('_default', '') || 'unknown'
                  const strategyColor = strategyName.includes('scalp')
                    ? 'bg-purple-500/20 text-purple-400'
                    : strategyName.includes('momentum')
                    ? 'bg-blue-500/20 text-blue-400'
                    : strategyName.includes('longshot')
                    ? 'bg-orange-500/20 text-orange-400'
                    : strategyName.includes('favorite')
                    ? 'bg-green-500/20 text-green-400'
                    : 'bg-gray-500/20 text-gray-400'
                  return (
                    <tr key={idx} className="hover:bg-gray-700/30">
                      <td className="py-2 text-gray-400 whitespace-nowrap">
                        {new Date(trade.timestamp).toLocaleTimeString()}
                      </td>
                      <td className="py-2">
                        <span className={clsx('px-2 py-0.5 rounded text-xs font-medium', strategyColor)}>
                          {strategyName}
                        </span>
                      </td>
                      <td className="py-2">
                        <div className="flex items-center gap-2">
                          <div className={clsx(
                            'w-2 h-2 rounded-full',
                            trade.bet_on_side === 'YES' ? 'bg-green-500' : 'bg-red-500'
                          )} />
                          <span className="text-white font-medium">{trade.bet_on_team}</span>
                        </div>
                      </td>
                      <td className="py-2 text-right text-white font-mono">
                        {trade.price ? `${(trade.price * 100).toFixed(1)}%` : '-'}
                      </td>
                      <td className="py-2 text-right font-mono">
                        {trade.spread !== undefined && trade.spread !== null ? (
                          <span className={clsx(
                            trade.spread <= 0.02 ? 'text-green-400' :
                            trade.spread <= 0.05 ? 'text-yellow-400' : 'text-red-400'
                          )}>
                            {(trade.spread * 100).toFixed(1)}%
                          </span>
                        ) : (
                          <span className="text-gray-500">-</span>
                        )}
                      </td>
                      <td className="py-2 text-right font-mono">
                        {trade.slippage !== undefined && trade.slippage !== null ? (
                          <span className={clsx(
                            trade.slippage <= 0.005 ? 'text-green-400' :
                            trade.slippage <= 0.01 ? 'text-yellow-400' : 'text-red-400'
                          )}>
                            {(trade.slippage * 100).toFixed(2)}%
                          </span>
                        ) : (
                          <span className="text-gray-500">-</span>
                        )}
                      </td>
                      <td className="py-2 text-right text-yellow-400 font-mono">
                        ${trade.size_usd?.toFixed(2)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// Engine Panel - New real-time trading engine
function EnginePanel({
  health,
  stats,
  statsLoading,
  strategies,
  strategiesLoading,
  spreads,
  spreadsLoading,
  trades,
  tradesLoading,
  streamLength,
  statusFilter,
  strategyFilter,
  onStatusFilterChange,
  onStrategyFilterChange,
}: {
  health?: { status: string; message: string }
  stats?: {
    positions_open: number
    positions_closed: number
    spreads_open: number
    spreads_closed: number
    total_trades: number
    strategies_active: number
    total_realized_pnl: number
    total_unrealized_pnl: number
    stream_length: number
  }
  statsLoading: boolean
  strategies: CSGOEngineStrategyState[]
  strategiesLoading: boolean
  spreads: CSGOEngineSpread[]
  spreadsLoading: boolean
  trades: CSGOEngineTrade[]
  tradesLoading: boolean
  streamLength: number
  statusFilter: string
  strategyFilter: string
  onStatusFilterChange: (v: string) => void
  onStrategyFilterChange: (v: string) => void
}) {
  const healthColor = health?.status === 'healthy' ? 'green' : health?.status === 'degraded' ? 'yellow' : 'red'

  return (
    <div className="space-y-6">
      {/* Engine Status Bar */}
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className={clsx(
              'w-3 h-3 rounded-full',
              healthColor === 'green' ? 'bg-green-500' : healthColor === 'yellow' ? 'bg-yellow-500' : 'bg-red-500'
            )} />
            <span className="text-white font-medium">CSGO Engine</span>
            <span className={clsx(
              'px-2 py-1 rounded text-xs font-medium',
              healthColor === 'green' ? 'bg-green-500/20 text-green-400' :
              healthColor === 'yellow' ? 'bg-yellow-500/20 text-yellow-400' : 'bg-red-500/20 text-red-400'
            )}>
              {health?.status?.toUpperCase() || 'UNKNOWN'}
            </span>
            <span className="text-gray-400 text-sm">{health?.message}</span>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <span className="text-gray-400">Stream: <span className="text-white font-mono">{streamLength.toLocaleString()}</span></span>
            <span className="text-gray-400">Active: <span className="text-white font-mono">{stats?.strategies_active ?? 0}</span></span>
          </div>
        </div>
      </div>

      {/* Engine Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        <KPICard
          label="Total P&L"
          value={(stats?.total_realized_pnl ?? 0) + (stats?.total_unrealized_pnl ?? 0)}
          format="currency"
          color={((stats?.total_realized_pnl ?? 0) + (stats?.total_unrealized_pnl ?? 0)) >= 0 ? 'green' : 'red'}
          loading={statsLoading}
        />
        <KPICard
          label="Realized"
          value={stats?.total_realized_pnl ?? 0}
          format="currency"
          color={(stats?.total_realized_pnl ?? 0) >= 0 ? 'green' : 'red'}
          loading={statsLoading}
        />
        <KPICard
          label="Unrealized"
          value={stats?.total_unrealized_pnl ?? 0}
          format="currency"
          color={(stats?.total_unrealized_pnl ?? 0) >= 0 ? 'green' : 'red'}
          loading={statsLoading}
        />
        <KPICard
          label="Open Positions"
          value={stats?.positions_open ?? 0}
          format="number"
          loading={statsLoading}
        />
        <KPICard
          label="Open Spreads"
          value={stats?.spreads_open ?? 0}
          format="number"
          loading={statsLoading}
        />
        <KPICard
          label="Total Trades"
          value={stats?.total_trades ?? 0}
          format="number"
          loading={statsLoading}
        />
      </div>

      {/* Strategies Table */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-700">
          <h3 className="text-white font-medium">Strategies</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-700/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Strategy</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Allocated</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Available</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Realized</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Unrealized</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Trades</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Win Rate</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {strategiesLoading ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-gray-400">Loading...</td>
                </tr>
              ) : strategies.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-gray-400">No strategies registered</td>
                </tr>
              ) : (
                strategies.map((s) => (
                  <tr key={s.strategy_name} className="hover:bg-gray-700/30">
                    <td className="px-4 py-3 text-white font-medium">{s.strategy_name}</td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono">${s.allocated_usd.toFixed(2)}</td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono">${s.available_usd.toFixed(2)}</td>
                    <td className={clsx('px-4 py-3 text-right font-mono', s.total_realized_pnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                      {s.total_realized_pnl >= 0 ? '+' : ''}${s.total_realized_pnl.toFixed(2)}
                    </td>
                    <td className={clsx('px-4 py-3 text-right font-mono', s.total_unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                      {s.total_unrealized_pnl >= 0 ? '+' : ''}${s.total_unrealized_pnl.toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono">{s.trade_count}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {s.win_rate !== null ? (
                        <span className={s.win_rate >= 0.5 ? 'text-green-400' : 'text-yellow-400'}>
                          {(s.win_rate * 100).toFixed(0)}%
                        </span>
                      ) : (
                        <span className="text-gray-500">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={clsx(
                        'px-2 py-1 rounded text-xs font-medium',
                        s.is_active ? 'bg-green-500/20 text-green-400' : 'bg-gray-500/20 text-gray-400'
                      )}>
                        {s.is_active ? 'ACTIVE' : 'PAUSED'}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Spreads Section */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-700 flex items-center justify-between">
          <h3 className="text-white font-medium">Spreads</h3>
          <div className="flex items-center gap-4">
            <select
              value={statusFilter}
              onChange={(e) => onStatusFilterChange(e.target.value)}
              className="bg-gray-700 text-white rounded px-3 py-1 text-sm border border-gray-600"
            >
              <option value="">All Status</option>
              <option value="open">Open</option>
              <option value="partial">Partial</option>
              <option value="closed">Closed</option>
            </select>
            <select
              value={strategyFilter}
              onChange={(e) => onStrategyFilterChange(e.target.value)}
              className="bg-gray-700 text-white rounded px-3 py-1 text-sm border border-gray-600"
            >
              <option value="">All Strategies</option>
              {strategies.map((s) => (
                <option key={s.strategy_name} value={s.strategy_name}>{s.strategy_name}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-700/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">ID</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Match</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Strategy</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Type</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Cost</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Realized</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Unrealized</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {spreadsLoading ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-gray-400">Loading...</td>
                </tr>
              ) : spreads.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-gray-400">No spreads found</td>
                </tr>
              ) : (
                spreads.map((s) => (
                  <tr key={s.id} className="hover:bg-gray-700/30">
                    <td className="px-4 py-3 text-gray-400 font-mono text-sm">#{s.id}</td>
                    <td className="px-4 py-3">
                      <div className="text-white text-sm">
                        {s.team_yes && s.team_no ? `${s.team_yes} vs ${s.team_no}` : `Market #${s.market_id}`}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-gray-300 text-sm">{s.strategy_name}</td>
                    <td className="px-4 py-3">
                      <span className="px-2 py-1 bg-purple-500/20 text-purple-400 rounded text-xs font-medium">
                        {s.spread_type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono">${s.total_cost_basis.toFixed(2)}</td>
                    <td className={clsx('px-4 py-3 text-right font-mono', s.total_realized_pnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                      {s.total_realized_pnl >= 0 ? '+' : ''}${s.total_realized_pnl.toFixed(2)}
                    </td>
                    <td className={clsx('px-4 py-3 text-right font-mono', s.total_unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                      {s.total_unrealized_pnl >= 0 ? '+' : ''}${s.total_unrealized_pnl.toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={clsx(
                        'px-2 py-1 rounded text-xs font-medium',
                        s.status === 'open' ? 'bg-yellow-500/20 text-yellow-400' :
                        s.status === 'partial' ? 'bg-blue-500/20 text-blue-400' : 'bg-gray-500/20 text-gray-400'
                      )}>
                        {s.status.toUpperCase()}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Trades */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-700">
          <h3 className="text-white font-medium">Recent Trades</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-700/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Time</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Match</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Side</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Shares</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Price</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Bid/Ask</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Spread</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Cost</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">Slippage</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {tradesLoading ? (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-gray-400">Loading...</td>
                </tr>
              ) : trades.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-gray-400">No trades yet</td>
                </tr>
              ) : (
                trades.map((t) => (
                  <tr key={t.id} className="hover:bg-gray-700/30">
                    <td className="px-4 py-3 text-gray-400 text-sm whitespace-nowrap">
                      {new Date(t.created_at).toLocaleTimeString()}
                    </td>
                    <td className="px-4 py-3">
                      {t.team_yes && t.team_no ? (
                        <div>
                          <div className="text-white text-sm">
                            {t.team_yes.length > 12 ? t.team_yes.slice(0, 12) + '...' : t.team_yes}
                            <span className="text-gray-500 mx-1">vs</span>
                            {t.team_no.length > 12 ? t.team_no.slice(0, 12) + '...' : t.team_no}
                          </div>
                          <div className="flex items-center gap-1 mt-0.5">
                            {t.format && (
                              <span className="px-1 py-0.5 bg-gray-600 rounded text-xs text-gray-300">
                                {t.format}
                              </span>
                            )}
                            {t.map_number && (
                              <span className="px-1 py-0.5 bg-purple-500/20 text-purple-400 rounded text-xs">
                                Map {t.map_number}
                              </span>
                            )}
                          </div>
                        </div>
                      ) : (
                        <span className="text-gray-500 text-sm">Pos #{t.position_id}</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={clsx(
                        'px-2 py-1 rounded text-xs font-medium',
                        t.side === 'BUY' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                      )}>
                        {t.side}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono text-sm">{t.shares.toFixed(1)}</td>
                    <td className="px-4 py-3 text-right text-white font-mono text-sm">{(t.price * 100).toFixed(1)}%</td>
                    <td className="px-4 py-3 text-right font-mono text-sm">
                      {t.best_bid !== null && t.best_ask !== null ? (
                        <span className="text-gray-300">
                          {(t.best_bid * 100).toFixed(0)}/{(t.best_ask * 100).toFixed(0)}
                        </span>
                      ) : (
                        <span className="text-gray-500">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-sm">
                      {t.spread !== null ? (
                        <span className={clsx(
                          t.spread <= 0.02 ? 'text-green-400' :
                          t.spread <= 0.05 ? 'text-yellow-400' : 'text-red-400'
                        )}>
                          {(t.spread * 100).toFixed(1)}%
                        </span>
                      ) : (
                        <span className="text-gray-500">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono text-sm">${t.cost_usd.toFixed(2)}</td>
                    <td className="px-4 py-3 text-right font-mono text-sm">
                      {t.slippage !== null ? (
                        <span className={clsx(
                          t.slippage <= 0.005 ? 'text-green-400' :
                          t.slippage <= 0.01 ? 'text-yellow-400' : 'text-red-400'
                        )}>
                          {(t.slippage * 100).toFixed(2)}%
                        </span>
                      ) : (
                        <span className="text-gray-500">-</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
