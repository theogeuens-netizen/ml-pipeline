import { useState } from 'react'
import { usePayoffCalculator } from '../hooks/usePayoffCalculator'
import { PositionInput } from '../components/payoff/PositionInput'
import { MetricsPanel } from '../components/payoff/MetricsPanel'
import { ScenarioTable } from '../components/payoff/ScenarioTable'
import { PayoffChart } from '../components/payoff/PayoffChart'
import { SuggestHedge } from '../components/payoff/SuggestHedge'
import { EarlyExit } from '../components/payoff/EarlyExit'

export default function PayoffCalculator() {
  const {
    positions,
    addPosition,
    removePosition,
    updatePosition,
    resetPositions,
    metrics,
    chartData,
    scenarioTable,
    suggestHedge,
    applyHedge,
    calculateEarlyExit,
    exportToClipboard
  } = usePayoffCalculator()

  const [copySuccess, setCopySuccess] = useState(false)

  const handleCopyToClipboard = async () => {
    const success = await exportToClipboard()
    if (success) {
      setCopySuccess(true)
      setTimeout(() => setCopySuccess(false), 2000)
    }
  }

  // Get the primary position for hedge suggestions
  const primaryPosition = positions.find(p =>
    p.sizeUsd > 0 && p.entryPrice > 0 && p.entryPrice < 1
  ) || null

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Position Payoff Calculator</h1>
          <p className="text-gray-400 text-sm mt-1">
            Calculate P&L scenarios for multi-leg positions on the same market
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleCopyToClipboard}
            className={`flex items-center gap-2 px-4 py-2 rounded transition-colors ${
              copySuccess
                ? 'bg-green-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            {copySuccess ? (
              <>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                Copied!
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                Copy
              </>
            )}
          </button>
          <button
            onClick={resetPositions}
            className="px-4 py-2 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 transition-colors"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Position Inputs */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-medium text-white">Positions</h2>
          <button
            onClick={addPosition}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Add Position
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {positions.map((position, index) => (
            <PositionInput
              key={position.id}
              position={position}
              index={index}
              canRemove={positions.length > 1}
              onUpdate={(updates) => updatePosition(position.id, updates)}
              onRemove={() => removePosition(position.id)}
            />
          ))}
        </div>
      </div>

      {/* Core Metrics */}
      <div>
        <h2 className="text-lg font-medium text-white mb-4">Analysis</h2>
        <MetricsPanel metrics={metrics} />
      </div>

      {/* Scenario Table */}
      <div>
        <h2 className="text-lg font-medium text-white mb-4">Resolution Scenarios</h2>
        <ScenarioTable scenarios={scenarioTable} positions={positions} />
      </div>

      {/* Payoff Chart */}
      <PayoffChart data={chartData} breakEvenPrices={metrics.breakEvenPrices} />

      {/* Tools Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SuggestHedge
          primaryPosition={primaryPosition}
          onApplyHedge={applyHedge}
          suggestHedge={suggestHedge}
        />
        <EarlyExit
          totalCost={metrics.totalCapital}
          calculateEarlyExit={calculateEarlyExit}
        />
      </div>

      {/* Help Text */}
      <div className="bg-gray-800/50 rounded-lg p-4 text-sm text-gray-400">
        <h3 className="font-medium text-gray-300 mb-2">How it works</h3>
        <ul className="space-y-1 list-disc list-inside">
          <li>Add positions by specifying side (YES/NO), entry price, and size in USD</li>
          <li>The payoff chart shows your net P&L at different resolution prices</li>
          <li>Use "Suggest Hedge" to calculate a position that locks in profit or achieves break-even</li>
          <li>Use "Early Exit Simulator" to see P&L if you close positions at a given price before resolution</li>
          <li>Click "Copy" to export position details to clipboard</li>
        </ul>
      </div>
    </div>
  )
}
