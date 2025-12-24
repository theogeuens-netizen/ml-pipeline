import { clsx } from 'clsx'
import { usePortfolioSummary } from '../../hooks/useAnalystData'

interface KPICardProps {
  label: string
  value: string
  change?: string
  changeType?: 'positive' | 'negative' | 'neutral'
  subtext?: string
}

function KPICard({ label, value, change, changeType = 'neutral', subtext }: KPICardProps) {
  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <p className="text-gray-400 text-sm font-medium mb-1">{label}</p>
      <p className="text-2xl font-bold text-white font-mono">{value}</p>
      {change && (
        <p className={clsx(
          'text-sm font-medium mt-1',
          changeType === 'positive' && 'text-green-400',
          changeType === 'negative' && 'text-red-400',
          changeType === 'neutral' && 'text-gray-400'
        )}>
          {change}
        </p>
      )}
      {subtext && !change && (
        <p className="text-gray-500 text-xs mt-1">{subtext}</p>
      )}
    </div>
  )
}

function formatCurrency(value: number, showSign = false): string {
  const sign = showSign && value >= 0 ? '+' : ''
  return `${sign}$${Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatPercent(value: number, showSign = false): string {
  const sign = showSign && value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

export default function PortfolioHeader() {
  const { data, isLoading, error } = usePortfolioSummary()

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        {[...Array(6)].map((_, i) => (
          <div key={i} className="bg-gray-800 rounded-lg p-4 border border-gray-700 animate-pulse">
            <div className="h-4 bg-gray-700 rounded w-20 mb-2" />
            <div className="h-8 bg-gray-700 rounded w-24" />
          </div>
        ))}
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="bg-red-900/30 border border-red-700 rounded-lg p-4">
        <p className="text-red-400">Failed to load portfolio summary</p>
      </div>
    )
  }

  const {
    cash,
    portfolio_value,
    unrealized_pnl,
    realized_pnl,
    total_return_pct,
    open_positions,
    current_drawdown_pct,
  } = data

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
      <KPICard
        label="Cash"
        value={formatCurrency(cash)}
        subtext="Available capital"
      />
      <KPICard
        label="Portfolio Value"
        value={formatCurrency(portfolio_value)}
        subtext={`${data.strategies_count} strategies`}
      />
      <KPICard
        label="Unrealized P&L"
        value={formatCurrency(unrealized_pnl, true)}
        changeType={unrealized_pnl >= 0 ? 'positive' : 'negative'}
        change={`${open_positions} open positions`}
      />
      <KPICard
        label="Realized P&L"
        value={formatCurrency(realized_pnl, true)}
        changeType={realized_pnl >= 0 ? 'positive' : 'negative'}
      />
      <KPICard
        label="Total Return"
        value={formatPercent(total_return_pct, true)}
        changeType={total_return_pct >= 0 ? 'positive' : 'negative'}
        subtext={`HWM: ${formatCurrency(data.high_water_mark)}`}
      />
      <KPICard
        label="Drawdown"
        value={formatPercent(current_drawdown_pct)}
        changeType={current_drawdown_pct > 10 ? 'negative' : current_drawdown_pct > 5 ? 'neutral' : 'positive'}
        subtext={current_drawdown_pct > 10 ? '⚠️ High drawdown' : ''}
      />
    </div>
  )
}
