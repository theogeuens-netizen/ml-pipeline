import { Position, Side } from '../../utils/payoff-math'

interface PositionInputProps {
  position: Position
  index: number
  canRemove: boolean
  onUpdate: (updates: Partial<Omit<Position, 'id'>>) => void
  onRemove: () => void
}

export function PositionInput({
  position,
  index,
  canRemove,
  onUpdate,
  onRemove
}: PositionInputProps) {
  const handleSideChange = (side: Side) => {
    onUpdate({ side })
  }

  const handlePriceChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseFloat(e.target.value)
    if (!isNaN(value) && value >= 0 && value <= 1) {
      onUpdate({ entryPrice: value })
    }
  }

  const handleSizeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseFloat(e.target.value)
    if (!isNaN(value) && value >= 0) {
      onUpdate({ sizeUsd: value })
    }
  }

  const isValidPrice = position.entryPrice > 0 && position.entryPrice < 1
  const isValidSize = position.sizeUsd > 0

  return (
    <div className="bg-gray-800 rounded-lg p-4 relative">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-gray-300">
          Position {index + 1}
        </h3>
        {canRemove && (
          <button
            onClick={onRemove}
            className="text-gray-500 hover:text-red-400 transition-colors"
            title="Remove position"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      {/* Side Toggle */}
      <div className="mb-4">
        <label className="block text-xs text-gray-400 mb-2">Side</label>
        <div className="flex gap-2">
          <button
            onClick={() => handleSideChange('YES')}
            className={`flex-1 py-2 px-4 rounded text-sm font-medium transition-colors ${
              position.side === 'YES'
                ? 'bg-green-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            YES
          </button>
          <button
            onClick={() => handleSideChange('NO')}
            className={`flex-1 py-2 px-4 rounded text-sm font-medium transition-colors ${
              position.side === 'NO'
                ? 'bg-red-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            NO
          </button>
        </div>
      </div>

      {/* Entry Price */}
      <div className="mb-4">
        <label className="block text-xs text-gray-400 mb-2">Entry Price</label>
        <div className="relative">
          <input
            type="number"
            value={position.entryPrice}
            onChange={handlePriceChange}
            step="0.01"
            min="0.01"
            max="0.99"
            className={`w-full bg-gray-700 border rounded px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
              isValidPrice ? 'border-gray-600' : 'border-red-500'
            }`}
          />
          {!isValidPrice && (
            <p className="text-xs text-red-400 mt-1">Must be between 0.01 and 0.99</p>
          )}
        </div>
      </div>

      {/* Size USD */}
      <div>
        <label className="block text-xs text-gray-400 mb-2">Size (USD)</label>
        <div className="relative">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
          <input
            type="number"
            value={position.sizeUsd}
            onChange={handleSizeChange}
            step="1"
            min="1"
            className={`w-full bg-gray-700 border rounded pl-7 pr-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
              isValidSize ? 'border-gray-600' : 'border-red-500'
            }`}
          />
          {!isValidSize && (
            <p className="text-xs text-red-400 mt-1">Must be greater than 0</p>
          )}
        </div>
      </div>

      {/* Position Summary */}
      {isValidPrice && isValidSize && (
        <div className="mt-4 pt-4 border-t border-gray-700">
          <div className="flex justify-between text-sm">
            <span className="text-gray-400">Shares:</span>
            <span className="text-gray-200">
              {(position.sizeUsd / position.entryPrice).toFixed(2)}
            </span>
          </div>
          <div className="flex justify-between text-sm mt-1">
            <span className="text-gray-400">If wins:</span>
            <span className="text-green-400">
              +${((position.sizeUsd / position.entryPrice) - position.sizeUsd).toFixed(2)}
            </span>
          </div>
          <div className="flex justify-between text-sm mt-1">
            <span className="text-gray-400">If loses:</span>
            <span className="text-red-400">
              -${position.sizeUsd.toFixed(2)}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
