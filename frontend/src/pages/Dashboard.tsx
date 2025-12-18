import { useStats, useHealth, useCoverage, useTaskStatus } from '../hooks/useData'
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts'
import { formatDistanceToNow } from 'date-fns'

function StatCard({ title, value, subtitle }: { title: string; value: string | number; subtitle?: string }) {
  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <h3 className="text-gray-400 text-sm font-medium">{title}</h3>
      <p className="mt-2 text-3xl font-bold text-white">{value}</p>
      {subtitle && <p className="mt-1 text-sm text-gray-500">{subtitle}</p>}
    </div>
  )
}

export default function Dashboard() {
  const { data: stats, isLoading: statsLoading } = useStats()
  const { data: health } = useHealth()
  const { data: coverage } = useCoverage()
  const { data: taskStatus } = useTaskStatus()

  if (statsLoading) {
    return <div className="text-gray-400">Loading...</div>
  }

  const tierData = stats ? [
    { name: 'T0', count: stats.markets.tier_0 },
    { name: 'T1', count: stats.markets.tier_1 },
    { name: 'T2', count: stats.markets.tier_2 },
    { name: 'T3', count: stats.markets.tier_3 },
    { name: 'T4', count: stats.markets.tier_4 },
  ] : []

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Dashboard</h1>
        <div className="flex items-center gap-2">
          <div className={`w-3 h-3 rounded-full ${health?.status === 'healthy' ? 'bg-green-500' : 'bg-yellow-500'}`} />
          <span className="text-sm text-gray-400">
            {health?.status === 'healthy' ? 'All Systems Operational' : 'Degraded'}
          </span>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="Markets Tracked"
          value={stats?.markets.total_tracked ?? 0}
          subtitle={`${stats?.markets.resolved ?? 0} resolved`}
        />
        <StatCard
          title="Snapshots Today"
          value={stats?.snapshots.today?.toLocaleString() ?? 0}
          subtitle={`${stats?.snapshots.total?.toLocaleString() ?? 0} total`}
        />
        <StatCard
          title="Trades Today"
          value={stats?.trades.today?.toLocaleString() ?? 0}
          subtitle={`${stats?.trades.total?.toLocaleString() ?? 0} total`}
        />
        <StatCard
          title="Database Size"
          value={stats?.database.size ?? 'N/A'}
          subtitle={`${stats?.websocket.connected_markets ?? 0} WS connected`}
        />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Markets by Tier */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Markets by Tier</h2>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={tierData}>
                <XAxis dataKey="name" stroke="#9CA3AF" />
                <YAxis stroke="#9CA3AF" />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                  labelStyle={{ color: '#fff' }}
                />
                <Bar dataKey="count" fill="#6366F1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Coverage */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">
            Collection Coverage
            <span className="ml-2 text-sm font-normal text-gray-400">(last hour)</span>
          </h2>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-gray-400">Overall</span>
              <span className={`font-bold ${(coverage?.overall_coverage_pct ?? 0) >= 95 ? 'text-green-400' : 'text-yellow-400'}`}>
                {coverage?.overall_coverage_pct?.toFixed(1) ?? 0}%
              </span>
            </div>
            {coverage && Object.entries(coverage.by_tier).map(([tier, data]) => (
              <div key={tier} className="flex items-center justify-between text-sm">
                <span className="text-gray-500">{tier.replace('_', ' ').toUpperCase()}</span>
                <div className="flex items-center gap-4">
                  <span className="text-gray-500">{data.actual_per_hour}/{data.expected_per_hour}</span>
                  <span className={data.coverage_pct >= 95 ? 'text-green-400' : 'text-yellow-400'}>
                    {data.coverage_pct.toFixed(1)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Task Health */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Task Health</h2>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-gray-400 text-sm">
                <th className="pb-3">Task</th>
                <th className="pb-3">Last Run</th>
                <th className="pb-3">Status</th>
                <th className="pb-3">Runs (24h)</th>
                <th className="pb-3">Success Rate</th>
                <th className="pb-3">Avg Duration</th>
              </tr>
            </thead>
            <tbody className="text-gray-300">
              {taskStatus && Object.entries(taskStatus.tasks).map(([name, data]) => (
                <tr key={name} className="border-t border-gray-700">
                  <td className="py-3 font-mono text-sm">{name.split('.').pop()}</td>
                  <td className="py-3 text-sm">
                    {data.last_run
                      ? formatDistanceToNow(new Date(data.last_run), { addSuffix: true })
                      : 'Never'}
                  </td>
                  <td className="py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                      data.last_status === 'success' ? 'bg-green-900 text-green-300' :
                      data.last_status === 'failed' ? 'bg-red-900 text-red-300' :
                      'bg-gray-700 text-gray-300'
                    }`}>
                      {data.last_status ?? 'N/A'}
                    </span>
                  </td>
                  <td className="py-3 text-sm">{data.runs_24h}</td>
                  <td className="py-3 text-sm">
                    <span className={data.success_rate_24h >= 95 ? 'text-green-400' : 'text-yellow-400'}>
                      {data.success_rate_24h.toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-3 text-sm">
                    {data.avg_duration_ms ? `${(data.avg_duration_ms / 1000).toFixed(1)}s` : 'N/A'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
