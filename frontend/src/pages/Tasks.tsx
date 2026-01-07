import { useTaskStatus, useTaskRuns } from '../hooks/useData'
import { formatDistanceToNow, format } from 'date-fns'

export default function Tasks() {
  const { data: status, isLoading: statusLoading } = useTaskStatus()
  const { data: runs, isLoading: runsLoading } = useTaskRuns({ limit: 50 })

  if (statusLoading) {
    return <div className="text-gray-400">Loading...</div>
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-white">Task Monitoring</h1>

      {/* Task Status */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Task Status</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-400">
                <th className="pb-3">Task</th>
                <th className="pb-3">Last Run</th>
                <th className="pb-3">Status</th>
                <th className="pb-3">Runs (24h)</th>
                <th className="pb-3">Success Rate</th>
                <th className="pb-3">Avg Duration</th>
              </tr>
            </thead>
            <tbody className="text-gray-300">
              {status && Object.entries(status.tasks).map(([name, data]) => (
                <tr key={name} className="border-t border-gray-700">
                  <td className="py-3 font-mono">{name.split('.').pop()}</td>
                  <td className="py-3">
                    {data.last_run
                      ? formatDistanceToNow(new Date(data.last_run), { addSuffix: true })
                      : 'Never'}
                  </td>
                  <td className="py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                      data.last_status === 'success' ? 'bg-green-900 text-green-300' :
                      data.last_status === 'failed' ? 'bg-red-900 text-red-300' :
                      data.last_status === 'running' ? 'bg-blue-900 text-blue-300' :
                      'bg-gray-700 text-gray-300'
                    }`}>
                      {data.last_status ?? 'N/A'}
                    </span>
                  </td>
                  <td className="py-3">{data.runs_24h}</td>
                  <td className="py-3">
                    <span className={data.success_rate_24h >= 95 ? 'text-green-400' : 'text-yellow-400'}>
                      {data.success_rate_24h.toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-3">
                    {data.avg_duration_ms ? `${(data.avg_duration_ms / 1000).toFixed(1)}s` : 'N/A'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Runs */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Recent Task Runs</h2>
        {runsLoading ? (
          <div className="text-gray-400">Loading...</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-400">
                  <th className="pb-3">Time</th>
                  <th className="pb-3">Task</th>
                  <th className="pb-3">Tier</th>
                  <th className="pb-3">Status</th>
                  <th className="pb-3">Duration</th>
                  <th className="pb-3">Markets</th>
                  <th className="pb-3">Rows</th>
                  <th className="pb-3">Error</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {runs?.items.map((run) => (
                  <tr key={run.id} className="border-t border-gray-700">
                    <td className="py-2">{format(new Date(run.started_at), 'HH:mm:ss')}</td>
                    <td className="py-2 font-mono text-xs">{run.task_name.split('.').pop()}</td>
                    <td className="py-2">{run.tier !== null ? `T${run.tier}` : '-'}</td>
                    <td className="py-2">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                        run.status === 'success' ? 'bg-green-900 text-green-300' :
                        run.status === 'failed' ? 'bg-red-900 text-red-300' :
                        'bg-blue-900 text-blue-300'
                      }`}>
                        {run.status}
                      </span>
                    </td>
                    <td className="py-2">
                      {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : '-'}
                    </td>
                    <td className="py-2">{run.markets_processed ?? '-'}</td>
                    <td className="py-2">{run.rows_inserted ?? '-'}</td>
                    <td className="py-2 max-w-xs truncate text-red-400">
                      {run.error_message?.slice(0, 50)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
