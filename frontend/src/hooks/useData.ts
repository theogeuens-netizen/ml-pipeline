import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: api.getStats,
  })
}

export function useMarkets(params?: Parameters<typeof api.getMarkets>[0]) {
  return useQuery({
    queryKey: ['markets', params],
    queryFn: () => api.getMarkets(params),
  })
}

export function useMarket(id: number) {
  return useQuery({
    queryKey: ['market', id],
    queryFn: () => api.getMarket(id),
    enabled: !!id,
  })
}

export function useCoverage() {
  return useQuery({
    queryKey: ['coverage'],
    queryFn: api.getCoverage,
  })
}

export function useGaps() {
  return useQuery({
    queryKey: ['gaps'],
    queryFn: api.getGaps,
  })
}

export function useTaskStatus() {
  return useQuery({
    queryKey: ['taskStatus'],
    queryFn: api.getTaskStatus,
  })
}

export function useTaskRuns(params?: Parameters<typeof api.getTaskRuns>[0]) {
  return useQuery({
    queryKey: ['taskRuns', params],
    queryFn: () => api.getTaskRuns(params),
  })
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: api.getHealth,
    refetchInterval: 10000,
  })
}

// Monitoring hooks
export function useMonitoringHealth() {
  return useQuery({
    queryKey: ['monitoringHealth'],
    queryFn: api.getMonitoringHealth,
    refetchInterval: 10000,
  })
}

export function useMonitoringErrors(limit = 50) {
  return useQuery({
    queryKey: ['monitoringErrors', limit],
    queryFn: () => api.getMonitoringErrors(limit),
    refetchInterval: 30000,
  })
}

export function useFieldCompleteness() {
  return useQuery({
    queryKey: ['fieldCompleteness'],
    queryFn: api.getFieldCompleteness,
    refetchInterval: 60000,
  })
}

export function useWebSocketCoverage() {
  return useQuery({
    queryKey: ['webSocketCoverage'],
    queryFn: api.getWebSocketCoverage,
    refetchInterval: 30000,
  })
}

export function useSubscriptionHealth() {
  return useQuery({
    queryKey: ['subscriptionHealth'],
    queryFn: api.getSubscriptionHealth,
    refetchInterval: 30000,
  })
}

export function useConnectionStatus() {
  return useQuery({
    queryKey: ['connectionStatus'],
    queryFn: api.getConnectionStatus,
    refetchInterval: 30000,
  })
}

// Database browser hooks
export function useTables() {
  return useQuery({
    queryKey: ['tables'],
    queryFn: api.getTables,
  })
}

export function useTableData(tableName: string, params?: Parameters<typeof api.getTableData>[1]) {
  return useQuery({
    queryKey: ['tableData', tableName, params],
    queryFn: () => api.getTableData(tableName, params),
    enabled: !!tableName,
  })
}
