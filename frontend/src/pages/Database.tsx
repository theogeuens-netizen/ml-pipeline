import { useState } from 'react'
import { useTables, useTableData } from '../hooks/useData'
import { clsx } from 'clsx'

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '-'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  if (typeof value === 'number') {
    // Format numbers nicely
    if (Number.isInteger(value)) {
      return value.toLocaleString()
    }
    return value.toFixed(6)
  }
  return String(value)
}

function formatTimestamp(value: string): string {
  const date = new Date(value)
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export default function Database() {
  const { data: tablesData, isLoading: tablesLoading } = useTables()
  const [selectedTable, setSelectedTable] = useState('snapshots')
  const [page, setPage] = useState(0)
  const [orderBy, setOrderBy] = useState('id')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [selectedRow, setSelectedRow] = useState<Record<string, unknown> | null>(null)

  const limit = 100
  const { data: tableData, isLoading: tableLoading } = useTableData(selectedTable, {
    limit,
    offset: page * limit,
    order_by: orderBy,
    order,
  })

  if (tablesLoading) {
    return <div className="text-gray-400">Loading...</div>
  }

  const tables = tablesData?.tables || []
  const totalPages = Math.ceil((tableData?.total || 0) / limit)

  const handleSort = (column: string) => {
    if (column === orderBy) {
      setOrder(order === 'asc' ? 'desc' : 'asc')
    } else {
      setOrderBy(column)
      setOrder('desc')
    }
    setPage(0)
  }

  const handleTableChange = (table: string) => {
    setSelectedTable(table)
    setPage(0)
    setOrderBy('id')
    setOrder('desc')
    setSelectedRow(null)
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Database Browser</h1>

      {/* Table Selection and Controls */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="text-gray-400 text-sm">Table:</label>
          <select
            value={selectedTable}
            onChange={(e) => handleTableChange(e.target.value)}
            className="bg-gray-700 text-white rounded px-3 py-2 border border-gray-600 focus:border-indigo-500 focus:outline-none"
          >
            {tables.map((table) => (
              <option key={table.name} value={table.name}>
                {table.name} ({table.row_count.toLocaleString()} rows)
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-gray-400 text-sm">Order by:</label>
          <select
            value={orderBy}
            onChange={(e) => { setOrderBy(e.target.value); setPage(0); }}
            className="bg-gray-700 text-white rounded px-3 py-2 border border-gray-600 focus:border-indigo-500 focus:outline-none"
          >
            {tableData?.columns.map((col) => (
              <option key={col} value={col}>{col}</option>
            ))}
          </select>
          <button
            onClick={() => setOrder(order === 'asc' ? 'desc' : 'asc')}
            className="bg-gray-700 text-white rounded px-3 py-2 border border-gray-600 hover:bg-gray-600"
          >
            {order === 'desc' ? 'DESC' : 'ASC'}
          </button>
        </div>
      </div>

      {/* Data Table */}
      <div className="bg-gray-800 rounded-lg overflow-hidden">
        {tableLoading ? (
          <div className="p-6 text-gray-400">Loading table data...</div>
        ) : tableData && tableData.items.length > 0 ? (
          <>
            {/* Table Info */}
            <div className="px-4 py-3 border-b border-gray-700 text-gray-400 text-sm">
              Showing {page * limit + 1}-{Math.min((page + 1) * limit, tableData.total)} of {tableData.total.toLocaleString()} rows
            </div>

            {/* Scrollable Table */}
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-400 bg-gray-900">
                    {tableData.columns.map((col) => (
                      <th
                        key={col}
                        className={clsx(
                          'px-4 py-3 cursor-pointer hover:text-white whitespace-nowrap',
                          col === orderBy && 'text-indigo-400'
                        )}
                        onClick={() => handleSort(col)}
                      >
                        {col}
                        {col === orderBy && (
                          <span className="ml-1">{order === 'desc' ? '↓' : '↑'}</span>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="text-gray-300">
                  {tableData.items.map((row, idx) => (
                    <tr
                      key={idx}
                      className={clsx(
                        'border-t border-gray-700 cursor-pointer hover:bg-gray-700/50',
                        selectedRow === row && 'bg-gray-700'
                      )}
                      onClick={() => setSelectedRow(selectedRow === row ? null : row)}
                    >
                      {tableData.columns.map((col) => {
                        const value = row[col]
                        const isTimestamp = col.includes('timestamp') || col.includes('_at') || col === 'started_at' || col === 'completed_at'
                        return (
                          <td key={col} className="px-4 py-3 whitespace-nowrap">
                            {isTimestamp && typeof value === 'string'
                              ? formatTimestamp(value)
                              : formatValue(value).slice(0, 50)}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="px-4 py-3 border-t border-gray-700 flex items-center justify-between">
              <button
                onClick={() => setPage(Math.max(0, page - 1))}
                disabled={page === 0}
                className={clsx(
                  'px-4 py-2 rounded',
                  page === 0
                    ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    : 'bg-indigo-600 text-white hover:bg-indigo-500'
                )}
              >
                Previous
              </button>
              <span className="text-gray-400">
                Page {page + 1} of {totalPages}
              </span>
              <button
                onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                disabled={page >= totalPages - 1}
                className={clsx(
                  'px-4 py-2 rounded',
                  page >= totalPages - 1
                    ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    : 'bg-indigo-600 text-white hover:bg-indigo-500'
                )}
              >
                Next
              </button>
            </div>
          </>
        ) : (
          <div className="p-6 text-gray-400">No data in this table.</div>
        )}
      </div>

      {/* Row Details Modal */}
      {selectedRow && (
        <div className="bg-gray-800 rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Row Details</h2>
            <button
              onClick={() => setSelectedRow(null)}
              className="text-gray-400 hover:text-white"
            >
              Close
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 max-h-96 overflow-y-auto">
            {tableData?.columns.map((col) => {
              const value = selectedRow[col]
              const isTimestamp = col.includes('timestamp') || col.includes('_at')
              return (
                <div key={col} className="bg-gray-900 rounded p-3">
                  <div className="text-gray-400 text-xs mb-1">{col}</div>
                  <div className="text-gray-200 text-sm font-mono break-all">
                    {isTimestamp && typeof value === 'string'
                      ? new Date(value).toLocaleString()
                      : value === null
                        ? <span className="text-gray-500">null</span>
                        : typeof value === 'object'
                          ? <pre className="text-xs overflow-x-auto">{JSON.stringify(value, null, 2)}</pre>
                          : formatValue(value)}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
