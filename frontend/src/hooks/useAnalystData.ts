import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

// Auto-refresh interval (30 seconds)
const REFRESH_INTERVAL = 30 * 1000

// Query keys for analyst dashboard
const keys = {
  portfolioSummary: ['analyst', 'portfolio-summary'] as const,
  balances: ['analyst', 'balances'] as const,
  leaderboard: (sortBy: string) => ['analyst', 'leaderboard', sortBy] as const,
  equityCurve: (days: number, strategy?: string) => ['analyst', 'equity-curve', days, strategy] as const,
  funnelStats: (hours: number, strategy?: string) => ['analyst', 'funnel-stats', hours, strategy] as const,
  decisions: (params?: { strategy?: string; executed?: boolean; limit?: number; offset?: number }) =>
    ['analyst', 'decisions', params] as const,
  trades: (params?: { strategy?: string; limit?: number; offset?: number }) =>
    ['analyst', 'trades', params] as const,
  strategyMetrics: (name: string) => ['analyst', 'strategy', name, 'metrics'] as const,
  strategyDebug: (name: string) => ['analyst', 'strategy', name, 'debug'] as const,
}

// Portfolio summary with auto-refresh (30s)
export function usePortfolioSummary(autoRefresh = true) {
  return useQuery({
    queryKey: keys.portfolioSummary,
    queryFn: api.getPortfolioSummary,
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: REFRESH_INTERVAL / 2,
  })
}

// Strategy balances (per-strategy wallets) with auto-refresh
export function useStrategyBalances(autoRefresh = true) {
  return useQuery({
    queryKey: keys.balances,
    queryFn: api.getStrategyBalances,
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: REFRESH_INTERVAL / 2,
  })
}

// Leaderboard with sorting and auto-refresh
export function useLeaderboard(sortBy = 'total_pnl', autoRefresh = true) {
  return useQuery({
    queryKey: keys.leaderboard(sortBy),
    queryFn: () => api.getLeaderboard(sortBy),
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: REFRESH_INTERVAL / 2,
  })
}

// Equity curve data for charts
export function useEquityCurve(days = 30, strategy?: string, autoRefresh = true) {
  return useQuery({
    queryKey: keys.equityCurve(days, strategy),
    queryFn: () => api.getEquityCurve(days, strategy),
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: autoRefresh ? REFRESH_INTERVAL / 2 : Infinity,
  })
}

// Decision funnel statistics
export function useFunnelStats(hours = 24, strategy?: string, autoRefresh = true) {
  return useQuery({
    queryKey: keys.funnelStats(hours, strategy),
    queryFn: () => api.getFunnelStats(hours, strategy),
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: autoRefresh ? REFRESH_INTERVAL / 2 : Infinity,
  })
}

// Trade decisions (audit trail)
export function useDecisions(params?: {
  strategy?: string
  executed?: boolean
  limit?: number
  offset?: number
}, autoRefresh = true) {
  return useQuery({
    queryKey: keys.decisions(params),
    queryFn: () => api.getDecisions(params),
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: autoRefresh ? REFRESH_INTERVAL / 2 : Infinity,
  })
}

// Executed trades with strategy filtering
export function useTrades(params?: {
  strategy?: string
  limit?: number
  offset?: number
}, autoRefresh = true) {
  return useQuery({
    queryKey: keys.trades(params),
    queryFn: () => api.getTrades(params),
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: autoRefresh ? REFRESH_INTERVAL / 2 : Infinity,
  })
}

// Single strategy detailed metrics
export function useStrategyMetrics(name: string, autoRefresh = true) {
  return useQuery({
    queryKey: keys.strategyMetrics(name),
    queryFn: () => api.getStrategyMetrics(name),
    enabled: !!name,
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: autoRefresh ? REFRESH_INTERVAL / 2 : Infinity,
  })
}

// Strategy debug info
export function useStrategyDebug(name: string, autoRefresh = true) {
  return useQuery({
    queryKey: keys.strategyDebug(name),
    queryFn: () => api.getStrategyDebug(name),
    enabled: !!name,
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: autoRefresh ? REFRESH_INTERVAL / 2 : Infinity,
  })
}

// Capital analytics
export function useCapitalAnalytics() {
  return useQuery({
    queryKey: ['analyst', 'capital'],
    queryFn: api.getCapitalAnalytics,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL / 2,
  })
}

// Position analytics
export function usePositionAnalytics() {
  return useQuery({
    queryKey: ['analyst', 'positions'],
    queryFn: api.getPositionAnalytics,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL / 2,
  })
}

// Signal analytics
export function useSignalAnalytics(hours = 6, autoRefresh = true) {
  return useQuery({
    queryKey: ['analyst', 'signals', hours],
    queryFn: () => api.getSignalAnalytics(hours),
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: autoRefresh ? REFRESH_INTERVAL / 2 : Infinity,
  })
}

// Market pipeline (incoming opportunities)
export function useMarketPipeline() {
  return useQuery({
    queryKey: ['analyst', 'pipeline'],
    queryFn: api.getMarketPipeline,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL / 2,
  })
}

// Live trading summary (real money positions)
export function useLiveTradingSummary(autoRefresh = true) {
  return useQuery({
    queryKey: ['live', 'summary'],
    queryFn: api.getLiveTradingSummary,
    refetchInterval: autoRefresh ? REFRESH_INTERVAL : false,
    staleTime: REFRESH_INTERVAL / 2,
  })
}

// Hook for refreshing all analyst data at once
export function useRefreshAnalystData() {
  const queryClient = useQueryClient()

  return () => {
    queryClient.invalidateQueries({ queryKey: ['analyst'] })
    queryClient.invalidateQueries({ queryKey: ['executor', 'positions'] })
    queryClient.invalidateQueries({ queryKey: ['executor', 'signals'] })
    queryClient.invalidateQueries({ queryKey: ['live'] })
  }
}
