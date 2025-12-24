import { useMemo } from 'react'
import {
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Line,
  ComposedChart,
  Legend,
} from 'recharts'
import { format } from 'date-fns'
import { useEquityCurve } from '../../hooks/useAnalystData'
import { TimeRange } from './TimeRangeSelector'

interface EquityCurveProps {
  timeRange: TimeRange
  strategy?: string
}

function rangeToDays(range: TimeRange): number {
  switch (range) {
    case '1d': return 1
    case '7d': return 7
    case '30d': return 30
    case '90d': return 90
    case 'all': return 365
  }
}

export default function EquityCurve({ timeRange, strategy }: EquityCurveProps) {
  const days = rangeToDays(timeRange)
  const { data, isLoading, error } = useEquityCurve(days, strategy)

  const chartData = useMemo(() => {
    if (!data) return []

    // Use the new chart_data field if available
    if (data.chart_data && Array.isArray(data.chart_data)) {
      return data.chart_data
    }

    // Fallback to total array
    if (data.total && Array.isArray(data.total)) {
      return data.total.map((point: any) => ({
        date: point.date,
        realized: point.value,
        unrealized: point.value,
        total: point.value,
        baseline: data.summary?.total_allocated || 1200,
      }))
    }

    return []
  }, [data])

  const summary = data?.summary

  if (isLoading) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <div className="h-6 bg-gray-700 rounded w-32 mb-4" />
        <div className="h-64 bg-gray-700 rounded animate-pulse" />
      </div>
    )
  }

  if (error || chartData.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <h3 className="text-white font-semibold mb-4">Equity Curve</h3>
        <div className="h-64 flex items-center justify-center">
          <p className="text-gray-500">No equity data available for this period</p>
        </div>
      </div>
    )
  }

  const baseline = chartData[0]?.baseline || 1200
  const currentValue = chartData[chartData.length - 1]?.unrealized || baseline
  const returnPct = ((currentValue - baseline) / baseline) * 100

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-white font-semibold">
          Equity Curve {strategy && <span className="text-gray-400 font-normal">({strategy})</span>}
        </h3>
        <div className="flex items-center gap-4 text-sm">
          <span className={returnPct >= 0 ? 'text-green-400' : 'text-red-400'}>
            {returnPct >= 0 ? '+' : ''}{returnPct.toFixed(2)}%
          </span>
        </div>
      </div>

      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData}>
            <XAxis
              dataKey="date"
              tickFormatter={(date) => format(new Date(date), timeRange === '1d' ? 'HH:mm' : 'MMM d')}
              tick={{ fontSize: 10, fill: '#9CA3AF' }}
              axisLine={{ stroke: '#374151' }}
              tickLine={{ stroke: '#374151' }}
            />
            <YAxis
              domain={['auto', 'auto']}
              tickFormatter={(value) => `$${value.toFixed(0)}`}
              tick={{ fontSize: 10, fill: '#9CA3AF' }}
              axisLine={{ stroke: '#374151' }}
              tickLine={{ stroke: '#374151' }}
              width={55}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#1F2937',
                border: '1px solid #374151',
                borderRadius: '8px',
                fontSize: '12px',
              }}
              labelFormatter={(date) => format(new Date(date), 'PPp')}
              formatter={(value: number, name: string) => {
                const labels: Record<string, string> = {
                  realized: 'Realized P&L',
                  unrealized: 'Total (incl. Unrealized)',
                  baseline: 'Baseline',
                }
                return [`$${value.toFixed(2)}`, labels[name] || name]
              }}
            />
            <Legend
              wrapperStyle={{ fontSize: '12px' }}
              formatter={(value) => {
                const labels: Record<string, string> = {
                  realized: 'Realized',
                  unrealized: 'Total (Unrealized)',
                }
                return <span className="text-gray-300">{labels[value] || value}</span>
              }}
            />

            {/* Baseline reference */}
            <ReferenceLine
              y={baseline}
              stroke="#4B5563"
              strokeDasharray="5 5"
            />

            {/* Realized P&L line (locked in profits/losses) */}
            <Line
              type="monotone"
              dataKey="realized"
              stroke="#3B82F6"
              strokeWidth={2}
              dot={{ r: 4, fill: '#3B82F6' }}
              activeDot={{ r: 6, fill: '#3B82F6' }}
            />

            {/* Total value including unrealized (current portfolio value) */}
            <Line
              type="monotone"
              dataKey="unrealized"
              stroke="#10B981"
              strokeWidth={2}
              dot={{ r: 4, fill: '#10B981' }}
              activeDot={{ r: 6, fill: '#10B981' }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Stats footer */}
      {summary && (
        <div className="grid grid-cols-4 gap-4 mt-4 pt-4 border-t border-gray-700">
          <div>
            <p className="text-gray-500 text-xs">Allocated</p>
            <p className="text-white font-mono text-sm">${summary.total_allocated?.toFixed(0)}</p>
          </div>
          <div>
            <p className="text-gray-500 text-xs">Realized P&L</p>
            <p className={`font-mono text-sm ${summary.total_realized >= 0 ? 'text-blue-400' : 'text-red-400'}`}>
              {summary.total_realized >= 0 ? '+' : ''}${summary.total_realized?.toFixed(2)}
            </p>
          </div>
          <div>
            <p className="text-gray-500 text-xs">Unrealized P&L</p>
            <p className={`font-mono text-sm ${summary.total_unrealized >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {summary.total_unrealized >= 0 ? '+' : ''}${summary.total_unrealized?.toFixed(2)}
            </p>
          </div>
          <div>
            <p className="text-gray-500 text-xs">Portfolio Value</p>
            <p className="text-white font-mono text-sm">${summary.portfolio_value?.toFixed(2)}</p>
          </div>
        </div>
      )}
    </div>
  )
}
