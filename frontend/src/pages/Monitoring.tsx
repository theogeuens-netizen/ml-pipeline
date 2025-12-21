import { useState } from 'react'
import { useMonitoringHealth, useMonitoringErrors, useFieldCompleteness, useWebSocketCoverage, useSubscriptionHealth, useConnectionStatus, useTierTransitions, useTaskActivity, useRedisStats, useLifecycleStatus } from '../hooks/useData'
import { clsx } from 'clsx'

const CATEGORY_LABELS: Record<string, string> = {
  price: 'Price',
  momentum: 'Momentum',
  volume: 'Volume',
  orderbook_depth: 'Orderbook Depth',
  orderbook_derived: 'Orderbook Derived',
  trade_flow: 'Trade Flow',
  whale_metrics: 'Whale Metrics',
  context: 'Context',
}

export default function Monitoring() {
  const { data: health, isLoading: healthLoading } = useMonitoringHealth()
  const { data: errors, isLoading: errorsLoading } = useMonitoringErrors(20)
  const { data: completeness, isLoading: completenessLoading } = useFieldCompleteness()
  const { data: wsCoverage } = useWebSocketCoverage()
  const { data: subHealth } = useSubscriptionHealth()
  const { data: connStatus } = useConnectionStatus()
  const { data: tierTransitions } = useTierTransitions(1)
  const { data: taskActivity } = useTaskActivity(30)
  const { data: redisStats } = useRedisStats()
  const { data: lifecycleStatus } = useLifecycleStatus()
  const [selectedError, setSelectedError] = useState<number | null>(null)

  if (healthLoading || completenessLoading) {
    return <div className="text-gray-400">Loading...</div>
  }

  const wsStatusColor = {
    healthy: 'bg-green-500',
    stale: 'bg-yellow-500',
    disconnected: 'bg-red-500',
  }[health?.websocket.status || 'disconnected']

  const wsStatusText = {
    healthy: 'LIVE',
    stale: 'STALE',
    disconnected: 'DOWN',
  }[health?.websocket.status || 'disconnected']

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-white">System Monitoring</h1>

      {/* System Health Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* WebSocket Status */}
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center gap-2 mb-2">
            <div className={clsx('w-3 h-3 rounded-full', wsStatusColor)} />
            <span className="text-gray-400 text-sm">WebSocket</span>
          </div>
          <div className="text-2xl font-bold text-white">{wsStatusText}</div>
          <div className="text-gray-400 text-sm mt-1">
            {health?.websocket.connected_markets || 0} markets
          </div>
        </div>

        {/* Tasks Status */}
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center gap-2 mb-2">
            <div className={clsx(
              'w-3 h-3 rounded-full',
              (health?.tasks.error_rate_pct || 0) < 5 ? 'bg-green-500' : 'bg-yellow-500'
            )} />
            <span className="text-gray-400 text-sm">Tasks (10min)</span>
          </div>
          <div className="text-2xl font-bold text-white">
            {health?.tasks.tasks_last_10min || 0}
          </div>
          <div className="text-gray-400 text-sm mt-1">
            {health?.tasks.errors_last_10min || 0} errors ({health?.tasks.error_rate_pct?.toFixed(1) || 0}%)
          </div>
        </div>

        {/* Trades Rate */}
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-gray-400 text-sm">Trades</span>
          </div>
          <div className="text-2xl font-bold text-white">
            {health?.websocket.trades_per_minute || 0}/min
          </div>
          <div className="text-gray-400 text-sm mt-1">
            {health?.websocket.trades_last_hour || 0} last hour
          </div>
        </div>

        {/* Field Completeness */}
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-gray-400 text-sm">Field Completeness</span>
          </div>
          <div className={clsx(
            'text-2xl font-bold',
            (completeness?.overall.avg_completeness_pct || 0) >= 70 ? 'text-green-400' : 'text-yellow-400'
          )}>
            {completeness?.overall.avg_completeness_pct?.toFixed(1) || 0}%
          </div>
          <div className="text-gray-400 text-sm mt-1">
            of {completeness?.overall.total_optional_fields || 0} optional fields
          </div>
        </div>
      </div>

      {/* WebSocket Coverage & Subscription Health */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* WebSocket Coverage */}
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">WebSocket Coverage</h2>
            <span className={clsx(
              'px-3 py-1 rounded-full text-sm font-medium',
              wsCoverage?.status === 'ok' ? 'bg-green-900 text-green-300' : 'bg-yellow-900 text-yellow-300'
            )}>
              {wsCoverage?.status?.toUpperCase() || 'LOADING'}
            </span>
          </div>
          {wsCoverage ? (
            <div className="space-y-3">
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Should subscribe</span>
                <span className="text-white font-medium">{wsCoverage.should_subscribe}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Actually subscribed</span>
                <span className="text-white font-medium">{wsCoverage.actually_subscribed}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Missing</span>
                <span className={clsx(
                  'font-medium',
                  wsCoverage.missing_count === 0 ? 'text-green-400' : 'text-red-400'
                )}>{wsCoverage.missing_count}</span>
              </div>
              {wsCoverage.missing_markets.length > 0 && (
                <div className="mt-4 pt-3 border-t border-gray-700">
                  <div className="text-xs text-gray-400 mb-2">Missing markets:</div>
                  {wsCoverage.missing_markets.slice(0, 3).map((m, idx) => (
                    <div key={idx} className="text-xs text-red-400 truncate">
                      T{m.tier}: {m.question || m.condition_id}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="text-gray-400">Loading...</div>
          )}
        </div>

        {/* Subscription Health */}
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Subscription Health</h2>
            <span className={clsx(
              'px-3 py-1 rounded-full text-sm font-medium',
              subHealth?.status === 'ok' ? 'bg-green-900 text-green-300' :
              subHealth?.status === 'warning' ? 'bg-yellow-900 text-yellow-300' :
              'bg-red-900 text-red-300'
            )}>
              {subHealth?.status?.toUpperCase() || 'LOADING'}
            </span>
          </div>
          {subHealth ? (
            <div className="space-y-3">
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Total subscribed</span>
                <span className="text-white font-medium">{subHealth.total_subscribed}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Active (trade &lt;10min)</span>
                <span className="text-green-400 font-medium">{subHealth.active} ({subHealth.active_pct}%)</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Quiet (10min-1hr)</span>
                <span className={clsx(
                  'font-medium',
                  subHealth.quiet === 0 ? 'text-green-400' : 'text-yellow-400'
                )}>{subHealth.quiet}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Dormant (&gt;1hr)</span>
                <span className={clsx(
                  'font-medium',
                  subHealth.dormant === 0 ? 'text-green-400' : 'text-red-400'
                )}>{subHealth.dormant}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Silent (no trades yet)</span>
                <span className="text-gray-500 font-medium">{subHealth.silent}</span>
              </div>
              {subHealth.dormant_markets && subHealth.dormant_markets.length > 0 && (
                <div className="mt-4 pt-3 border-t border-gray-700">
                  <div className="text-xs text-gray-400 mb-2">Dormant markets (were active):</div>
                  {subHealth.dormant_markets.slice(0, 3).map((m, idx) => (
                    <div key={idx} className="text-xs text-red-400 truncate">
                      T{m.tier}: {m.question || m.condition_id} ({Math.round(m.seconds_since_event / 3600)}hr ago)
                    </div>
                  ))}
                </div>
              )}
              <div className="mt-3 text-xs text-gray-500 italic">
                {subHealth.note}
              </div>
            </div>
          ) : (
            <div className="text-gray-400">Loading...</div>
          )}
        </div>
      </div>

      {/* Connection Status */}
      {connStatus && (
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Connection Status</h2>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
            {/* WebSocket */}
            <div>
              <div className="text-gray-400 text-sm mb-2">WebSocket ({connStatus.websocket.connections} connections)</div>
              <div className="text-2xl font-bold text-white">{connStatus.websocket.markets_subscribed}</div>
              <div className="text-sm text-gray-400">of {connStatus.websocket.total_capacity} capacity ({connStatus.websocket.utilization_pct}%)</div>
              <div className="mt-2 text-xs text-gray-500">
                {Object.entries(connStatus.websocket.by_tier).map(([tier, count]) => (
                  <span key={tier} className="mr-3">{tier}: {count}</span>
                ))}
              </div>
            </div>

            {/* Database */}
            <div>
              <div className="text-gray-400 text-sm mb-2">Database Pool</div>
              <div className="flex items-center gap-2">
                <div className={clsx(
                  'w-3 h-3 rounded-full',
                  connStatus.database.status === 'healthy' ? 'bg-green-500' : 'bg-yellow-500'
                )} />
                <span className="text-lg font-medium text-white">
                  {connStatus.database.connections_in_use}/{connStatus.database.pool_size}
                </span>
              </div>
              <div className="text-sm text-gray-400">connections in use</div>
            </div>

            {/* Redis */}
            <div>
              <div className="text-gray-400 text-sm mb-2">Redis</div>
              <div className="flex items-center gap-2">
                <div className={clsx(
                  'w-3 h-3 rounded-full',
                  connStatus.redis.status === 'healthy' ? 'bg-green-500' : 'bg-red-500'
                )} />
                <span className="text-lg font-medium text-white">{connStatus.redis.status.toUpperCase()}</span>
              </div>
              <div className="text-sm text-gray-400">{connStatus.redis.connected_clients} clients</div>
            </div>

            {/* API Clients */}
            <div>
              <div className="text-gray-400 text-sm mb-2">API Clients</div>
              <div className="space-y-1">
                <div className="flex items-center gap-2 text-sm">
                  <div className={clsx(
                    'w-2 h-2 rounded-full',
                    connStatus.api_clients.gamma.status === 'healthy' ? 'bg-green-500' : 'bg-red-500'
                  )} />
                  <span className="text-gray-300">Gamma ({connStatus.api_clients.gamma.rate_limit})</span>
                </div>
                <div className="flex items-center gap-2 text-sm">
                  <div className={clsx(
                    'w-2 h-2 rounded-full',
                    connStatus.api_clients.clob.status === 'healthy' ? 'bg-green-500' : 'bg-red-500'
                  )} />
                  <span className="text-gray-300">CLOB ({connStatus.api_clients.clob.rate_limit})</span>
                </div>
              </div>
              <div className="mt-2 text-xs text-gray-500">{connStatus.api_clients.note}</div>
            </div>
          </div>
        </div>
      )}

      {/* Tier Transitions, Task Activity, Redis Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Tier Transitions */}
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Tier Flow (1h)</h2>
            <span className="text-gray-400 text-sm">{tierTransitions?.total_transitions || 0} transitions</span>
          </div>
          {tierTransitions ? (
            <div className="space-y-2">
              {Object.entries(tierTransitions.summary).length > 0 ? (
                Object.entries(tierTransitions.summary)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([key, count]) => (
                    <div key={key} className="flex justify-between text-sm">
                      <span className={clsx(
                        'font-mono',
                        key.includes('deactivated') ? 'text-red-400' : 'text-blue-400'
                      )}>{key}</span>
                      <span className="text-white font-medium">{count}</span>
                    </div>
                  ))
              ) : (
                <div className="text-gray-500 text-sm">No tier changes in last hour</div>
              )}
              {tierTransitions.recent.length > 0 && (
                <div className="mt-4 pt-3 border-t border-gray-700">
                  <div className="text-xs text-gray-400 mb-2">Recent:</div>
                  {tierTransitions.recent.slice(0, 3).map((t, idx) => (
                    <div key={idx} className="text-xs text-gray-300 truncate">
                      T{t.from_tier}→{t.to_tier === -1 ? 'off' : `T${t.to_tier}`}: {t.market}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="text-gray-400 text-sm">Loading...</div>
          )}
        </div>

        {/* Task Activity */}
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Task Activity</h2>
          </div>
          {taskActivity ? (
            <div className="space-y-2">
              {Object.entries(taskActivity.by_task).map(([task, stats]) => (
                <div key={task} className="flex items-center justify-between text-sm">
                  <span className="text-gray-300 font-mono truncate flex-1">{task}</span>
                  <div className="flex items-center gap-2 ml-2">
                    <span className="text-green-400">{stats.success}</span>
                    {stats.failed > 0 && <span className="text-red-400">{stats.failed}</span>}
                    {stats.running > 0 && <span className="text-yellow-400">{stats.running}</span>}
                  </div>
                </div>
              ))}
              {taskActivity.recent.length > 0 && (
                <div className="mt-4 pt-3 border-t border-gray-700 max-h-32 overflow-y-auto">
                  <div className="text-xs text-gray-400 mb-2">Recent:</div>
                  {taskActivity.recent.slice(0, 5).map((t) => (
                    <div key={t.id} className="text-xs flex items-center gap-2 mb-1">
                      <span className={clsx(
                        'w-2 h-2 rounded-full',
                        t.status === 'success' ? 'bg-green-500' : t.status === 'failed' ? 'bg-red-500' : 'bg-yellow-500'
                      )} />
                      <span className="text-gray-300">{t.task}</span>
                      {t.tier !== null && <span className="text-gray-500">T{t.tier}</span>}
                      {t.duration_ms && <span className="text-gray-500">{t.duration_ms}ms</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="text-gray-400 text-sm">Loading...</div>
          )}
        </div>

        {/* Redis Stats */}
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Redis Stats</h2>
          </div>
          {redisStats ? (
            <div className="space-y-3">
              <div>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-gray-400">Memory</span>
                  <span className="text-white">{redisStats.memory_used_mb}MB / {redisStats.memory_peak_mb}MB peak</span>
                </div>
                <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className={clsx(
                      'h-full',
                      redisStats.memory_used_mb < 500 ? 'bg-green-500' : 'bg-yellow-500'
                    )}
                    style={{ width: `${Math.min(100, (redisStats.memory_used_mb / 1024) * 100)}%` }}
                  />
                </div>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Total Keys</span>
                <span className="text-white">{redisStats.total_keys}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Ops/sec</span>
                <span className="text-white">{redisStats.ops_per_sec}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-400">Clients</span>
                <span className="text-white">{redisStats.connected_clients}</span>
              </div>
              <div className="mt-3 pt-3 border-t border-gray-700">
                <div className="text-xs text-gray-400 mb-2">Keys by pattern:</div>
                {Object.entries(redisStats.keys_by_pattern).map(([pattern, count]) => (
                  <div key={pattern} className="flex justify-between text-xs mb-1">
                    <span className="text-gray-500 font-mono">{pattern}</span>
                    <span className="text-gray-300">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="text-gray-400 text-sm">Loading...</div>
          )}
        </div>
      </div>

      {/* Market Lifecycle Status */}
      {lifecycleStatus && (
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Market Lifecycle</h2>
            <div className="flex gap-4 text-sm">
              <span className="text-gray-400">
                24h: <span className="text-green-400">{lifecycleStatus.recent_activity_24h.closed} closed</span>,{' '}
                <span className="text-blue-400">{lifecycleStatus.recent_activity_24h.resolved} resolved</span>
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Trading Status Distribution */}
            <div>
              <div className="text-gray-400 text-sm mb-3">Trading Status</div>
              <div className="space-y-2">
                {Object.entries(lifecycleStatus.trading_status_distribution)
                  .sort(([, a], [, b]) => b - a)
                  .map(([status, count]) => (
                    <div key={status} className="flex items-center gap-3">
                      <div className={clsx(
                        'w-3 h-3 rounded-full',
                        status === 'trading' ? 'bg-green-500' :
                        status === 'closed' ? 'bg-yellow-500' :
                        status === 'resolved' ? 'bg-blue-500' :
                        status === 'suspended' ? 'bg-orange-500' :
                        'bg-gray-500'
                      )} />
                      <span className="text-gray-300 capitalize flex-1">{status}</span>
                      <span className="text-white font-medium">{count.toLocaleString()}</span>
                    </div>
                  ))}
              </div>
            </div>

            {/* UMA Resolution Status Distribution */}
            <div>
              <div className="text-gray-400 text-sm mb-3">UMA Status</div>
              <div className="space-y-2">
                {Object.entries(lifecycleStatus.uma_status_distribution)
                  .sort(([, a], [, b]) => b - a)
                  .map(([status, count]) => (
                    <div key={status} className="flex items-center gap-3">
                      <div className={clsx(
                        'w-3 h-3 rounded-full',
                        status === 'resolved' ? 'bg-blue-500' :
                        status === 'proposed' ? 'bg-yellow-500' :
                        status === 'disputed' ? 'bg-red-500' :
                        status === 'none' ? 'bg-gray-500' :
                        'bg-purple-500'
                      )} />
                      <span className="text-gray-300 capitalize flex-1">{status}</span>
                      <span className="text-white font-medium">{count.toLocaleString()}</span>
                    </div>
                  ))}
              </div>
            </div>
          </div>

          {/* Alerts */}
          {lifecycleStatus.alerts.markets_stuck_pending_24h > 0 && (
            <div className="mt-4 pt-4 border-t border-gray-700">
              <div className="flex items-center gap-2 text-yellow-400">
                <span className="text-sm">⚠️ {lifecycleStatus.alerts.markets_stuck_pending_24h} markets stuck in pending state &gt;24h</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Field Completeness by Category */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Field Completeness by Category</h2>
        <div className="space-y-3">
          {completeness && Object.entries(completeness.by_category).map(([category, data]) => (
            <div key={category} className="flex items-center gap-4">
              <span className="w-36 text-gray-400 text-sm">{CATEGORY_LABELS[category] || category}</span>
              <div className="flex-1">
                <div className="h-4 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className={clsx(
                      'h-full transition-all',
                      data.pct >= 80 ? 'bg-green-500' : data.pct >= 50 ? 'bg-yellow-500' : 'bg-red-500'
                    )}
                    style={{ width: `${Math.min(100, data.pct)}%` }}
                  />
                </div>
              </div>
              <span className="w-16 text-right text-gray-400 text-sm">
                {data.avg_populated.toFixed(1)}/{data.fields_total}
              </span>
              <span className={clsx(
                'w-16 text-right font-medium',
                data.pct >= 80 ? 'text-green-400' : data.pct >= 50 ? 'text-yellow-400' : 'text-red-400'
              )}>
                {data.pct.toFixed(0)}%
              </span>
            </div>
          ))}
        </div>

        {/* By Tier */}
        {completeness && Object.keys(completeness.by_tier).length > 0 && (
          <div className="mt-6 pt-4 border-t border-gray-700">
            <div className="flex items-center gap-4 text-sm">
              <span className="text-gray-400">By Tier:</span>
              {Object.entries(completeness.by_tier)
                .sort(([a], [b]) => Number(a) - Number(b))
                .map(([tier, data]) => (
                  <span key={tier} className="text-gray-300">
                    T{tier}: <span className={clsx(
                      data.avg_completeness_pct >= 70 ? 'text-green-400' : 'text-yellow-400'
                    )}>{data.avg_completeness_pct.toFixed(0)}%</span>
                  </span>
                ))}
            </div>
          </div>
        )}
      </div>

      {/* Recent Errors */}
      <div className="bg-gray-800 rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Recent Errors</h2>
          <span className={clsx(
            'px-3 py-1 rounded-full text-sm font-medium',
            (errors?.total || 0) === 0 ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'
          )}>
            {errors?.total || 0} errors
          </span>
        </div>

        {errorsLoading ? (
          <div className="text-gray-400">Loading errors...</div>
        ) : errors && errors.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-400">
                  <th className="pb-3 w-24">Time</th>
                  <th className="pb-3">Task</th>
                  <th className="pb-3 w-16">Tier</th>
                  <th className="pb-3">Error</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {errors.items.map((error) => (
                  <tr
                    key={error.id}
                    className={clsx(
                      'border-t border-gray-700 cursor-pointer hover:bg-gray-700/50',
                      selectedError === error.id && 'bg-gray-700'
                    )}
                    onClick={() => setSelectedError(selectedError === error.id ? null : error.id)}
                  >
                    <td className="py-3">
                      {new Date(error.timestamp).toLocaleTimeString('en-US', { hour12: false })}
                    </td>
                    <td className="py-3 font-mono text-xs">{error.task}</td>
                    <td className="py-3">{error.tier !== null ? `T${error.tier}` : '-'}</td>
                    <td className="py-3 text-red-400">
                      {error.error?.slice(0, 60)}{error.error && error.error.length > 60 ? '...' : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Error Details Modal */}
            {selectedError && (
              <div className="mt-4 p-4 bg-gray-900 rounded-lg">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-gray-400 text-sm">Error Details</span>
                  <button
                    onClick={() => setSelectedError(null)}
                    className="text-gray-400 hover:text-white"
                  >
                    Close
                  </button>
                </div>
                <pre className="text-xs text-red-400 overflow-x-auto whitespace-pre-wrap">
                  {errors.items.find(e => e.id === selectedError)?.traceback || 'No traceback available'}
                </pre>
              </div>
            )}
          </div>
        ) : (
          <p className="text-gray-400">No recent errors. System is running smoothly.</p>
        )}
      </div>
    </div>
  )
}
