import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

// Query keys
const keys = {
  status: ['executor', 'status'] as const,
  balance: ['executor', 'balance'] as const,
  positions: (params?: Parameters<typeof api.getPositions>[0]) =>
    ['executor', 'positions', params] as const,
  signals: (params?: Parameters<typeof api.getSignals>[0]) =>
    ['executor', 'signals', params] as const,
  trades: (params?: Parameters<typeof api.getTrades>[0]) =>
    ['executor', 'trades', params] as const,
  strategies: ['strategies'] as const,
  strategy: (name: string) => ['strategies', name] as const,
  strategyStats: (name: string) => ['strategies', name, 'stats'] as const,
  config: ['executor', 'config'] as const,
  mode: ['executor', 'mode'] as const,
  wallet: ['executor', 'wallet'] as const,
  walletTrades: ['executor', 'wallet', 'trades'] as const,
}

// Executor status
export function useExecutorStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: api.getExecutorStatus,
    refetchInterval: 5000,
  })
}

// Executor balance
export function useExecutorBalance() {
  return useQuery({
    queryKey: keys.balance,
    queryFn: api.getExecutorBalance,
    refetchInterval: 10000,
  })
}

// Positions
export function usePositions(params?: Parameters<typeof api.getPositions>[0]) {
  return useQuery({
    queryKey: keys.positions(params),
    queryFn: () => api.getPositions(params),
    refetchInterval: 5000,
  })
}

// Signals
export function useSignals(params?: Parameters<typeof api.getSignals>[0]) {
  return useQuery({
    queryKey: keys.signals(params),
    queryFn: () => api.getSignals(params),
    refetchInterval: 3000,
  })
}

// Trades
export function useTrades(params?: Parameters<typeof api.getTrades>[0]) {
  return useQuery({
    queryKey: keys.trades(params),
    queryFn: () => api.getTrades(params),
    refetchInterval: 5000,
  })
}

// Strategies
export function useStrategies() {
  return useQuery({
    queryKey: keys.strategies,
    queryFn: api.getStrategies,
  })
}

// Single strategy
export function useStrategy(name: string) {
  return useQuery({
    queryKey: keys.strategy(name),
    queryFn: () => api.getStrategy(name),
    enabled: !!name,
  })
}

// Strategy stats
export function useStrategyStats(name: string) {
  return useQuery({
    queryKey: keys.strategyStats(name),
    queryFn: () => api.getStrategyStats(name),
    enabled: !!name,
  })
}

// Config
export function useExecutorConfig() {
  return useQuery({
    queryKey: keys.config,
    queryFn: api.getExecutorConfig,
  })
}

// Trading mode
export function useTradingMode() {
  return useQuery({
    queryKey: keys.mode,
    queryFn: api.getTradingMode,
  })
}

// Mutations

export function useClosePosition() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      positionId,
      exitPrice,
      reason,
    }: {
      positionId: number
      exitPrice?: number
      reason?: string
    }) => api.closePosition(positionId, exitPrice, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executor', 'positions'] })
      queryClient.invalidateQueries({ queryKey: keys.status })
      queryClient.invalidateQueries({ queryKey: keys.balance })
    },
  })
}

export function useResetPaperTrading() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (startingBalance?: number) => api.resetPaperTrading(startingBalance),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executor'] })
    },
  })
}

export function useEnableStrategy() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.enableStrategy(name, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.strategies })
      queryClient.invalidateQueries({ queryKey: keys.status })
    },
  })
}

export function useUpdateStrategyConfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      name,
      config,
    }: {
      name: string
      config: { enabled?: boolean; params?: Record<string, unknown> }
    }) => api.updateStrategyConfig(name, config),
    onSuccess: (_, { name }) => {
      queryClient.invalidateQueries({ queryKey: keys.strategy(name) })
      queryClient.invalidateQueries({ queryKey: keys.strategies })
    },
  })
}

export function useSetTradingMode() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (mode: string) => api.setTradingMode(mode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.mode })
      queryClient.invalidateQueries({ queryKey: keys.status })
      queryClient.invalidateQueries({ queryKey: keys.config })
    },
  })
}

export function useUpdateRiskConfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (config: Parameters<typeof api.updateRiskConfig>[0]) =>
      api.updateRiskConfig(config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.config })
      queryClient.invalidateQueries({ queryKey: keys.status })
    },
  })
}

export function useUpdateSizingConfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (config: Parameters<typeof api.updateSizingConfig>[0]) =>
      api.updateSizingConfig(config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.config })
    },
  })
}

export function useReloadConfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.reloadConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executor'] })
      queryClient.invalidateQueries({ queryKey: keys.strategies })
    },
  })
}

// Wallet hooks (live Polymarket wallet)
export function useWalletStatus() {
  return useQuery({
    queryKey: keys.wallet,
    queryFn: api.getWalletStatus,
    refetchInterval: 30000, // Refresh every 30 seconds
  })
}

export function useWalletTrades() {
  return useQuery({
    queryKey: keys.walletTrades,
    queryFn: api.getWalletTrades,
  })
}

export function useSyncWallet() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: api.syncWallet,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.wallet })
      queryClient.invalidateQueries({ queryKey: keys.walletTrades })
      queryClient.invalidateQueries({ queryKey: ['executor', 'positions'] })
    },
  })
}
