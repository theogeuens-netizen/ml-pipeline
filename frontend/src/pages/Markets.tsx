import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useMarkets } from '../hooks/useData'
import { formatDistanceToNow } from 'date-fns'

// Debounce hook
function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])

  return debouncedValue
}

export default function Markets() {
  const [tier, setTier] = useState<number | undefined>(undefined)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const limit = 20

  // Debounce search by 300ms to avoid API calls on every keystroke
  const debouncedSearch = useDebounce(search, 300)

  // Reset page when debounced search changes
  useEffect(() => {
    setPage(0)
  }, [debouncedSearch])

  const { data, isLoading } = useMarkets({
    tier,
    active: true,
    resolved: false,
    search: debouncedSearch || undefined,
    limit,
    offset: page * limit,
  })

  const totalPages = data ? Math.ceil(data.total / limit) : 0

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Markets</h1>

      {/* Filters */}
      <div className="flex flex-wrap gap-4">
        <select
          value={tier ?? ''}
          onChange={(e) => {
            setTier(e.target.value ? Number(e.target.value) : undefined)
            setPage(0)
          }}
          className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-gray-300"
        >
          <option value="">All Tiers</option>
          <option value="0">Tier 0 (&gt;48h)</option>
          <option value="1">Tier 1 (12-48h)</option>
          <option value="2">Tier 2 (4-12h)</option>
          <option value="3">Tier 3 (1-4h)</option>
          <option value="4">Tier 4 (&lt;1h)</option>
        </select>

        <input
          type="text"
          placeholder="Search markets..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-gray-300 w-64"
        />

        <span className="text-gray-500 self-center">
          {data?.total ?? 0} markets
        </span>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="text-gray-400">Loading...</div>
      ) : (
        <div className="bg-gray-800 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-900">
              <tr className="text-left text-gray-400 text-sm">
                <th className="p-4">Question</th>
                <th className="p-4 w-20">Price</th>
                <th className="p-4 w-16">Tier</th>
                <th className="p-4 w-24">Snapshots</th>
                <th className="p-4 w-32">Last Update</th>
              </tr>
            </thead>
            <tbody className="text-gray-300">
              {data?.items.map((market) => (
                <tr key={market.id} className="border-t border-gray-700 hover:bg-gray-750">
                  <td className="p-4">
                    <Link
                      to={`/markets/${market.id}`}
                      className="hover:text-indigo-400"
                    >
                      {market.question.length > 80
                        ? market.question.slice(0, 80) + '...'
                        : market.question}
                    </Link>
                    {market.category && (
                      <span className="ml-2 text-xs text-gray-500">{market.category}</span>
                    )}
                  </td>
                  <td className="p-4">
                    {market.initial_price
                      ? `${(market.initial_price * 100).toFixed(1)}%`
                      : 'N/A'}
                  </td>
                  <td className="p-4">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                      market.tier === 4 ? 'bg-red-900 text-red-300' :
                      market.tier === 3 ? 'bg-orange-900 text-orange-300' :
                      market.tier === 2 ? 'bg-yellow-900 text-yellow-300' :
                      market.tier === 1 ? 'bg-blue-900 text-blue-300' :
                      'bg-gray-700 text-gray-300'
                    }`}>
                      T{market.tier}
                    </span>
                  </td>
                  <td className="p-4 text-sm">{market.snapshot_count.toLocaleString()}</td>
                  <td className="p-4 text-sm text-gray-500">
                    {market.last_snapshot_at
                      ? formatDistanceToNow(new Date(market.last_snapshot_at), { addSuffix: true })
                      : 'Never'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0}
            className="px-3 py-1 rounded bg-gray-700 text-gray-300 disabled:opacity-50"
          >
            Previous
          </button>
          <span className="text-gray-400">
            Page {page + 1} of {totalPages}
          </span>
          <button
            onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
            disabled={page >= totalPages - 1}
            className="px-3 py-1 rounded bg-gray-700 text-gray-300 disabled:opacity-50"
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
