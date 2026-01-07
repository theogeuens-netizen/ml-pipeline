import { ScenarioRow, Position } from '../../utils/payoff-math'

interface ScenarioTableProps {
  scenarios: ScenarioRow[]
  positions: Position[]
}

export function ScenarioTable({ scenarios, positions }: ScenarioTableProps) {
  const validPositions = positions.filter(p =>
    p.sizeUsd > 0 && p.entryPrice > 0 && p.entryPrice < 1
  )

  const formatCurrency = (value: number) => {
    const sign = value >= 0 ? '+' : ''
    return `${sign}$${Math.abs(value).toFixed(2)}`
  }

  const formatPercent = (value: number) => {
    const sign = value >= 0 ? '+' : ''
    return `${sign}${value.toFixed(1)}%`
  }

  const getPnlColor = (value: number) => {
    if (value > 0) return 'text-green-400'
    if (value < 0) return 'text-red-400'
    return 'text-gray-400'
  }

  if (validPositions.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-6 text-center text-gray-400">
        Add a valid position to see scenario analysis
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-700">
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
                Outcome
              </th>
              {validPositions.map((_, index) => (
                <th
                  key={index}
                  className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider"
                >
                  Pos {index + 1}
                </th>
              ))}
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">
                Net P&L
              </th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">
                ROI
              </th>
            </tr>
          </thead>
          <tbody>
            {scenarios.map((scenario) => (
              <tr
                key={scenario.outcome}
                className="border-b border-gray-700/50 hover:bg-gray-700/30"
              >
                <td className="px-4 py-4">
                  <span
                    className={`inline-flex items-center px-2.5 py-0.5 rounded text-xs font-medium ${
                      scenario.outcome === 'YES'
                        ? 'bg-green-500/20 text-green-400'
                        : 'bg-red-500/20 text-red-400'
                    }`}
                  >
                    {scenario.outcome} wins
                  </span>
                </td>
                {scenario.positionPnls.map((posPnl, index) => (
                  <td
                    key={index}
                    className={`px-4 py-4 text-right font-mono text-sm ${getPnlColor(posPnl.pnl)}`}
                  >
                    {formatCurrency(posPnl.pnl)}
                  </td>
                ))}
                <td
                  className={`px-4 py-4 text-right font-mono text-sm font-semibold ${getPnlColor(scenario.netPnl)}`}
                >
                  {formatCurrency(scenario.netPnl)}
                </td>
                <td
                  className={`px-4 py-4 text-right font-mono text-sm ${getPnlColor(scenario.roi)}`}
                >
                  {formatPercent(scenario.roi)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
