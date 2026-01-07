// Position Payoff Calculator - Pure Calculation Functions

export type Side = 'YES' | 'NO'

export interface Position {
  id: string
  side: Side
  entryPrice: number // 0-1
  sizeUsd: number
}

export interface PayoffResult {
  totalCapital: number
  netExposure: { yes: number; no: number }
  breakEvenPrices: number[]
  maxProfit: { amount: number; outcome: Side }
  maxLoss: { amount: number; outcome: Side }
  yesWinsPnl: number
  noWinsPnl: number
  hedgeRatio: number | null
  isLockedProfit: boolean
  isLockedLoss: boolean
}

export interface ChartDataPoint {
  price: number
  pnl: number
}

export interface ScenarioRow {
  outcome: Side
  positionPnls: { id: string; pnl: number }[]
  netPnl: number
  roi: number
}

/**
 * Calculate the number of shares purchased for a given USD amount and price
 */
export function calculateShares(sizeUsd: number, price: number): number {
  if (price <= 0 || price >= 1) return 0
  return sizeUsd / price
}

/**
 * Calculate P&L for a single position given a resolution outcome
 *
 * For YES position:
 *   - If YES wins: shares pay out $1 each, P&L = shares - cost = cost * (1-p)/p
 *   - If NO wins: shares worth $0, P&L = -cost
 *
 * For NO position:
 *   - If NO wins: shares pay out $1 each, P&L = shares - cost = cost * (1-p)/p
 *   - If YES wins: shares worth $0, P&L = -cost
 */
export function calculatePositionPnl(position: Position, outcome: Side): number {
  const { side, entryPrice, sizeUsd } = position

  if (sizeUsd <= 0 || entryPrice <= 0 || entryPrice >= 1) {
    return 0
  }

  const shares = calculateShares(sizeUsd, entryPrice)

  if (side === outcome) {
    // Position wins - shares pay out $1 each
    return shares - sizeUsd // = sizeUsd * (1 - entryPrice) / entryPrice
  } else {
    // Position loses - shares worth $0
    return -sizeUsd
  }
}

/**
 * Calculate total P&L for all positions given a resolution outcome
 */
export function calculateTotalPnl(positions: Position[], outcome: Side): number {
  return positions.reduce((total, pos) => total + calculatePositionPnl(pos, outcome), 0)
}

/**
 * Calculate P&L at any given resolution price (for payoff chart)
 *
 * This models a continuous payoff where the position value scales linearly
 * with the resolution price. At price 0, YES is worthless; at price 1, YES pays full.
 */
export function calculatePayoffAtPrice(positions: Position[], resolutionPrice: number): number {
  return positions.reduce((total, pos) => {
    const { side, entryPrice, sizeUsd } = pos

    if (sizeUsd <= 0 || entryPrice <= 0 || entryPrice >= 1) {
      return total
    }

    const shares = calculateShares(sizeUsd, entryPrice)

    if (side === 'YES') {
      // YES shares are worth resolutionPrice each
      const value = shares * resolutionPrice
      return total + (value - sizeUsd)
    } else {
      // NO shares are worth (1 - resolutionPrice) each
      const value = shares * (1 - resolutionPrice)
      return total + (value - sizeUsd)
    }
  }, 0)
}

/**
 * Generate chart data points from 0 to 1 in specified increments
 */
export function generateChartData(positions: Position[], step: number = 0.01): ChartDataPoint[] {
  const data: ChartDataPoint[] = []

  for (let price = 0; price <= 1; price += step) {
    data.push({
      price: Math.round(price * 100) / 100, // Avoid floating point issues
      pnl: calculatePayoffAtPrice(positions, price)
    })
  }

  // Ensure we always include exactly 1.0
  if (data[data.length - 1].price !== 1) {
    data.push({ price: 1, pnl: calculatePayoffAtPrice(positions, 1) })
  }

  return data
}

/**
 * Find break-even prices (where P&L crosses zero)
 */
export function findBreakEvenPrices(positions: Position[]): number[] {
  if (positions.length === 0) return []

  const breakEvens: number[] = []
  const step = 0.001 // Fine granularity for finding crossings

  let prevPnl = calculatePayoffAtPrice(positions, 0)

  for (let price = step; price <= 1; price += step) {
    const pnl = calculatePayoffAtPrice(positions, price)

    // Check for zero crossing
    if ((prevPnl < 0 && pnl >= 0) || (prevPnl > 0 && pnl <= 0) || Math.abs(pnl) < 0.001) {
      // Linear interpolation to find exact crossing
      if (Math.abs(pnl - prevPnl) > 0.0001) {
        const crossPrice = price - step + (step * Math.abs(prevPnl)) / Math.abs(pnl - prevPnl)
        breakEvens.push(Math.round(crossPrice * 1000) / 1000)
      } else if (Math.abs(pnl) < 0.001) {
        breakEvens.push(Math.round(price * 1000) / 1000)
      }
    }

    prevPnl = pnl
  }

  // Remove duplicates (within 0.01 tolerance)
  return breakEvens.filter((be, i, arr) =>
    i === 0 || Math.abs(be - arr[i - 1]) > 0.01
  )
}

/**
 * Calculate all payoff metrics for a set of positions
 */
export function calculatePayoffMetrics(positions: Position[]): PayoffResult {
  const validPositions = positions.filter(p =>
    p.sizeUsd > 0 && p.entryPrice > 0 && p.entryPrice < 1
  )

  // Total capital deployed
  const totalCapital = validPositions.reduce((sum, p) => sum + p.sizeUsd, 0)

  // Net exposure
  const yesExposure = validPositions
    .filter(p => p.side === 'YES')
    .reduce((sum, p) => sum + p.sizeUsd, 0)
  const noExposure = validPositions
    .filter(p => p.side === 'NO')
    .reduce((sum, p) => sum + p.sizeUsd, 0)

  // P&L for each outcome
  const yesWinsPnl = calculateTotalPnl(validPositions, 'YES')
  const noWinsPnl = calculateTotalPnl(validPositions, 'NO')

  // Max profit and loss
  let maxProfit: { amount: number; outcome: Side }
  let maxLoss: { amount: number; outcome: Side }

  if (yesWinsPnl >= noWinsPnl) {
    maxProfit = { amount: yesWinsPnl, outcome: 'YES' }
    maxLoss = { amount: noWinsPnl, outcome: 'NO' }
  } else {
    maxProfit = { amount: noWinsPnl, outcome: 'NO' }
    maxLoss = { amount: yesWinsPnl, outcome: 'YES' }
  }

  // Break-even prices
  const breakEvenPrices = findBreakEvenPrices(validPositions)

  // Hedge ratio (if 2+ positions with opposite sides)
  let hedgeRatio: number | null = null
  if (validPositions.length >= 2) {
    const firstSide = validPositions[0].side
    const hedgePositions = validPositions.filter(p => p.side !== firstSide)
    if (hedgePositions.length > 0) {
      const primarySize = validPositions.filter(p => p.side === firstSide)
        .reduce((sum, p) => sum + p.sizeUsd, 0)
      const hedgeSize = hedgePositions.reduce((sum, p) => sum + p.sizeUsd, 0)
      hedgeRatio = hedgeSize / primarySize
    }
  }

  // Check if locked profit or loss (both outcomes positive or both negative)
  const isLockedProfit = yesWinsPnl > 0 && noWinsPnl > 0
  const isLockedLoss = yesWinsPnl < 0 && noWinsPnl < 0

  return {
    totalCapital,
    netExposure: { yes: yesExposure - noExposure, no: noExposure - yesExposure },
    breakEvenPrices,
    maxProfit,
    maxLoss,
    yesWinsPnl,
    noWinsPnl,
    hedgeRatio,
    isLockedProfit,
    isLockedLoss
  }
}

/**
 * Generate scenario table data
 */
export function generateScenarioTable(positions: Position[]): ScenarioRow[] {
  const validPositions = positions.filter(p =>
    p.sizeUsd > 0 && p.entryPrice > 0 && p.entryPrice < 1
  )

  const totalCapital = validPositions.reduce((sum, p) => sum + p.sizeUsd, 0)

  const outcomes: Side[] = ['YES', 'NO']

  return outcomes.map(outcome => {
    const positionPnls = validPositions.map(pos => ({
      id: pos.id,
      pnl: calculatePositionPnl(pos, outcome)
    }))

    const netPnl = positionPnls.reduce((sum, p) => sum + p.pnl, 0)
    const roi = totalCapital > 0 ? (netPnl / totalCapital) * 100 : 0

    return { outcome, positionPnls, netPnl, roi }
  })
}

/**
 * Calculate the optimal hedge size to achieve a target P&L
 *
 * Given a primary position and desired outcome, calculates the hedge position needed.
 *
 * @param primaryPosition - The position to hedge
 * @param hedgeEntryPrice - Entry price for the hedge (typically 1 - current_market_price)
 * @param targetPnl - Target P&L (0 for break-even, positive for locked profit)
 */
export function calculateOptimalHedge(
  primaryPosition: Position,
  hedgeEntryPrice: number,
  targetPnl: number = 0
): { side: Side; sizeUsd: number; guaranteedPnl: number } | null {
  const { side, entryPrice, sizeUsd } = primaryPosition

  if (sizeUsd <= 0 || entryPrice <= 0 || entryPrice >= 1) {
    return null
  }
  if (hedgeEntryPrice <= 0 || hedgeEntryPrice >= 1) {
    return null
  }

  // Hedge side is opposite of primary
  const hedgeSide: Side = side === 'YES' ? 'NO' : 'YES'

  // Calculate primary position outcomes
  const primaryWinPnl = sizeUsd * (1 - entryPrice) / entryPrice
  const primaryLosePnl = -sizeUsd

  // Hedge outcomes (if hedge wins/loses)
  // hedgeWinPnl = hedgeSize * (1 - hedgePrice) / hedgePrice
  // hedgeLosePnl = -hedgeSize

  // For break-even on either outcome:
  // If primary wins (hedge loses): primaryWinPnl - hedgeSize = targetPnl
  //   => hedgeSize = primaryWinPnl - targetPnl
  // If primary loses (hedge wins): primaryLosePnl + hedgeSize * (1 - hedgePrice) / hedgePrice = targetPnl
  //   => hedgeSize = (targetPnl - primaryLosePnl) * hedgePrice / (1 - hedgePrice)

  // For equal outcomes on both sides:
  // primaryWinPnl - hedgeSize = primaryLosePnl + hedgeSize * (1 - hedgePrice) / hedgePrice
  // Let w = (1 - hedgePrice) / hedgePrice (hedge win multiplier)
  // primaryWinPnl - hedgeSize = primaryLosePnl + hedgeSize * w
  // primaryWinPnl - primaryLosePnl = hedgeSize * (1 + w)
  // hedgeSize = (primaryWinPnl - primaryLosePnl) / (1 + w)

  const hedgeWinMultiplier = (1 - hedgeEntryPrice) / hedgeEntryPrice
  const hedgeSize = (primaryWinPnl - primaryLosePnl - 2 * targetPnl) / (1 + hedgeWinMultiplier)

  if (hedgeSize < 0) {
    return null // Can't achieve target with opposite side
  }

  // Calculate guaranteed P&L after hedge
  const guaranteedPnl = primaryWinPnl - hedgeSize

  return {
    side: hedgeSide,
    sizeUsd: Math.round(hedgeSize * 100) / 100,
    guaranteedPnl: Math.round(guaranteedPnl * 100) / 100
  }
}

/**
 * Calculate P&L if exiting position early at a given market price
 */
export function calculateEarlyExitPnl(
  positions: Position[],
  exitPrice: number
): { proceeds: number; totalPnl: number; roi: number } {
  const validPositions = positions.filter(p =>
    p.sizeUsd > 0 && p.entryPrice > 0 && p.entryPrice < 1
  )

  const totalCost = validPositions.reduce((sum, p) => sum + p.sizeUsd, 0)

  // Calculate exit value for each position
  const proceeds = validPositions.reduce((sum, pos) => {
    const shares = calculateShares(pos.sizeUsd, pos.entryPrice)

    if (pos.side === 'YES') {
      // Selling YES shares at exitPrice
      return sum + shares * exitPrice
    } else {
      // Selling NO shares at (1 - exitPrice)
      return sum + shares * (1 - exitPrice)
    }
  }, 0)

  const totalPnl = proceeds - totalCost
  const roi = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0

  return {
    proceeds: Math.round(proceeds * 100) / 100,
    totalPnl: Math.round(totalPnl * 100) / 100,
    roi: Math.round(roi * 100) / 100
  }
}

/**
 * Format a position for sharing/export
 */
export function formatPositionsForExport(
  positions: Position[],
  metrics: PayoffResult
): string {
  const lines: string[] = []

  // Positions
  positions.forEach((pos, i) => {
    if (pos.sizeUsd > 0 && pos.entryPrice > 0 && pos.entryPrice < 1) {
      lines.push(`Position ${i + 1}: ${pos.side} @ ${pos.entryPrice.toFixed(2)}, $${pos.sizeUsd.toFixed(2)}`)
    }
  })

  lines.push('---')
  lines.push(`Total Capital: $${metrics.totalCapital.toFixed(2)}`)

  const formatPnl = (pnl: number) => {
    const sign = pnl >= 0 ? '+' : ''
    return `${sign}$${pnl.toFixed(2)}`
  }

  const yesRoi = metrics.totalCapital > 0
    ? (metrics.yesWinsPnl / metrics.totalCapital * 100).toFixed(1)
    : '0.0'
  const noRoi = metrics.totalCapital > 0
    ? (metrics.noWinsPnl / metrics.totalCapital * 100).toFixed(1)
    : '0.0'

  lines.push(`YES wins: ${formatPnl(metrics.yesWinsPnl)} (${yesRoi}%)`)
  lines.push(`NO wins: ${formatPnl(metrics.noWinsPnl)} (${noRoi}%)`)

  if (metrics.breakEvenPrices.length > 0) {
    lines.push(`Break-even: ${metrics.breakEvenPrices.map(p => p.toFixed(2)).join(', ')}`)
  }

  if (metrics.isLockedProfit) {
    lines.push(`Locked Profit: ${formatPnl(Math.min(metrics.yesWinsPnl, metrics.noWinsPnl))}`)
  } else if (metrics.isLockedLoss) {
    lines.push(`Locked Loss: ${formatPnl(Math.max(metrics.yesWinsPnl, metrics.noWinsPnl))}`)
  }

  return lines.join('\n')
}
