import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  CartesianGrid
} from 'recharts'
import { ChartDataPoint } from '../../utils/payoff-math'

interface PayoffChartProps {
  data: ChartDataPoint[]
  breakEvenPrices: number[]
}

export function PayoffChart({ data, breakEvenPrices }: PayoffChartProps) {
  // Split data into profit and loss segments for different fills
  const profitData = data.map(d => ({
    ...d,
    profit: d.pnl > 0 ? d.pnl : 0,
    loss: d.pnl < 0 ? d.pnl : 0
  }))

  // Calculate Y-axis domain with some padding
  const minPnl = Math.min(...data.map(d => d.pnl))
  const maxPnl = Math.max(...data.map(d => d.pnl))
  const padding = Math.max(Math.abs(maxPnl - minPnl) * 0.1, 10)
  const yMin = Math.floor(minPnl - padding)
  const yMax = Math.ceil(maxPnl + padding)

  // Custom tooltip
  const CustomTooltip = ({ active, payload, label }: {
    active?: boolean
    payload?: Array<{ value: number }>
    label?: number
  }) => {
    if (!active || !payload || payload.length === 0) return null

    const pnl = data.find(d => d.price === label)?.pnl ?? 0
    const pnlColor = pnl >= 0 ? 'text-green-400' : 'text-red-400'
    const sign = pnl >= 0 ? '+' : ''

    return (
      <div className="bg-gray-800 border border-gray-700 rounded px-3 py-2 shadow-lg">
        <div className="text-xs text-gray-400">
          Resolution Price: {(label ?? 0).toFixed(2)}
        </div>
        <div className={`text-sm font-semibold ${pnlColor}`}>
          P&L: {sign}${pnl.toFixed(2)}
        </div>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-300 mb-4">Payoff Diagram</h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={profitData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
            <defs>
              <linearGradient id="profitGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#22C55E" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#22C55E" stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="lossGradient" x1="0" y1="1" x2="0" y2="0">
                <stop offset="0%" stopColor="#EF4444" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#EF4444" stopOpacity={0.05} />
              </linearGradient>
            </defs>

            <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.5} />

            <XAxis
              dataKey="price"
              stroke="#9CA3AF"
              fontSize={10}
              tickFormatter={(v) => v.toFixed(1)}
              domain={[0, 1]}
              ticks={[0, 0.2, 0.4, 0.6, 0.8, 1.0]}
            />
            <YAxis
              stroke="#9CA3AF"
              fontSize={10}
              tickFormatter={(v) => `$${v}`}
              domain={[yMin, yMax]}
            />

            <Tooltip content={<CustomTooltip />} />

            {/* Zero line */}
            <ReferenceLine
              y={0}
              stroke="#6B7280"
              strokeDasharray="4 4"
              strokeWidth={1}
            />

            {/* Break-even markers */}
            {breakEvenPrices.map((price, i) => (
              <ReferenceLine
                key={i}
                x={price}
                stroke="#F59E0B"
                strokeDasharray="4 4"
                strokeWidth={1}
                label={{
                  value: `BE: ${price.toFixed(2)}`,
                  position: 'top',
                  fill: '#F59E0B',
                  fontSize: 10
                }}
              />
            ))}

            {/* Profit area (above zero) */}
            <Area
              type="monotone"
              dataKey="profit"
              stroke="none"
              fill="url(#profitGradient)"
              fillOpacity={1}
            />

            {/* Loss area (below zero) */}
            <Area
              type="monotone"
              dataKey="loss"
              stroke="none"
              fill="url(#lossGradient)"
              fillOpacity={1}
            />

            {/* Main P&L line */}
            <Line
              type="monotone"
              dataKey="pnl"
              stroke="#6366F1"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: '#6366F1', stroke: '#fff', strokeWidth: 2 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Legend */}
      <div className="flex items-center justify-center gap-6 mt-4 text-xs text-gray-400">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded bg-indigo-500"></div>
          <span>P&L</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded bg-green-500/40"></div>
          <span>Profit Zone</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded bg-red-500/40"></div>
          <span>Loss Zone</span>
        </div>
        {breakEvenPrices.length > 0 && (
          <div className="flex items-center gap-2">
            <div className="w-3 h-0.5 bg-amber-500"></div>
            <span>Break-even</span>
          </div>
        )}
      </div>
    </div>
  )
}
