const API_BASE = ''

export interface Stats {
  timestamp: string
  markets: {
    total_tracked: number
    resolved: number
    tier_0: number
    tier_1: number
    tier_2: number
    tier_3: number
    tier_4: number
  }
  snapshots: {
    total: number
    today: number
  }
  trades: {
    total: number
    today: number
  }
  database: {
    size: string
    tables: Record<string, string>
  }
  websocket: {
    connected_markets: number
  }
}

export interface Market {
  id: number
  condition_id: string
  slug: string
  question: string
  tier: number
  active: boolean
  resolved: boolean
  outcome: string | null
  initial_price: number | null
  snapshot_count: number
  last_snapshot_at: string | null
  end_date: string | null
  category: string | null
}

export interface MarketDetail extends Market {
  description: string | null
  initial_volume: number | null
  initial_liquidity: number | null
  tracking_started_at: string | null
  hours_to_close: number | null
  event_id: string | null
  event_title: string | null
  yes_token_id: string | null
  resolved_at: string | null
  recent_snapshots: {
    id: number
    timestamp: string
    price: number | null
    spread: number | null
    volume_24h: number | null
    book_imbalance: number | null
    trade_count_1h: number | null
    whale_count_1h: number | null
  }[]
}

export interface Coverage {
  timestamp: string
  period: string
  overall_coverage_pct: number
  by_tier: Record<string, {
    markets: number
    expected_per_hour: number
    actual_per_hour: number
    coverage_pct: number
  }>
}

export interface TaskStatus {
  timestamp: string
  tasks: Record<string, {
    last_run: string | null
    last_status: string | null
    runs_24h: number
    success_24h: number
    success_rate_24h: number
    avg_duration_ms: number | null
  }>
}

export interface TaskRun {
  id: number
  task_name: string
  task_id: string
  tier: number | null
  started_at: string
  completed_at: string | null
  duration_ms: number | null
  status: string
  markets_processed: number | null
  rows_inserted: number | null
  error_message: string | null
}

export interface Gap {
  market_id: number
  condition_id: string
  question: string | null
  tier: number
  last_snapshot_at: string | null
  seconds_since_last: number | null
  expected_interval: number
}

// Monitoring types
export interface MonitoringHealth {
  timestamp: string
  websocket: {
    status: 'healthy' | 'stale' | 'disconnected'
    connected_markets: number
    last_activity: string | null
    seconds_since_activity: number | null
    trades_last_hour: number
    trades_per_minute: number
  }
  tasks: {
    tasks_last_10min: number
    errors_last_10min: number
    error_rate_pct: number
  }
}

export interface MonitoringError {
  id: number
  timestamp: string
  task: string
  full_task_name: string
  tier: number | null
  error: string | null
  traceback: string | null
}

export interface FieldCompleteness {
  timestamp: string
  overall: {
    avg_completeness_pct: number
    total_snapshots_1h: number
    total_optional_fields: number
  }
  by_category: Record<string, {
    fields_total: number
    avg_populated: number
    pct: number
  }>
  by_tier: Record<string, {
    count: number
    avg_completeness_pct: number
  }>
}

export interface WebSocketCoverage {
  timestamp: string
  should_subscribe: number
  actually_subscribed: number
  missing_count: number
  extra_count: number
  missing_markets: { condition_id: string; tier: number; question: string | null }[]
  status: 'ok' | 'degraded'
}

export interface SubscriptionHealth {
  timestamp: string
  total_subscribed: number
  active: number           // Trade in last 10 min
  active_pct: number
  quiet: number            // Trade 10min-1hr ago
  dormant: number          // Trade >1hr ago
  silent: number           // Never received trade
  quiet_markets: { condition_id: string; seconds_since_event: number; tier?: number; question?: string }[]
  dormant_markets: { condition_id: string; seconds_since_event: number; tier?: number; question?: string }[]
  status: 'ok' | 'warning' | 'degraded'
  note: string
}

export interface ConnectionStatus {
  timestamp: string
  websocket: {
    connections: number
    max_per_connection: number
    total_capacity: number
    markets_subscribed: number
    utilization_pct: number
    by_tier: Record<string, number>
    note: string
  }
  database: {
    status: string
    pool_size: number
    connections_in_use: number
  }
  redis: {
    status: string
    connected_clients: number
  }
  api_clients: {
    note: string
    gamma: { status: string; rate_limit: string }
    clob: { status: string; rate_limit: string }
  }
}

// Database browser types
export interface TableInfo {
  name: string
  row_count: number
}

export interface TableData {
  table: string
  total: number
  limit: number
  offset: number
  columns: string[]
  items: Record<string, unknown>[]
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

export const api = {
  getStats: () => fetchJson<Stats>('/api/stats'),

  getMarkets: (params?: {
    tier?: number
    active?: boolean
    resolved?: boolean
    search?: string
    limit?: number
    offset?: number
  }) => {
    const searchParams = new URLSearchParams()
    if (params?.tier !== undefined) searchParams.set('tier', String(params.tier))
    if (params?.active !== undefined) searchParams.set('active', String(params.active))
    if (params?.resolved !== undefined) searchParams.set('resolved', String(params.resolved))
    if (params?.search) searchParams.set('search', params.search)
    if (params?.limit) searchParams.set('limit', String(params.limit))
    if (params?.offset) searchParams.set('offset', String(params.offset))
    const qs = searchParams.toString()
    return fetchJson<{ total: number; items: Market[] }>(`/api/markets${qs ? `?${qs}` : ''}`)
  },

  getMarket: (id: number) => fetchJson<MarketDetail>(`/api/markets/${id}`),

  getCoverage: () => fetchJson<Coverage>('/api/data-quality/coverage'),

  getGaps: () => fetchJson<{ gap_count: number; gaps: Gap[] }>('/api/data-quality/gaps'),

  getTaskStatus: () => fetchJson<TaskStatus>('/api/tasks/status'),

  getTaskRuns: (params?: { task_name?: string; limit?: number }) => {
    const searchParams = new URLSearchParams()
    if (params?.task_name) searchParams.set('task_name', params.task_name)
    if (params?.limit) searchParams.set('limit', String(params.limit))
    const qs = searchParams.toString()
    return fetchJson<{ total: number; items: TaskRun[] }>(`/api/tasks/runs${qs ? `?${qs}` : ''}`)
  },

  getHealth: () => fetchJson<{ status: string; timestamp: string }>('/health'),

  // Monitoring endpoints
  getMonitoringHealth: () => fetchJson<MonitoringHealth>('/api/monitoring/health'),
  getMonitoringErrors: (limit = 50) => fetchJson<{ total: number; items: MonitoringError[] }>(`/api/monitoring/errors?limit=${limit}`),
  getFieldCompleteness: () => fetchJson<FieldCompleteness>('/api/monitoring/field-completeness'),
  getWebSocketCoverage: () => fetchJson<WebSocketCoverage>('/api/monitoring/websocket-coverage'),
  getSubscriptionHealth: () => fetchJson<SubscriptionHealth>('/api/monitoring/subscription-health'),
  getConnectionStatus: () => fetchJson<ConnectionStatus>('/api/monitoring/connections'),

  // Database browser endpoints
  getTables: () => fetchJson<{ tables: TableInfo[] }>('/api/database/tables'),
  getTableData: (tableName: string, params?: {
    limit?: number
    offset?: number
    order_by?: string
    order?: 'asc' | 'desc'
  }) => {
    const searchParams = new URLSearchParams()
    if (params?.limit) searchParams.set('limit', String(params.limit))
    if (params?.offset) searchParams.set('offset', String(params.offset))
    if (params?.order_by) searchParams.set('order_by', params.order_by)
    if (params?.order) searchParams.set('order', params.order)
    const qs = searchParams.toString()
    return fetchJson<TableData>(`/api/database/tables/${tableName}${qs ? `?${qs}` : ''}`)
  },
}
