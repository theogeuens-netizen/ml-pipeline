import { useCoverage, useGaps } from '../hooks/useData'
import { Link } from 'react-router-dom'

export default function DataQuality() {
  const { data: coverage, isLoading: coverageLoading } = useCoverage()
  const { data: gaps, isLoading: gapsLoading } = useGaps()

  if (coverageLoading || gapsLoading) {
    return <div className="text-gray-400">Loading...</div>
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-white">Data Quality</h1>

      {/* Overall Coverage */}
      <div className="bg-gray-800 rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Collection Coverage</h2>
          <span className={`text-2xl font-bold ${(coverage?.overall_coverage_pct ?? 0) >= 95 ? 'text-green-400' : 'text-yellow-400'}`}>
            {coverage?.overall_coverage_pct?.toFixed(1) ?? 0}%
          </span>
        </div>
        <p className="text-gray-400 text-sm mb-6">
          Comparing expected vs actual snapshots in the last hour
        </p>

        <div className="space-y-4">
          {coverage && Object.entries(coverage.by_tier).map(([tier, data]) => (
            <div key={tier} className="flex items-center gap-4">
              <span className="w-16 text-gray-400">{tier.replace('_', ' ').toUpperCase()}</span>
              <div className="flex-1">
                <div className="h-4 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className={`h-full ${data.coverage_pct >= 95 ? 'bg-green-500' : data.coverage_pct >= 80 ? 'bg-yellow-500' : 'bg-red-500'}`}
                    style={{ width: `${Math.min(100, data.coverage_pct)}%` }}
                  />
                </div>
              </div>
              <span className="w-32 text-right text-gray-400 text-sm">
                {data.actual_per_hour}/{data.expected_per_hour}
              </span>
              <span className={`w-16 text-right ${data.coverage_pct >= 95 ? 'text-green-400' : 'text-yellow-400'}`}>
                {data.coverage_pct.toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Gaps */}
      <div className="bg-gray-800 rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Data Gaps</h2>
          <span className={`px-3 py-1 rounded-full text-sm font-medium ${
            (gaps?.gap_count ?? 0) === 0 ? 'bg-green-900 text-green-300' : 'bg-yellow-900 text-yellow-300'
          }`}>
            {gaps?.gap_count ?? 0} gaps detected
          </span>
        </div>

        {gaps && gaps.gap_count > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-400">
                  <th className="pb-3">Market</th>
                  <th className="pb-3 w-16">Tier</th>
                  <th className="pb-3 w-32">Last Snapshot</th>
                  <th className="pb-3 w-32">Expected Interval</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {gaps.gaps.map((gap) => (
                  <tr key={gap.market_id} className="border-t border-gray-700">
                    <td className="py-3">
                      <Link
                        to={`/markets/${gap.market_id}`}
                        className="hover:text-indigo-400"
                      >
                        {gap.question?.slice(0, 60)}...
                      </Link>
                    </td>
                    <td className="py-3">T{gap.tier}</td>
                    <td className="py-3">
                      {gap.seconds_since_last
                        ? `${Math.floor(gap.seconds_since_last / 60)}m ago`
                        : 'Never'}
                    </td>
                    <td className="py-3">{gap.expected_interval}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-gray-400">No data gaps detected. All markets are up to date.</p>
        )}
      </div>
    </div>
  )
}
