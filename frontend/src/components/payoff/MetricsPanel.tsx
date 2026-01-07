import { PayoffResult } from '../../utils/payoff-math'

interface MetricsPanelProps {
  metrics: PayoffResult
}

function StatCard({
  title,
  value,
  subtitle,
  valueColor = 'text-white'
}: {
  title: string
  value: string
  subtitle?: string
  valueColor?: string
}) {
  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <div className="text-xs text-gray-400 mb-1">{title}</div>
      <div className={`text-xl font-semibold ${valueColor}`}>{value}</div>
      {subtitle && <div className="text-xs text-gray-500 mt-1">{subtitle}</div>}
    </div>
  )
}

export function MetricsPanel({ metrics }: MetricsPanelProps) {
  const formatCurrency = (value: number) => {
    const sign = value >= 0 ? '+' : ''
    return `${sign}$${Math.abs(value).toFixed(2)}`
  }

  const formatPercent = (value: number, total: number) => {
    if (total === 0) return '0%'
    const pct = (value / total) * 100
    const sign = pct >= 0 ? '+' : ''
    return `${sign}${pct.toFixed(1)}%`
  }

  // Format exposure
  let exposureText: string
  let exposureColor: string
  if (metrics.netExposure.yes > 0) {
    exposureText = `$${metrics.netExposure.yes.toFixed(0)} YES`
    exposureColor = 'text-green-400'
  } else if (metrics.netExposure.no > 0) {
    exposureText = `$${metrics.netExposure.no.toFixed(0)} NO`
    exposureColor = 'text-red-400'
  } else {
    exposureText = 'Neutral'
    exposureColor = 'text-gray-400'
  }

  // Format break-even
  const breakEvenText = metrics.breakEvenPrices.length > 0
    ? metrics.breakEvenPrices.map(p => p.toFixed(2)).join(', ')
    : 'N/A'

  // Status badge
  let statusBadge: { text: string; color: string } | null = null
  if (metrics.isLockedProfit) {
    const minProfit = Math.min(metrics.yesWinsPnl, metrics.noWinsPnl)
    statusBadge = {
      text: `Locked Profit: ${formatCurrency(minProfit)}`,
      color: 'bg-green-600'
    }
  } else if (metrics.isLockedLoss) {
    const maxLoss = Math.max(metrics.yesWinsPnl, metrics.noWinsPnl)
    statusBadge = {
      text: `Locked Loss: ${formatCurrency(maxLoss)}`,
      color: 'bg-red-600'
    }
  }

  return (
    <div className="space-y-4">
      {statusBadge && (
        <div className={`${statusBadge.color} text-white text-sm font-medium px-4 py-2 rounded-lg text-center`}>
          {statusBadge.text}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          title="Total Capital"
          value={`$${metrics.totalCapital.toFixed(2)}`}
        />
        <StatCard
          title="Net Exposure"
          value={exposureText}
          valueColor={exposureColor}
        />
        <StatCard
          title="Break-even"
          value={breakEvenText}
          valueColor="text-gray-300"
        />
        <StatCard
          title="Hedge Ratio"
          value={metrics.hedgeRatio !== null ? metrics.hedgeRatio.toFixed(2) : 'N/A'}
          subtitle={metrics.hedgeRatio !== null ? 'Hedge / Primary' : undefined}
          valueColor="text-gray-300"
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-gray-800 rounded-lg p-4">
          <div className="text-xs text-gray-400 mb-1">Max Profit</div>
          <div className="text-xl font-semibold text-green-400">
            {formatCurrency(metrics.maxProfit.amount)}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            If {metrics.maxProfit.outcome} wins
            ({formatPercent(metrics.maxProfit.amount, metrics.totalCapital)} ROI)
          </div>
        </div>

        <div className="bg-gray-800 rounded-lg p-4">
          <div className="text-xs text-gray-400 mb-1">Max Loss</div>
          <div className="text-xl font-semibold text-red-400">
            {formatCurrency(metrics.maxLoss.amount)}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            If {metrics.maxLoss.outcome} wins
            ({formatPercent(metrics.maxLoss.amount, metrics.totalCapital)} ROI)
          </div>
        </div>
      </div>
    </div>
  )
}
