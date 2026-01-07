import { useState, useMemo } from 'react'
import { Position, Side } from '../../utils/payoff-math'

interface SuggestHedgeProps {
  primaryPosition: Position | null
  onApplyHedge: (hedge: { side: Side; sizeUsd: number; guaranteedPnl: number }, entryPrice: number) => void
  suggestHedge: (hedgeEntryPrice: number, targetPnl?: number) => { side: Side; sizeUsd: number; guaranteedPnl: number } | null
}

type HedgeTarget = 'breakeven' | 'lock_profit'

export function SuggestHedge({ primaryPosition, onApplyHedge, suggestHedge }: SuggestHedgeProps) {
  const [hedgeTarget, setHedgeTarget] = useState<HedgeTarget>('breakeven')
  const [targetProfit, setTargetProfit] = useState<number>(10)
  const [hedgeEntryPrice, setHedgeEntryPrice] = useState<number>(0.5)

  // Calculate the suggested hedge
  const suggestion = useMemo(() => {
    if (!primaryPosition || primaryPosition.sizeUsd <= 0) return null

    const target = hedgeTarget === 'breakeven' ? 0 : targetProfit
    return suggestHedge(hedgeEntryPrice, target)
  }, [primaryPosition, hedgeTarget, targetProfit, hedgeEntryPrice, suggestHedge])

  const handleApply = () => {
    if (suggestion) {
      onApplyHedge(suggestion, hedgeEntryPrice)
    }
  }

  // Auto-calculate the "natural" hedge price (opposite of primary)
  const suggestedHedgePrice = primaryPosition
    ? (1 - primaryPosition.entryPrice).toFixed(2)
    : '0.50'

  if (!primaryPosition || primaryPosition.sizeUsd <= 0 || primaryPosition.entryPrice <= 0 || primaryPosition.entryPrice >= 1) {
    return (
      <div className="bg-gray-800 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-300 mb-4">Suggest Hedge</h3>
        <p className="text-sm text-gray-400">
          Add a valid primary position to get hedge suggestions
        </p>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-300 mb-4">Suggest Hedge</h3>

      {/* Hedge Target */}
      <div className="mb-4">
        <label className="block text-xs text-gray-400 mb-2">Hedge Goal</label>
        <div className="flex gap-2">
          <button
            onClick={() => setHedgeTarget('breakeven')}
            className={`flex-1 py-2 px-3 rounded text-sm transition-colors ${
              hedgeTarget === 'breakeven'
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            Break-even
          </button>
          <button
            onClick={() => setHedgeTarget('lock_profit')}
            className={`flex-1 py-2 px-3 rounded text-sm transition-colors ${
              hedgeTarget === 'lock_profit'
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            Lock Profit
          </button>
        </div>
      </div>

      {/* Target Profit (if locking profit) */}
      {hedgeTarget === 'lock_profit' && (
        <div className="mb-4">
          <label className="block text-xs text-gray-400 mb-2">Target Profit ($)</label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
            <input
              type="number"
              value={targetProfit}
              onChange={(e) => setTargetProfit(parseFloat(e.target.value) || 0)}
              step="1"
              min="0"
              className="w-full bg-gray-700 border border-gray-600 rounded pl-7 pr-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>
      )}

      {/* Hedge Entry Price */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-2">
          <label className="text-xs text-gray-400">Hedge Entry Price</label>
          <button
            onClick={() => setHedgeEntryPrice(parseFloat(suggestedHedgePrice))}
            className="text-xs text-indigo-400 hover:text-indigo-300"
          >
            Use {suggestedHedgePrice}
          </button>
        </div>
        <input
          type="number"
          value={hedgeEntryPrice}
          onChange={(e) => setHedgeEntryPrice(parseFloat(e.target.value) || 0)}
          step="0.01"
          min="0.01"
          max="0.99"
          className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
      </div>

      {/* Suggestion Result */}
      {suggestion ? (
        <div className="space-y-3">
          <div className="bg-gray-900/50 rounded p-3">
            <div className="text-xs text-gray-400 mb-2">Suggested Hedge Position</div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm text-gray-300">Side:</span>
              <span className={`text-sm font-medium ${suggestion.side === 'YES' ? 'text-green-400' : 'text-red-400'}`}>
                {suggestion.side}
              </span>
            </div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm text-gray-300">Entry Price:</span>
              <span className="text-sm font-medium text-white">{hedgeEntryPrice.toFixed(2)}</span>
            </div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm text-gray-300">Size:</span>
              <span className="text-sm font-medium text-white">${suggestion.sizeUsd.toFixed(2)}</span>
            </div>
            <div className="flex items-center justify-between pt-2 border-t border-gray-700">
              <span className="text-sm text-gray-300">Guaranteed P&L:</span>
              <span className={`text-sm font-semibold ${suggestion.guaranteedPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {suggestion.guaranteedPnl >= 0 ? '+' : ''}${suggestion.guaranteedPnl.toFixed(2)}
              </span>
            </div>
          </div>

          <button
            onClick={handleApply}
            className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2 px-4 rounded transition-colors"
          >
            Apply Hedge
          </button>
        </div>
      ) : (
        <div className="text-sm text-amber-400 bg-amber-500/10 rounded p-3">
          Cannot achieve this target with the current settings. Try adjusting the hedge entry price or target profit.
        </div>
      )}

      {/* Info */}
      <div className="mt-4 text-xs text-gray-500">
        <p>
          Hedge is calculated against Position 1 ({primaryPosition.side} @ {primaryPosition.entryPrice.toFixed(2)}, ${primaryPosition.sizeUsd.toFixed(2)}).
        </p>
      </div>
    </div>
  )
}
