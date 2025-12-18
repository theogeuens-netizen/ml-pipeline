import { useState } from 'react'
import { clsx } from 'clsx'
import { formatDistanceToNow } from 'date-fns'
import {
  useExecutorStatus,
  usePositions,
  useSignals,
  useTrades,
  useStrategies,
  useClosePosition,
  useEnableStrategy,
  useSetTradingMode,
  useResetPaperTrading,
  useWalletStatus,
  useSyncWallet,
} from '../hooks/useExecutorData'

// Stat card component
function StatCard({ title, value, subtitle, valueColor }: {
  title: string
  value: string | number
  subtitle?: string
  valueColor?: string
}) {
  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-gray-400 text-sm font-medium">{title}</h3>
      <p className={clsx('mt-1 text-2xl font-bold', valueColor || 'text-white')}>{value}</p>
      {subtitle && <p className="mt-1 text-xs text-gray-500">{subtitle}</p>}
    </div>
  )
}

// Tab button component
function TabButton({ active, onClick, children }: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'px-4 py-2 text-sm font-medium rounded-t-lg',
        active
          ? 'bg-gray-800 text-white border-b-2 border-indigo-500'
          : 'bg-gray-900 text-gray-400 hover:text-gray-200'
      )}
    >
      {children}
    </button>
  )
}

// Positions tab
function PositionsTab() {
  const { data, isLoading } = usePositions({ status: 'open', limit: 50 })
  const closeMutation = useClosePosition()

  if (isLoading) return <div className="text-gray-400 p-4">Loading positions...</div>

  const positions = data?.items || []

  if (positions.length === 0) {
    return <div className="text-gray-500 p-8 text-center">No open positions</div>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="text-left text-gray-400 text-sm border-b border-gray-700">
            <th className="pb-3 px-4">Strategy</th>
            <th className="pb-3 px-4">Market</th>
            <th className="pb-3 px-4">Side</th>
            <th className="pb-3 px-4">Entry</th>
            <th className="pb-3 px-4">Current</th>
            <th className="pb-3 px-4">Size</th>
            <th className="pb-3 px-4">P&L</th>
            <th className="pb-3 px-4">Action</th>
          </tr>
        </thead>
        <tbody className="text-gray-300">
          {positions.map((pos) => (
            <tr key={pos.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
              <td className="py-3 px-4 font-mono text-sm">{pos.strategy_name}</td>
              <td className="py-3 px-4 text-sm">{pos.market_id}</td>
              <td className="py-3 px-4">
                <span className={clsx(
                  'px-2 py-0.5 rounded text-xs font-medium',
                  pos.side === 'BUY' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'
                )}>
                  {pos.side}
                </span>
              </td>
              <td className="py-3 px-4 text-sm">${pos.entry_price?.toFixed(4) ?? '-'}</td>
              <td className="py-3 px-4 text-sm">${pos.current_price?.toFixed(4) ?? '-'}</td>
              <td className="py-3 px-4 text-sm">{pos.size_shares?.toFixed(2) ?? '-'}</td>
              <td className={clsx(
                'py-3 px-4 text-sm font-medium',
                (pos.unrealized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
              )}>
                ${pos.unrealized_pnl?.toFixed(2) ?? '0.00'}
                {pos.unrealized_pnl_pct !== null && (
                  <span className="text-xs ml-1">
                    ({(pos.unrealized_pnl_pct * 100).toFixed(1)}%)
                  </span>
                )}
              </td>
              <td className="py-3 px-4">
                <button
                  onClick={() => closeMutation.mutate({ positionId: pos.id, exitPrice: pos.current_price ?? undefined })}
                  disabled={closeMutation.isPending}
                  className="px-2 py-1 bg-red-600 hover:bg-red-700 rounded text-xs font-medium disabled:opacity-50"
                >
                  Close
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Signals tab
function SignalsTab() {
  const { data, isLoading } = useSignals({ limit: 50 })

  if (isLoading) return <div className="text-gray-400 p-4">Loading signals...</div>

  const signals = data?.items || []

  if (signals.length === 0) {
    return <div className="text-gray-500 p-8 text-center">No signals yet</div>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="text-left text-gray-400 text-sm border-b border-gray-700">
            <th className="pb-3 px-4">Time</th>
            <th className="pb-3 px-4">Strategy</th>
            <th className="pb-3 px-4">Market</th>
            <th className="pb-3 px-4">Side</th>
            <th className="pb-3 px-4">Edge</th>
            <th className="pb-3 px-4">Confidence</th>
            <th className="pb-3 px-4">Status</th>
            <th className="pb-3 px-4">Reason</th>
          </tr>
        </thead>
        <tbody className="text-gray-300">
          {signals.map((sig) => (
            <tr key={sig.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
              <td className="py-3 px-4 text-sm text-gray-500">
                {sig.created_at ? formatDistanceToNow(new Date(sig.created_at), { addSuffix: true }) : '-'}
              </td>
              <td className="py-3 px-4 font-mono text-sm">{sig.strategy_name}</td>
              <td className="py-3 px-4 text-sm">{sig.market_id}</td>
              <td className="py-3 px-4">
                <span className={clsx(
                  'px-2 py-0.5 rounded text-xs font-medium',
                  sig.side === 'BUY' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'
                )}>
                  {sig.side}
                </span>
              </td>
              <td className="py-3 px-4 text-sm">{sig.edge ? (sig.edge * 100).toFixed(1) + '%' : '-'}</td>
              <td className="py-3 px-4 text-sm">{sig.confidence ? (sig.confidence * 100).toFixed(0) + '%' : '-'}</td>
              <td className="py-3 px-4">
                <span className={clsx(
                  'px-2 py-0.5 rounded text-xs font-medium',
                  sig.status === 'executed' ? 'bg-green-900 text-green-300' :
                  sig.status === 'approved' ? 'bg-blue-900 text-blue-300' :
                  sig.status === 'rejected' ? 'bg-red-900 text-red-300' :
                  'bg-gray-700 text-gray-300'
                )}>
                  {sig.status}
                </span>
              </td>
              <td className="py-3 px-4 text-sm text-gray-400 max-w-xs truncate" title={sig.reason}>
                {sig.reason}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Trades tab
function TradesTab() {
  const { data, isLoading } = useTrades({ limit: 50 })

  if (isLoading) return <div className="text-gray-400 p-4">Loading trades...</div>

  const trades = data?.items || []

  if (trades.length === 0) {
    return <div className="text-gray-500 p-8 text-center">No trades yet</div>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="text-left text-gray-400 text-sm border-b border-gray-700">
            <th className="pb-3 px-4">Time</th>
            <th className="pb-3 px-4">Side</th>
            <th className="pb-3 px-4">Price</th>
            <th className="pb-3 px-4">Shares</th>
            <th className="pb-3 px-4">USD</th>
            <th className="pb-3 px-4">Type</th>
          </tr>
        </thead>
        <tbody className="text-gray-300">
          {trades.map((trade) => (
            <tr key={trade.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
              <td className="py-3 px-4 text-sm text-gray-500">
                {trade.executed_at ? formatDistanceToNow(new Date(trade.executed_at), { addSuffix: true }) : '-'}
              </td>
              <td className="py-3 px-4">
                <span className={clsx(
                  'px-2 py-0.5 rounded text-xs font-medium',
                  trade.side === 'BUY' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'
                )}>
                  {trade.side}
                </span>
              </td>
              <td className="py-3 px-4 text-sm">${trade.price?.toFixed(4) ?? '-'}</td>
              <td className="py-3 px-4 text-sm">{trade.size_shares?.toFixed(2) ?? '-'}</td>
              <td className="py-3 px-4 text-sm">${trade.size_usd?.toFixed(2) ?? '-'}</td>
              <td className="py-3 px-4">
                <span className={clsx(
                  'px-2 py-0.5 rounded text-xs font-medium',
                  trade.is_paper ? 'bg-yellow-900 text-yellow-300' : 'bg-purple-900 text-purple-300'
                )}>
                  {trade.is_paper ? 'Paper' : 'Live'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Strategies tab
function StrategiesTab() {
  const { data, isLoading } = useStrategies()
  const enableMutation = useEnableStrategy()

  if (isLoading) return <div className="text-gray-400 p-4">Loading strategies...</div>

  const strategies = data?.items || []

  return (
    <div className="space-y-4 p-4">
      {strategies.map((strategy) => (
        <div key={strategy.name} className="bg-gray-700/50 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-white font-medium">{strategy.name}</h3>
              <p className="text-gray-400 text-sm mt-1">{strategy.description}</p>
            </div>
            <div className="flex items-center gap-4">
              <span className="text-gray-500 text-sm">v{strategy.version}</span>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={strategy.enabled}
                  onChange={(e) => enableMutation.mutate({
                    name: strategy.name,
                    enabled: e.target.checked
                  })}
                  className="sr-only peer"
                  disabled={enableMutation.isPending}
                />
                <div className="w-11 h-6 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-600"></div>
              </label>
            </div>
          </div>
          {strategy.params && Object.keys(strategy.params).length > 0 && (
            <div className="mt-3 pt-3 border-t border-gray-600">
              <h4 className="text-gray-400 text-xs font-medium mb-2">Parameters</h4>
              <div className="flex flex-wrap gap-2">
                {Object.entries(strategy.params).map(([key, value]) => (
                  <span key={key} className="px-2 py-1 bg-gray-600 rounded text-xs text-gray-300">
                    {key}: {String(value)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// Wallet tab (Live Polymarket wallet)
function WalletTab() {
  const { data: wallet, isLoading, error } = useWalletStatus()
  const syncMutation = useSyncWallet()

  if (isLoading) return <div className="text-gray-400 p-4">Loading wallet...</div>

  if (error || !wallet?.success) {
    return (
      <div className="text-red-400 p-8 text-center">
        <p>Failed to connect to Polymarket wallet</p>
        <p className="text-sm text-gray-500 mt-2">{wallet?.error || 'Check API configuration'}</p>
      </div>
    )
  }

  return (
    <div className="p-4 space-y-6">
      {/* Wallet Overview */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-gray-700/50 rounded-lg p-4">
          <h3 className="text-gray-400 text-sm">USDC Balance</h3>
          <p className="text-2xl font-bold text-white">${wallet.usdc_balance.toFixed(2)}</p>
        </div>
        <div className="bg-gray-700/50 rounded-lg p-4">
          <h3 className="text-gray-400 text-sm">Position Value</h3>
          <p className="text-2xl font-bold text-white">${wallet.position_value.toFixed(2)}</p>
        </div>
        <div className="bg-gray-700/50 rounded-lg p-4">
          <h3 className="text-gray-400 text-sm">Total Value</h3>
          <p className="text-2xl font-bold text-green-400">${wallet.total_value.toFixed(2)}</p>
        </div>
        <div className="bg-gray-700/50 rounded-lg p-4">
          <h3 className="text-gray-400 text-sm">Open Orders</h3>
          <p className="text-2xl font-bold text-white">{wallet.open_orders}</p>
        </div>
      </div>

      {/* Wallet Address & Sync */}
      <div className="flex items-center justify-between bg-gray-700/30 rounded-lg p-3">
        <div>
          <span className="text-gray-400 text-sm">Wallet: </span>
          <span className="text-gray-300 font-mono text-sm">{wallet.wallet_address}</span>
        </div>
        <button
          onClick={() => syncMutation.mutate()}
          disabled={syncMutation.isPending}
          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 rounded text-sm font-medium disabled:opacity-50"
        >
          {syncMutation.isPending ? 'Syncing...' : 'Sync to Database'}
        </button>
      </div>

      {syncMutation.isSuccess && (
        <div className="bg-green-900/30 border border-green-700 rounded-lg p-3 text-green-300 text-sm">
          Synced {syncMutation.data.synced} new positions, updated {syncMutation.data.updated} existing
        </div>
      )}

      {/* Positions */}
      <div>
        <h2 className="text-lg font-medium text-white mb-3">Live Positions</h2>
        {wallet.positions.length === 0 ? (
          <div className="text-gray-500 text-center py-8">No positions on Polymarket</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left text-gray-400 text-sm border-b border-gray-700">
                  <th className="pb-3 px-4">Outcome</th>
                  <th className="pb-3 px-4">Size</th>
                  <th className="pb-3 px-4">Avg Price</th>
                  <th className="pb-3 px-4">Cost Basis</th>
                  <th className="pb-3 px-4">Market</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {wallet.positions.map((pos) => (
                  <tr key={pos.asset_id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                    <td className="py-3 px-4">
                      <span className={clsx(
                        'px-2 py-0.5 rounded text-xs font-medium',
                        pos.outcome === 'Yes' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'
                      )}>
                        {pos.outcome}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-sm">{pos.size.toFixed(2)}</td>
                    <td className="py-3 px-4 text-sm">${pos.avg_price.toFixed(4)}</td>
                    <td className="py-3 px-4 text-sm">${pos.cost_basis.toFixed(2)}</td>
                    <td className="py-3 px-4 text-xs text-gray-500 font-mono truncate max-w-[200px]" title={pos.market}>
                      {pos.market.slice(0, 10)}...{pos.market.slice(-6)}
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

// Main Trading page
export default function Trading() {
  const [activeTab, setActiveTab] = useState<'positions' | 'signals' | 'trades' | 'strategies' | 'wallet'>('positions')
  const { data: status, isLoading: statusLoading } = useExecutorStatus()
  const setModeMutation = useSetTradingMode()
  const resetMutation = useResetPaperTrading()

  const stats = status?.stats

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold text-white">Trading</h1>
          {status && (
            <span className={clsx(
              'px-3 py-1 rounded-full text-sm font-medium',
              status.mode === 'paper' ? 'bg-yellow-900 text-yellow-300' : 'bg-red-900 text-red-300'
            )}>
              {status.mode === 'paper' ? 'Paper Trading' : 'LIVE Trading'}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {status?.mode === 'paper' && (
            <button
              onClick={() => resetMutation.mutate(10000)}
              disabled={resetMutation.isPending}
              className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium disabled:opacity-50"
            >
              Reset Paper
            </button>
          )}
          <button
            onClick={() => setModeMutation.mutate(status?.mode === 'paper' ? 'live' : 'paper')}
            disabled={setModeMutation.isPending}
            className={clsx(
              'px-3 py-1.5 rounded text-sm font-medium disabled:opacity-50',
              status?.mode === 'paper'
                ? 'bg-red-600 hover:bg-red-700'
                : 'bg-yellow-600 hover:bg-yellow-700'
            )}
          >
            Switch to {status?.mode === 'paper' ? 'Live' : 'Paper'}
          </button>
        </div>
      </div>

      {/* Stats */}
      {statusLoading ? (
        <div className="text-gray-400">Loading...</div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
          <StatCard
            title="Balance"
            value={`$${(stats?.balance ?? 0).toFixed(2)}`}
          />
          <StatCard
            title="Total P&L"
            value={`$${(stats?.total_pnl ?? 0).toFixed(2)}`}
            valueColor={(stats?.total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
            subtitle={stats?.total_pnl_pct ? `${(stats.total_pnl_pct * 100).toFixed(1)}%` : undefined}
          />
          <StatCard
            title="Open Positions"
            value={stats?.open_positions ?? 0}
          />
          <StatCard
            title="Total Trades"
            value={stats?.total_trades ?? 0}
          />
          <StatCard
            title="Win Rate"
            value={stats?.win_rate ? `${(stats.win_rate * 100).toFixed(0)}%` : '-'}
            subtitle={stats?.winning_trades !== undefined
              ? `${stats.winning_trades}W / ${stats.losing_trades}L`
              : undefined}
          />
          <StatCard
            title="Strategies"
            value={status?.enabled_strategies?.length ?? 0}
            subtitle="enabled"
          />
        </div>
      )}

      {/* Tabs */}
      <div>
        <div className="flex gap-1 border-b border-gray-700">
          <TabButton active={activeTab === 'positions'} onClick={() => setActiveTab('positions')}>
            Positions
          </TabButton>
          <TabButton active={activeTab === 'signals'} onClick={() => setActiveTab('signals')}>
            Signals
          </TabButton>
          <TabButton active={activeTab === 'trades'} onClick={() => setActiveTab('trades')}>
            Trades
          </TabButton>
          <TabButton active={activeTab === 'strategies'} onClick={() => setActiveTab('strategies')}>
            Strategies
          </TabButton>
          <TabButton active={activeTab === 'wallet'} onClick={() => setActiveTab('wallet')}>
            Wallet
          </TabButton>
        </div>

        <div className="bg-gray-800 rounded-b-lg min-h-[400px]">
          {activeTab === 'positions' && <PositionsTab />}
          {activeTab === 'signals' && <SignalsTab />}
          {activeTab === 'trades' && <TradesTab />}
          {activeTab === 'strategies' && <StrategiesTab />}
          {activeTab === 'wallet' && <WalletTab />}
        </div>
      </div>

      {/* Risk Limits Info */}
      {status?.risk_limits && (
        <div className="bg-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-medium text-gray-400 mb-3">Risk Limits</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <span className="text-gray-500">Max Position:</span>
              <span className="text-white ml-2">${status.risk_limits.max_position_usd}</span>
            </div>
            <div>
              <span className="text-gray-500">Max Exposure:</span>
              <span className="text-white ml-2">${status.risk_limits.max_total_exposure_usd}</span>
            </div>
            <div>
              <span className="text-gray-500">Max Positions:</span>
              <span className="text-white ml-2">{status.risk_limits.max_positions}</span>
            </div>
            <div>
              <span className="text-gray-500">Max Drawdown:</span>
              <span className="text-white ml-2">{(status.risk_limits.max_drawdown_pct * 100).toFixed(0)}%</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
