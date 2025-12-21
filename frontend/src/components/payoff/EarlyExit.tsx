import { useState, useMemo } from 'react'

interface EarlyExitProps {
  totalCost: number
  calculateEarlyExit: (exitPrice: number) => { proceeds: number; totalPnl: number; roi: number }
}

export function EarlyExit({ totalCost, calculateEarlyExit }: EarlyExitProps) {
  const [exitPrice, setExitPrice] = useState<number>(0.5)

  const result = useMemo(() => calculateEarlyExit(exitPrice), [exitPrice, calculateEarlyExit])

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setExitPrice(parseFloat(e.target.value))
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseFloat(e.target.value)
    if (!isNaN(value) && value >= 0 && value <= 1) {
      setExitPrice(value)
    }
  }

  const getPnlColor = (value: number) => {
    if (value > 0) return 'text-green-400'
    if (value < 0) return 'text-red-400'
    return 'text-gray-400'
  }

  if (totalCost <= 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-300 mb-4">Early Exit Simulator</h3>
        <p className="text-sm text-gray-400">
          Add positions to simulate early exits
        </p>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-300 mb-4">Early Exit Simulator</h3>
      <p className="text-xs text-gray-500 mb-4">
        Calculate your P&L if you close all positions at a given market price before resolution.
      </p>

      {/* Exit Price Slider */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-2">
          <label className="text-xs text-gray-400">Exit Price</label>
          <input
            type="number"
            value={exitPrice}
            onChange={handleInputChange}
            step="0.01"
            min="0"
            max="1"
            className="w-20 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </div>
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={exitPrice}
          onChange={handleSliderChange}
          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-indigo-500"
        />
        <div className="flex justify-between text-xs text-gray-500 mt-1">
          <span>0.00</span>
          <span>0.50</span>
          <span>1.00</span>
        </div>
      </div>

      {/* Results */}
      <div className="space-y-3">
        <div className="flex items-center justify-between py-2 border-b border-gray-700">
          <span className="text-sm text-gray-400">Total Cost</span>
          <span className="text-sm text-gray-300">${totalCost.toFixed(2)}</span>
        </div>

        <div className="flex items-center justify-between py-2 border-b border-gray-700">
          <span className="text-sm text-gray-400">Exit Proceeds</span>
          <span className="text-sm text-gray-300">${result.proceeds.toFixed(2)}</span>
        </div>

        <div className="flex items-center justify-between py-2 border-b border-gray-700">
          <span className="text-sm text-gray-400">Net P&L</span>
          <span className={`text-sm font-semibold ${getPnlColor(result.totalPnl)}`}>
            {result.totalPnl >= 0 ? '+' : ''}${result.totalPnl.toFixed(2)}
          </span>
        </div>

        <div className="flex items-center justify-between py-2">
          <span className="text-sm text-gray-400">ROI</span>
          <span className={`text-sm font-semibold ${getPnlColor(result.roi)}`}>
            {result.roi >= 0 ? '+' : ''}{result.roi.toFixed(1)}%
          </span>
        </div>
      </div>

      {/* Visual indicator */}
      <div className="mt-4 pt-4 border-t border-gray-700">
        <div className="relative h-4 bg-gray-700 rounded-full overflow-hidden">
          {/* Break-even line */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-gray-400"
            style={{ left: `${(totalCost > 0 ? (totalCost / (totalCost + Math.abs(result.proceeds - totalCost) + 0.01)) : 0.5) * 100}%` }}
          />
          {/* Current position */}
          <div
            className={`absolute top-0 bottom-0 rounded-full transition-all ${
              result.totalPnl >= 0 ? 'bg-green-500' : 'bg-red-500'
            }`}
            style={{
              left: result.totalPnl >= 0 ? '50%' : `${Math.max(0, 50 + (result.roi / 2))}%`,
              width: result.totalPnl >= 0
                ? `${Math.min(50, Math.abs(result.roi) / 2)}%`
                : `${Math.min(50, Math.abs(result.roi) / 2)}%`,
              right: result.totalPnl < 0 ? undefined : undefined
            }}
          />
        </div>
        <div className="flex justify-between text-xs text-gray-500 mt-1">
          <span>-100%</span>
          <span>0%</span>
          <span>+100%</span>
        </div>
      </div>
    </div>
  )
}
