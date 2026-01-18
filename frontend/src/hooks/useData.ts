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
    refetchInterval: 30000,
  })
}

// Monitoring hooks
export function useMonitoringHealth() {
  return useQuery({
    queryKey: ['monitoringHealth'],
    queryFn: api.getMonitoringHealth,
    refetchInterval: 30000,
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

export function useTierTransitions(hours = 1) {
  return useQuery({
    queryKey: ['tierTransitions', hours],
    queryFn: () => api.getTierTransitions(hours),
    refetchInterval: 30000,
  })
}

export function useTaskActivity(limit = 50) {
  return useQuery({
    queryKey: ['taskActivity', limit],
    queryFn: () => api.getTaskActivity(limit),
    refetchInterval: 30000,
  })
}

export function useRedisStats() {
  return useQuery({
    queryKey: ['redisStats'],
    queryFn: api.getRedisStats,
    refetchInterval: 30000,
  })
}

// Lifecycle monitoring hooks
export function useLifecycleStatus() {
  return useQuery({
    queryKey: ['lifecycleStatus'],
    queryFn: api.getLifecycleStatus,
    refetchInterval: 30000,
  })
}

export function useLifecycleAnomalies(limit = 50) {
  return useQuery({
    queryKey: ['lifecycleAnomalies', limit],
    queryFn: () => api.getLifecycleAnomalies(limit),
    refetchInterval: 60000,
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

// Categorization monitoring hooks
export function useCategorizationMetrics() {
  return useQuery({
    queryKey: ['categorizationMetrics'],
    queryFn: api.getCategorizationMetrics,
    refetchInterval: 60000,
  })
}

export function useCategorizationRuns(limit = 25, offset = 0) {
  return useQuery({
    queryKey: ['categorizationRuns', limit, offset],
    queryFn: () => api.getCategorizationRuns(limit, offset),
    refetchInterval: 60000,
  })
}

export function useCategorizationRules(limit = 50, offset = 0) {
  return useQuery({
    queryKey: ['categorizationRules', limit, offset],
    queryFn: () => api.getCategorizationRules(limit, offset),
    refetchInterval: 120000,
  })
}

// GRID Integration hooks
export function useGRIDStats() {
  return useQuery({
    queryKey: ['gridStats'],
    queryFn: api.getGRIDStats,
    refetchInterval: 10000, // 10 second refresh
  })
}

export function useGRIDEvents(params?: Parameters<typeof api.getGRIDEvents>[0]) {
  return useQuery({
    queryKey: ['gridEvents', params],
    queryFn: () => api.getGRIDEvents(params),
    refetchInterval: 5000, // 5 second refresh for live events
  })
}

export function useGRIDMatches(includeClosed = false) {
  return useQuery({
    queryKey: ['gridMatches', includeClosed],
    queryFn: () => api.getGRIDMatches(includeClosed),
    refetchInterval: 30000,
  })
}

export function useGRIDPollerState() {
  return useQuery({
    queryKey: ['gridPollerState'],
    queryFn: api.getGRIDPollerState,
    refetchInterval: 5000, // 5 second refresh for live state
  })
}
