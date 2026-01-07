import { useState, useMemo, useCallback } from 'react'
import {
  Position,
  Side,
  PayoffResult,
  ChartDataPoint,
  ScenarioRow,
  calculatePayoffMetrics,
  generateChartData,
  generateScenarioTable,
  calculateOptimalHedge,
  calculateEarlyExitPnl,
  formatPositionsForExport
} from '../utils/payoff-math'

let positionIdCounter = 0

function generatePositionId(): string {
  return `pos-${++positionIdCounter}-${Date.now()}`
}

function createDefaultPosition(): Position {
  return {
    id: generatePositionId(),
    side: 'YES',
    entryPrice: 0.5,
    sizeUsd: 100
  }
}

export interface UsePayoffCalculatorReturn {
  // Position management
  positions: Position[]
  addPosition: () => void
  removePosition: (id: string) => void
  updatePosition: (id: string, updates: Partial<Omit<Position, 'id'>>) => void
  resetPositions: () => void

  // Computed values
  metrics: PayoffResult
  chartData: ChartDataPoint[]
  scenarioTable: ScenarioRow[]

  // Hedge suggestions
  suggestHedge: (
    hedgeEntryPrice: number,
    targetPnl?: number
  ) => { side: Side; sizeUsd: number; guaranteedPnl: number } | null
  applyHedge: (hedge: { side: Side; sizeUsd: number; guaranteedPnl: number }, entryPrice: number) => void

  // Early exit
  calculateEarlyExit: (exitPrice: number) => { proceeds: number; totalPnl: number; roi: number }

  // Export
  exportToClipboard: () => Promise<boolean>
}

export function usePayoffCalculator(): UsePayoffCalculatorReturn {
  const [positions, setPositions] = useState<Position[]>([createDefaultPosition()])

  // Add a new position
  const addPosition = useCallback(() => {
    setPositions(prev => [...prev, createDefaultPosition()])
  }, [])

  // Remove a position by ID
  const removePosition = useCallback((id: string) => {
    setPositions(prev => {
      // Don't remove the last position
      if (prev.length <= 1) return prev
      return prev.filter(p => p.id !== id)
    })
  }, [])

  // Update a position
  const updatePosition = useCallback((id: string, updates: Partial<Omit<Position, 'id'>>) => {
    setPositions(prev =>
      prev.map(p => (p.id === id ? { ...p, ...updates } : p))
    )
  }, [])

  // Reset to single default position
  const resetPositions = useCallback(() => {
    positionIdCounter = 0
    setPositions([createDefaultPosition()])
  }, [])

  // Calculate metrics
  const metrics = useMemo(() => calculatePayoffMetrics(positions), [positions])

  // Generate chart data
  const chartData = useMemo(() => generateChartData(positions), [positions])

  // Generate scenario table
  const scenarioTable = useMemo(() => generateScenarioTable(positions), [positions])

  // Suggest a hedge position
  const suggestHedge = useCallback(
    (hedgeEntryPrice: number, targetPnl: number = 0) => {
      // Use the first position as the primary position to hedge
      const primaryPosition = positions.find(p =>
        p.sizeUsd > 0 && p.entryPrice > 0 && p.entryPrice < 1
      )

      if (!primaryPosition) return null

      return calculateOptimalHedge(primaryPosition, hedgeEntryPrice, targetPnl)
    },
    [positions]
  )

  // Apply a suggested hedge by adding it as a new position
  const applyHedge = useCallback(
    (hedge: { side: Side; sizeUsd: number; guaranteedPnl: number }, entryPrice: number) => {
      const newPosition: Position = {
        id: generatePositionId(),
        side: hedge.side,
        entryPrice,
        sizeUsd: hedge.sizeUsd
      }
      setPositions(prev => [...prev, newPosition])
    },
    []
  )

  // Calculate early exit P&L
  const calculateEarlyExit = useCallback(
    (exitPrice: number) => calculateEarlyExitPnl(positions, exitPrice),
    [positions]
  )

  // Export to clipboard
  const exportToClipboard = useCallback(async (): Promise<boolean> => {
    try {
      const text = formatPositionsForExport(positions, metrics)
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      return false
    }
  }, [positions, metrics])

  return {
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
  }
}
