import { useParams, Link } from 'react-router-dom'
import { useMarket } from '../hooks/useData'
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, AreaChart, Area, BarChart, Bar } from 'recharts'
import { format } from 'date-fns'

export default function MarketDetail() {
  const { id } = useParams<{ id: string }>()
  const { data: market, isLoading } = useMarket(Number(id))

  if (isLoading) {
    return <div className="text-gray-400">Loading...</div>
  }

  if (!market) {
    return <div className="text-gray-400">Market not found</div>
  }

  const chartData = [...(market.recent_snapshots || [])]
    .reverse()
    .map((s) => ({
      time: format(new Date(s.timestamp), 'HH:mm'),
      price: s.price ? s.price * 100 : null,
      spread: s.spread ? s.spread * 100 : null,
      volume_24h: s.volume_24h || null,
      book_imbalance: s.book_imbalance || null,
      trade_count_1h: s.trade_count_1h || null,
      whale_count_1h: s.whale_count_1h || null,
      bid_depth: (s as any).bid_depth_10 || null,
      ask_depth: (s as any).ask_depth_10 || null,
    }))

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <Link to="/markets" className="text-indigo-400 hover:underline text-sm">
          &larr; Back to Markets
        </Link>
        <h1 className="text-2xl font-bold text-white mt-2">{market.question}</h1>
        {market.description && (
          <p className="text-gray-400 mt-2">{market.description}</p>
        )}
      </div>

      {/* Info Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-gray-800 rounded-lg p-4">
          <span className="text-gray-500 text-sm">Tier</span>
          <p className="text-xl font-bold text-white">T{market.tier}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <span className="text-gray-500 text-sm">Snapshots</span>
          <p className="text-xl font-bold text-white">{market.snapshot_count.toLocaleString()}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <span className="text-gray-500 text-sm">Hours to Close</span>
          <p className="text-xl font-bold text-white">
            {market.hours_to_close ? market.hours_to_close.toFixed(1) : 'N/A'}
          </p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <span className="text-gray-500 text-sm">Status</span>
          <p className="text-xl font-bold text-white">
            {market.resolved ? market.outcome : 'Active'}
          </p>
        </div>
      </div>

      {/* Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Price Chart */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Price (%)</h2>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <XAxis dataKey="time" stroke="#9CA3AF" fontSize={10} />
                <YAxis domain={[0, 100]} stroke="#9CA3AF" fontSize={10} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                  labelStyle={{ color: '#fff' }}
                />
                <Area
                  type="monotone"
                  dataKey="price"
                  stroke="#6366F1"
                  fill="#6366F1"
                  fillOpacity={0.3}
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Spread Chart */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Spread (%)</h2>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <XAxis dataKey="time" stroke="#9CA3AF" fontSize={10} />
                <YAxis stroke="#9CA3AF" fontSize={10} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                  labelStyle={{ color: '#fff' }}
                />
                <Line
                  type="monotone"
                  dataKey="spread"
                  stroke="#F59E0B"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Volume Chart */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">24h Volume ($)</h2>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <XAxis dataKey="time" stroke="#9CA3AF" fontSize={10} />
                <YAxis stroke="#9CA3AF" fontSize={10} tickFormatter={(v) => `$${(v/1000).toFixed(0)}k`} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                  labelStyle={{ color: '#fff' }}
                  formatter={(value: number) => [`$${value?.toLocaleString()}`, 'Volume']}
                />
                <Area
                  type="monotone"
                  dataKey="volume_24h"
                  stroke="#10B981"
                  fill="#10B981"
                  fillOpacity={0.3}
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Book Imbalance Chart */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Book Imbalance</h2>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <XAxis dataKey="time" stroke="#9CA3AF" fontSize={10} />
                <YAxis domain={[-1, 1]} stroke="#9CA3AF" fontSize={10} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                  labelStyle={{ color: '#fff' }}
                />
                <Line
                  type="monotone"
                  dataKey="book_imbalance"
                  stroke="#EC4899"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Trade Activity Chart */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Trade Activity (1h)</h2>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <XAxis dataKey="time" stroke="#9CA3AF" fontSize={10} />
                <YAxis stroke="#9CA3AF" fontSize={10} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                  labelStyle={{ color: '#fff' }}
                />
                <Bar dataKey="trade_count_1h" fill="#8B5CF6" name="Trades" />
                <Bar dataKey="whale_count_1h" fill="#EF4444" name="Whales" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Order Book Depth Chart */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Order Book Depth</h2>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <XAxis dataKey="time" stroke="#9CA3AF" fontSize={10} />
                <YAxis stroke="#9CA3AF" fontSize={10} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                  labelStyle={{ color: '#fff' }}
                />
                <Area
                  type="monotone"
                  dataKey="bid_depth"
                  stroke="#10B981"
                  fill="#10B981"
                  fillOpacity={0.3}
                  strokeWidth={2}
                  name="Bid Depth"
                />
                <Area
                  type="monotone"
                  dataKey="ask_depth"
                  stroke="#EF4444"
                  fill="#EF4444"
                  fillOpacity={0.3}
                  strokeWidth={2}
                  name="Ask Depth"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Recent Snapshots */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Recent Snapshots</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-400">
                <th className="pb-2">Time</th>
                <th className="pb-2">Price</th>
                <th className="pb-2">Spread</th>
                <th className="pb-2">Volume 24h</th>
                <th className="pb-2">Imbalance</th>
                <th className="pb-2">Trades 1h</th>
                <th className="pb-2">Whales 1h</th>
              </tr>
            </thead>
            <tbody className="text-gray-300">
              {market.recent_snapshots?.slice(0, 20).map((s) => (
                <tr key={s.id} className="border-t border-gray-700">
                  <td className="py-2">{format(new Date(s.timestamp), 'HH:mm:ss')}</td>
                  <td className="py-2">{s.price ? `${(s.price * 100).toFixed(1)}%` : 'N/A'}</td>
                  <td className="py-2">{s.spread ? `${(s.spread * 100).toFixed(2)}%` : 'N/A'}</td>
                  <td className="py-2">{s.volume_24h ? `$${s.volume_24h.toLocaleString()}` : 'N/A'}</td>
                  <td className="py-2">{s.book_imbalance?.toFixed(3) ?? 'N/A'}</td>
                  <td className="py-2">{s.trade_count_1h ?? 'N/A'}</td>
                  <td className="py-2">{s.whale_count_1h ?? 'N/A'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
