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

export interface TierTransition {
  market: string
  from_tier: number
  to_tier: number
  at: string
  hours_to_close: number | null
  reason: string | null
}

export interface TierTransitions {
  timestamp: string
  period_hours: number
  summary: Record<string, number>
  total_transitions: number
  recent: TierTransition[]
}

export interface TaskActivity {
  timestamp: string
  by_task: Record<string, { success: number; failed: number; running: number }>
  recent: {
    id: number
    task: string
    tier: number | null
    status: string
    started_at: string | null
    duration_ms: number | null
    markets_processed: number | null
    rows_inserted: number | null
    error: string | null
  }[]
}

export interface LifecycleStatus {
  timestamp: string
  trading_status_distribution: Record<string, number>
  uma_status_distribution: Record<string, number>
  alerts: {
    markets_stuck_pending_24h: number
  }
  recent_activity_24h: {
    closed: number
    resolved: number
  }
}

export interface LifecycleAnomaly {
  type: string
  market_id: number
  slug: string
  end_date?: string | null
  closed_at?: string | null
  resolved_at?: string | null
  uma_status_updated_at?: string | null
  severity: 'high' | 'medium' | 'info'
}

export interface LifecycleAnomalies {
  timestamp: string
  total_anomalies: number
  anomalies: LifecycleAnomaly[]
}

export interface RedisStats {
  timestamp: string
  memory_used_mb: number
  memory_peak_mb: number
  connected_clients: number
  total_keys: number
  keys_by_pattern: Record<string, number>
  uptime_seconds: number
  ops_per_sec: number
}

// Executor types
export interface ExecutorStatus {
  mode: 'paper' | 'live'
  running: boolean
  balance: number
  total_value: number
  stats: ExecutorStats
  enabled_strategies: string[]
  risk_limits: {
    max_position_usd: number
    max_total_exposure_usd: number
    max_positions_per_strategy: number | null
    max_positions: number
    max_drawdown_pct: number
  }
}

export interface ExecutorStats {
  balance: number
  starting_balance?: number
  total_pnl?: number
  total_pnl_pct?: number
  realized_pnl?: number
  high_water_mark?: number
  low_water_mark?: number
  max_drawdown?: number
  open_positions: number
  total_trades: number
  closed_positions?: number
  winning_trades?: number
  losing_trades?: number
  win_rate?: number
}

export interface ExecutorPosition {
  id: number
  is_paper: boolean
  strategy_name: string
  market_id: number
  token_id: string
  side: string
  status: string
  entry_price: number | null
  exit_price: number | null
  current_price: number | null
  size_shares: number | null
  cost_basis: number | null
  current_value: number | null
  unrealized_pnl: number | null
  unrealized_pnl_pct: number | null
  realized_pnl: number | null
  entry_time: string | null
  exit_time: string | null
  close_reason: string | null
  hedge_position_id: number | null
}

export interface ExecutorSignal {
  id: number
  strategy_name: string
  market_id: number
  token_id: string
  side: string
  status: string
  reason: string
  edge: number | null
  confidence: number | null
  price_at_signal: number | null
  best_bid: number | null
  best_ask: number | null
  suggested_size_usd: number | null
  status_reason: string | null
  created_at: string | null
  processed_at: string | null
}

export interface ExecutorTrade {
  id: number
  order_id: number
  position_id: number | null
  strategy_name: string | null
  is_paper: boolean
  price: number | null
  size_shares: number | null
  size_usd: number | null
  side: string
  fee_usd: number | null
  timestamp: string | null
}

export interface ExecutorOrder {
  id: number
  signal_id: number
  is_paper: boolean
  token_id: string
  side: string
  order_type: string
  status: string
  limit_price: number | null
  executed_price: number | null
  size_usd: number | null
  size_shares: number | null
  filled_shares: number | null
  polymarket_order_id: string | null
  submitted_at: string | null
  filled_at: string | null
  error_message: string | null
}

export interface Strategy {
  name: string
  description: string
  version: string
  enabled: boolean
  params: Record<string, unknown>
}

export interface StrategyStats {
  strategy: string
  signals: {
    total: number
    pending: number
    approved: number
    executed: number
    rejected: number
  }
  positions: {
    total: number
    open: number
    closed: number
    winning: number
    losing: number
    win_rate: number
  }
  pnl: {
    total_realized: number
    average_per_trade: number
  }
}

export interface ExecutorConfig {
  mode: string
  settings: {
    scan_interval_seconds: number
    log_level: string
  }
  risk: {
    max_position_usd: number
    max_total_exposure_usd: number
    max_positions_per_strategy: number
    max_positions: number
    max_drawdown_pct: number
  }
  sizing: {
    method: string
    fixed_amount_usd: number
    kelly_fraction: number
    max_size_usd: number | null
  }
  execution: {
    default_order_type: string
    limit_offset_bps: number
    market_slippage_bps: number
    max_retry_attempts: number
  }
  filters: {
    min_liquidity_usd: number
    min_volume_24h_usd: number
    excluded_keywords: string[]
  }
  strategies: Record<string, { enabled: boolean; params: Record<string, unknown> }>
}

// Wallet types
export interface WalletPosition {
  asset_id: string
  market: string
  outcome: string
  size: number
  cost_basis: number
  avg_price: number
  trades: string[]
}

export interface WalletStatus {
  success: boolean
  wallet_address: string
  usdc_balance: number
  position_value: number
  total_value: number
  positions: WalletPosition[]
  open_orders: number
  error?: string
}

export interface WalletTrade {
  id: string
  market: string
  asset_id: string
  side: string
  size: string
  price: string
  outcome: string
  status: string
  transaction_hash: string
}

export interface WalletSyncResult {
  success: boolean
  synced: number
  updated: number
  errors: string[]
  total_positions: number
  usdc_balance: number
}

// Analyst dashboard types
export interface StrategyBalance {
  name: string
  allocated_usd: number
  current_usd: number
  position_value: number
  portfolio_value: number
  total_pnl: number
  realized_pnl: number
  unrealized_pnl: number
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number
  max_drawdown_pct: number
}

export interface StrategyBalancesResponse {
  total: number
  total_allocated: number
  total_current: number
  total_pnl: number
  strategies: StrategyBalance[]
}

export interface LeaderboardStrategy {
  strategy_name: string
  allocated_usd: number
  current_usd: number
  total_pnl: number
  realized_pnl: number
  unrealized_pnl: number
  total_return_pct: number
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number
  total_cost_basis: number
  sharpe_ratio: number | null
  sortino_ratio: number | null
  max_drawdown_usd: number
  max_drawdown_pct: number
  current_drawdown_pct: number
  avg_win_usd: number | null
  avg_loss_usd: number | null
  profit_factor: number | null
  expectancy_usd: number | null
  avg_hold_hours: number | null
  open_positions: number
  first_trade: string | null
  last_trade: string | null
}

export interface LeaderboardResponse {
  sort_by: string
  total: number
  strategies: LeaderboardStrategy[]
}

export interface EquityCurveDataPoint {
  date: string
  daily_pnl: number
  trade_count: number
  value: number
  cumulative_pnl: number
}

export interface EquityCurveChartPoint {
  date: string
  realized: number
  unrealized: number
  total: number
  baseline: number
}

export interface EquityCurveSummary {
  total_allocated: number
  total_realized: number
  total_unrealized: number
  portfolio_value: number
}

export interface EquityCurveResponse {
  start_date: string
  end_date: string
  days: number
  strategies: Record<string, EquityCurveDataPoint[]>
  total: EquityCurveDataPoint[]
  chart_data: EquityCurveChartPoint[]
  summary: EquityCurveSummary
  allocations: Record<string, number>
}

export interface FunnelStatsResponse {
  period_hours: number
  total_decisions: number
  executed: number
  rejected: number
  profitable: number
  execution_rate: number
  win_rate: number
  rejection_reasons: Record<string, number>
  by_strategy: Record<string, { total: number; executed: number; rejected: number }>
}

export interface TradeDecision {
  id: number
  timestamp: string | null
  strategy_name: string
  strategy_sha: string
  market_id: number | null
  condition_id: string | null
  market_snapshot: Record<string, unknown> | null
  decision_inputs: Record<string, unknown> | null
  signal_side: string | null
  signal_reason: string | null
  signal_edge: number | null
  signal_size_usd: number | null
  executed: boolean
  rejected_reason: string | null
  execution_price: number | null
  position_id: number | null
}

export interface DecisionsResponse {
  total: number
  limit: number
  offset: number
  items: TradeDecision[]
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

// Categorization metrics
export interface CategorizationMetrics {
  timestamp: string
  last_run: {
    run_id: string
    started_at: string | null
    completed_at: string | null
    markets_saved: number | null
    total_tokens: number | null
    status?: string | null
  } | null
  last_success: {
    run_id: string
    started_at: string | null
    completed_at: string | null
    markets_saved: number | null
    total_tokens: number | null
    status?: string | null
  } | null
  runs_24h: number
  success_24h: number
  markets_saved_24h: number
  tokens_24h: number
  quarantined_24h: number
}

export interface CategorizationRun {
  run_id: string
  started_at: string | null
  completed_at: string | null
  model: string | null
  batch_size: number | null
  markets_fetched: number | null
  markets_sent: number | null
  markets_saved: number | null
  quarantined: number | null
  retry_count: number | null
  status: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  error: string | null
}

export interface RuleStat {
  id: number
  name: string
  l1: string
  l2: string
  times_matched: number
  times_validated: number
  times_correct: number
  accuracy: number | null
  enabled: boolean
}

// CS:GO Strategy types
export interface CSGOTeam {
  team_name: string
  wins: number
  losses: number
  total_matches: number
  win_rate_pct: number
  updated_at: string | null
}

export interface CSGOTeamsResponse {
  total: number
  offset: number
  limit: number
  teams: CSGOTeam[]
}

export interface CSGOH2HRecord {
  opponent: string
  wins: number
  losses: number
  total: number
}

export interface CSGOTeamDetails {
  team_name: string
  wins: number
  losses: number
  total_matches: number
  win_rate_pct: number
  updated_at: string | null
  h2h_records: CSGOH2HRecord[]
}

export interface CSGOH2H {
  team1: string
  team2: string
  team1_wins: number
  team2_wins: number
  total_matches: number
  found: boolean
}

export interface CSGOPosition {
  id: number
  strategy_name: string
  market_id: number | null
  market_question: string | null
  // Team info from csgo_matches
  team_yes: string | null
  team_no: string | null
  bet_on_team: string | null
  bet_on_side: string | null  // YES or NO
  // Match detail from csgo_matches
  market_type: string | null  // moneyline, child_moneyline
  format: string | null  // BO1, BO3
  group_item_title: string | null  // Match Winner, Map 1 Winner, etc.
  tournament: string | null
  game_start_time: string | null
  // Prices
  entry_price: number
  current_token_price: number | null
  yes_price: number | null
  no_price: number | null
  // Position details
  token_id: string | null
  side: string
  entry_time: string | null
  size_shares: number
  cost_basis: number
  current_value: number | null
  unrealized_pnl: number
  realized_pnl: number
  status: string
  close_reason: string | null
  is_hedge: boolean
  hedge_position_id: number | null
  exit_price: number | null
  exit_time: string | null
}

export interface CSGOPositionsResponse {
  total: number
  offset: number
  limit: number
  positions: CSGOPosition[]
}

export interface CSGOActiveMarket {
  market_id: number
  question: string
  team_a: string | null
  team_b: string | null
  team_a_winrate: number | null
  team_b_winrate: number | null
  favorite_side: string
  favorite_price: number
  winrate_diff: number
  signal_strength: 'none' | 'base' | 'strong' | 'very_strong'
  hours_to_close: number | null
  best_bid: number | null
  best_ask: number | null
}

export interface CSGOActiveMarketsResponse {
  total: number
  hours_ahead: number
  opportunities: CSGOActiveMarket[]
}

export interface CSGOMatch {
  market_id: number
  question: string
  team_a: string | null
  team_b: string | null
  team_a_winrate: number | null
  team_b_winrate: number | null
  favorite_side: string | null
  favorite_price: number | null
  current_price: number | null
  best_bid: number | null
  best_ask: number | null
  winrate_diff: number | null
  signal_strength: 'none' | 'base' | 'strong' | 'very_strong'
  size_usd: number | null
  hours_to_close: number | null
  end_date: string | null
  meets_criteria: boolean
  criteria: {
    has_teams: boolean
    has_winrates: boolean
    price_in_range: boolean
    time_in_range: boolean
    has_edge: boolean
  }
}

export interface CSGOMatchesResponse {
  total: number
  meeting_criteria: number
  matches: CSGOMatch[]
}

export interface CSGOPerformanceSummary {
  total: number
  open: number
  hedged: number
  stopped: number
  resolved: number
  total_pnl: number
  unrealized_pnl: number
  realized_pnl: number
}

export interface CSGOPeriodStats {
  total_positions: number
  closed_positions: number
  wins: number
  losses: number
  win_rate: number
  total_realized_pnl: number
}

export interface CSGOPerformanceResponse {
  days: number
  summary: CSGOPerformanceSummary
  period_stats: CSGOPeriodStats
}

// CS:GO Pipeline types
export interface CSGOPipelineMatch {
  id: number
  market_id: number
  gamma_id: number | null
  condition_id: string
  team_yes: string | null
  team_no: string | null
  game_start_time: string | null
  game_start_override: boolean
  end_date: string | null
  tournament: string | null
  format: string | null
  market_type: string | null
  group_item_title: string | null
  subscribed: boolean
  // Lifecycle fields
  closed: boolean
  resolved: boolean
  outcome: string | null
  accepting_orders: boolean
  last_status_check: string | null
  // Market data
  tier: number | null
  current_price: number | null
  best_bid: number | null
  best_ask: number | null
  spread: number | null
  volume_24h: number | null
  liquidity: number | null
  created_at: string | null
  updated_at: string | null
}

export interface CSGOPipelineMatchesResponse {
  total: number
  offset: number
  limit: number
  matches: CSGOPipelineMatch[]
}

export interface CSGOPipelineEventMarket {
  id: number
  market_id: number
  market_type: string
  label: string
  group_item_title: string | null
  format: string | null
  current_price: number | null
  spread: number | null
  volume: number | null
  volume_24h: number | null
  liquidity: number | null
  subscribed: boolean
  closed: boolean
  resolved: boolean
}

export interface CSGOPipelineEvent {
  event_key: string
  team_yes: string
  team_no: string
  tournament: string | null
  format: string | null
  game_start_time: string | null
  is_live: boolean
  main_price: number | null
  main_spread: number | null
  market_count: number
  markets: CSGOPipelineEventMarket[]
}

export interface CSGOPipelineEventsResponse {
  total: number
  events: CSGOPipelineEvent[]
}

export interface CSGOPipelineSignal {
  market_id: number
  match_id: number
  condition_id: string
  token_type: string
  event_type: string
  timestamp: string
  price?: number
  size?: number
  side?: string
  best_bid?: number
  best_ask?: number
  spread?: number
  mid_price?: number
  price_velocity_1m?: number
  _message_id?: string
}

export interface CSGOPipelineSignalsResponse {
  stream_stats: {
    length: number
    first_entry?: string
    last_entry?: string
    error?: string
  }
  count: number
  signals: CSGOPipelineSignal[]
}

export interface CSGOPipelineSyncResponse {
  sync: {
    new: number
    existing: number
    total: number
  }
  enrichment: {
    enriched: number
    failed: number
    skipped: number
  }
}

export interface CSGOPositionListItem {
  id: number
  market_id: number
  label: string
  status: string
}

export interface CSGOPositionListResponse {
  positions: CSGOPositionListItem[]
}

export interface CSGOPriceDataPoint {
  timestamp: string
  yes_price: number | null
  no_price: number | null
  best_bid: number | null
  best_ask: number | null
  volume_24h: number | null
}

export interface CSGOTradeMarker {
  timestamp: string
  side: string
  price: number | null
  size_usd: number | null
  position_id: number
  bet_on_team: string | null
  bet_on_side: string | null
  strategy_name: string | null
  spread: number | null
  slippage: number | null
}

export interface CSGOPriceHistoryResponse {
  market_id: number
  match_info: {
    team_yes: string | null
    team_no: string | null
    format: string | null
    group_item_title: string | null
    tournament: string | null
    game_start_time: string | null
  } | null
  price_data: CSGOPriceDataPoint[]
  trades: CSGOTradeMarker[]
  data_points: number
  data_source: string
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
  getTierTransitions: (hours = 1) => fetchJson<TierTransitions>(`/api/monitoring/tier-transitions?hours=${hours}`),
  getTaskActivity: (limit = 50) => fetchJson<TaskActivity>(`/api/monitoring/task-activity?limit=${limit}`),
  getRedisStats: () => fetchJson<RedisStats>('/api/monitoring/redis-stats'),

  // Lifecycle monitoring endpoints
  getLifecycleStatus: () => fetchJson<LifecycleStatus>('/api/monitoring/lifecycle-status'),
  getLifecycleAnomalies: (limit = 50) => fetchJson<LifecycleAnomalies>(`/api/monitoring/lifecycle-anomalies?limit=${limit}`),

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

  // Executor endpoints
  getExecutorStatus: () => fetchJson<ExecutorStatus>('/api/executor/status'),
  getExecutorBalance: () => fetchJson<{ mode: string; paper: ExecutorStats; live: ExecutorStats | null }>('/api/executor/balance'),

  getPositions: (params?: {
    status?: string
    strategy?: string
    is_paper?: boolean
    limit?: number
    offset?: number
  }) => {
    const searchParams = new URLSearchParams({ exclude_csgo: 'true' })
    if (params?.status) searchParams.set('status', params.status)
    if (params?.strategy) searchParams.set('strategy', params.strategy)
    if (params?.is_paper !== undefined) searchParams.set('is_paper', String(params.is_paper))
    if (params?.limit) searchParams.set('limit', String(params.limit))
    if (params?.offset) searchParams.set('offset', String(params.offset))
    return fetchJson<{ total: number; items: ExecutorPosition[] }>(`/api/executor/positions?${searchParams}`)
  },

  closePosition: async (positionId: number, exitPrice?: number, reason?: string) => {
    const searchParams = new URLSearchParams()
    if (exitPrice) searchParams.set('exit_price', String(exitPrice))
    if (reason) searchParams.set('reason', reason)
    const qs = searchParams.toString()
    const response = await fetch(`${API_BASE}/api/executor/positions/${positionId}/close${qs ? `?${qs}` : ''}`, {
      method: 'POST',
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json()
  },

  getSignals: (params?: {
    status?: string
    strategy?: string
    limit?: number
    offset?: number
  }) => {
    const searchParams = new URLSearchParams()
    if (params?.status) searchParams.set('status', params.status)
    if (params?.strategy) searchParams.set('strategy', params.strategy)
    if (params?.limit) searchParams.set('limit', String(params.limit))
    if (params?.offset) searchParams.set('offset', String(params.offset))
    const qs = searchParams.toString()
    return fetchJson<{ total: number; items: ExecutorSignal[] }>(`/api/executor/signals${qs ? `?${qs}` : ''}`)
  },

  getTrades: (params?: {
    is_paper?: boolean
    strategy?: string
    limit?: number
    offset?: number
  }) => {
    const searchParams = new URLSearchParams()
    if (params?.is_paper !== undefined) searchParams.set('is_paper', String(params.is_paper))
    if (params?.strategy) searchParams.set('strategy', params.strategy)
    if (params?.limit) searchParams.set('limit', String(params.limit))
    if (params?.offset) searchParams.set('offset', String(params.offset))
    const qs = searchParams.toString()
    return fetchJson<{ total: number; items: ExecutorTrade[] }>(`/api/executor/trades${qs ? `?${qs}` : ''}`)
  },

  resetPaperTrading: async (startingBalance?: number) => {
    const searchParams = new URLSearchParams()
    if (startingBalance) searchParams.set('starting_balance', String(startingBalance))
    const qs = searchParams.toString()
    const response = await fetch(`${API_BASE}/api/executor/reset-paper${qs ? `?${qs}` : ''}`, {
      method: 'POST',
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json()
  },

  // Strategy endpoints
  getStrategies: () => fetchJson<{ total: number; items: Strategy[] }>('/api/strategies'),

  getStrategy: (name: string) => fetchJson<Strategy & {
    state: unknown;
    statistics: { signals_generated: number; signals_executed: number; signals_rejected: number };
    sizing: { method: string; fixed_amount_usd: number } | null;
    execution: { order_type: string; limit_offset_bps: number } | null;
  }>(`/api/strategies/${name}`),

  getStrategyStats: (name: string) => fetchJson<StrategyStats>(`/api/strategies/${name}/stats`),

  enableStrategy: async (name: string, enabled: boolean) => {
    const response = await fetch(`${API_BASE}/api/strategies/${name}/enable`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json()
  },

  updateStrategyConfig: async (name: string, config: { enabled?: boolean; params?: Record<string, unknown> }) => {
    const response = await fetch(`${API_BASE}/api/strategies/${name}/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json()
  },

  // Executor config endpoints
  getExecutorConfig: () => fetchJson<ExecutorConfig>('/api/executor/config'),

  getTradingMode: () => fetchJson<{ mode: string; available_modes: string[] }>('/api/executor/config/mode'),

  setTradingMode: async (mode: string) => {
    const response = await fetch(`${API_BASE}/api/executor/config/mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json()
  },

  updateRiskConfig: async (config: Partial<{
    max_position_usd: number
    max_total_exposure_usd: number
    max_positions_per_strategy: number
    max_positions: number
    max_drawdown_pct: number
  }>) => {
    const response = await fetch(`${API_BASE}/api/executor/config/risk`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json()
  },

  updateSizingConfig: async (config: Partial<{
    method: string
    fixed_amount_usd: number
    kelly_fraction: number
    max_size_usd: number
  }>) => {
    const response = await fetch(`${API_BASE}/api/executor/config/sizing`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json()
  },

  reloadConfig: async () => {
    const response = await fetch(`${API_BASE}/api/executor/config/reload`, {
      method: 'POST',
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json()
  },

  // Wallet endpoints (live Polymarket wallet)
  getWalletStatus: () => fetchJson<WalletStatus>('/api/executor/wallet'),

  getWalletTrades: () => fetchJson<{ success: boolean; total: number; trades: WalletTrade[] }>('/api/executor/wallet/trades'),

  getLiveTradingSummary: () => fetchJson<LiveTradingSummary>('/api/executor/live/summary'),

  syncWallet: async () => {
    const response = await fetch(`${API_BASE}/api/executor/wallet/sync`, {
      method: 'POST',
    })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json() as Promise<WalletSyncResult>
  },

  // Analyst dashboard endpoints (exclude_csgo=true filters out CSGO strategies)
  getStrategyBalances: () => fetchJson<StrategyBalancesResponse>('/api/executor/strategies/balances?exclude_csgo=true'),

  getLeaderboard: (sortBy = 'total_pnl') =>
    fetchJson<LeaderboardResponse>(`/api/executor/strategies/leaderboard?sort_by=${sortBy}&exclude_csgo=true`),

  getEquityCurve: (days = 30, strategy?: string) => {
    const params = new URLSearchParams({ days: String(days), exclude_csgo: 'true' })
    if (strategy) params.set('strategy', strategy)
    return fetchJson<EquityCurveResponse>(`/api/executor/strategies/equity-curve?${params}`)
  },

  getFunnelStats: (hours = 24, strategy?: string) => {
    const params = new URLSearchParams({ hours: String(hours) })
    if (strategy) params.set('strategy', strategy)
    return fetchJson<FunnelStatsResponse>(`/api/executor/strategies/funnel-stats?${params}`)
  },

  getDecisions: (params?: {
    strategy?: string
    executed?: boolean
    limit?: number
    offset?: number
  }) => {
    const searchParams = new URLSearchParams()
    if (params?.strategy) searchParams.set('strategy', params.strategy)
    if (params?.executed !== undefined) searchParams.set('executed', String(params.executed))
    if (params?.limit) searchParams.set('limit', String(params.limit))
    if (params?.offset) searchParams.set('offset', String(params.offset))
    const qs = searchParams.toString()
    return fetchJson<DecisionsResponse>(`/api/executor/decisions${qs ? `?${qs}` : ''}`)
  },

  getStrategyMetrics: (name: string) => fetchJson<LeaderboardStrategy>(`/api/executor/strategies/${name}/metrics`),

  getStrategyDebug: (name: string) => fetchJson<{
    strategy_name: string
    params: Record<string, unknown>
    recent_decisions: TradeDecision[]
    funnel_stats: Record<string, unknown>
  }>(`/api/executor/strategies/${name}/debug`),

  // Analytics endpoints
  getCapitalAnalytics: () => fetchJson<CapitalAnalytics>('/api/executor/analytics/capital'),

  getPositionAnalytics: () => fetchJson<PositionAnalytics>('/api/executor/analytics/positions'),

  getSignalAnalytics: (hours = 6) => fetchJson<SignalAnalytics>(`/api/executor/analytics/signals?hours=${hours}`),

  getMarketPipeline: () => fetchJson<MarketPipeline>('/api/executor/analytics/pipeline'),

  // Portfolio summary for dashboard header
  getPortfolioSummary: () => fetchJson<PortfolioSummary>('/api/executor/portfolio/summary'),

  // CSV export URL builder
  getPositionsExportUrl: (params?: { status?: string; strategy?: string }) => {
    const searchParams = new URLSearchParams({ exclude_csgo: 'true' })
    if (params?.status) searchParams.set('status', params.status)
    if (params?.strategy) searchParams.set('strategy', params.strategy)
    return `/api/executor/positions/export?${searchParams}`
  },

  // Categorization monitoring
  getCategorizationMetrics: () => fetchJson<CategorizationMetrics>('/api/categorization/metrics'),
  getCategorizationRuns: (limit = 50, offset = 0) =>
    fetchJson<{ items: CategorizationRun[]; limit: number; offset: number }>(
      `/api/categorization/runs?limit=${limit}&offset=${offset}`
    ),
  getCategorizationRules: (limit = 50, offset = 0) =>
    fetchJson<{ items: RuleStat[]; limit: number; offset: number }>(
      `/api/categorization/rules?limit=${limit}&offset=${offset}`
    ),

  // CS:GO Strategy
  getCSGOTeams: (limit = 50, offset = 0, sortBy = 'win_rate_pct', minMatches = 0) =>
    fetchJson<CSGOTeamsResponse>(`/api/csgo/teams?limit=${limit}&offset=${offset}&sort_by=${sortBy}&min_matches=${minMatches}`),

  getCSGOTeamDetails: (teamName: string) =>
    fetchJson<CSGOTeamDetails>(`/api/csgo/teams/${encodeURIComponent(teamName)}`),

  getCSGOH2H: (team1: string, team2: string) =>
    fetchJson<CSGOH2H>(`/api/csgo/h2h?team1=${encodeURIComponent(team1)}&team2=${encodeURIComponent(team2)}`),

  getCSGOPositions: (status?: string, limit = 50, offset = 0) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (status) params.set('status', status)
    return fetchJson<CSGOPositionsResponse>(`/api/csgo/positions?${params}`)
  },

  getCSGOActiveMarkets: (hoursAhead = 2.0, minPrice = 0.65, maxPrice = 0.80) =>
    fetchJson<CSGOActiveMarketsResponse>(`/api/csgo/markets/active?hours_ahead=${hoursAhead}&min_favorite_price=${minPrice}&max_favorite_price=${maxPrice}`),

  getCSGOMatches: () =>
    fetchJson<CSGOMatchesResponse>('/api/csgo/matches'),

  getCSGOPerformance: (days = 30) =>
    fetchJson<CSGOPerformanceResponse>(`/api/csgo/performance?days=${days}`),

  refreshCSGOData: () =>
    fetchJson<{ success: boolean; output?: string; error?: string }>('/api/csgo/refresh-data'),

  // CS:GO Pipeline endpoints
  getCSGOPipelineMatches: (params?: {
    upcoming_only?: boolean
    hours_ahead?: number
    enriched_only?: boolean
    limit?: number
    offset?: number
  }) => {
    const searchParams = new URLSearchParams()
    if (params?.upcoming_only !== undefined) searchParams.set('upcoming_only', String(params.upcoming_only))
    if (params?.hours_ahead !== undefined) searchParams.set('hours_ahead', String(params.hours_ahead))
    if (params?.enriched_only !== undefined) searchParams.set('enriched_only', String(params.enriched_only))
    if (params?.limit) searchParams.set('limit', String(params.limit))
    if (params?.offset) searchParams.set('offset', String(params.offset))
    const qs = searchParams.toString()
    return fetchJson<CSGOPipelineMatchesResponse>(`/api/csgo/pipeline/matches${qs ? `?${qs}` : ''}`)
  },

  getCSGOPipelineEvents: (params?: {
    upcoming_only?: boolean
    hours_ahead?: number
    limit?: number
  }) => {
    const searchParams = new URLSearchParams()
    if (params?.upcoming_only !== undefined) searchParams.set('upcoming_only', String(params.upcoming_only))
    if (params?.hours_ahead !== undefined) searchParams.set('hours_ahead', String(params.hours_ahead))
    if (params?.limit) searchParams.set('limit', String(params.limit))
    const qs = searchParams.toString()
    return fetchJson<CSGOPipelineEventsResponse>(`/api/csgo/pipeline/events${qs ? `?${qs}` : ''}`)
  },

  getCSGOPipelineMatch: (matchId: number) =>
    fetchJson<CSGOPipelineMatch & { market_question: string | null; gamma_data: Record<string, unknown> | null }>(
      `/api/csgo/pipeline/matches/${matchId}`
    ),

  updateCSGOPipelineMatch: async (matchId: number, gameStartTime: string) => {
    const response = await fetch(
      `${API_BASE}/api/csgo/pipeline/matches/${matchId}?game_start_time=${encodeURIComponent(gameStartTime)}`,
      { method: 'PATCH' }
    )
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json()
  },

  getCSGOPipelineSignals: (count = 50, conditionId?: string) => {
    const params = new URLSearchParams({ count: String(count) })
    if (conditionId) params.set('condition_id', conditionId)
    return fetchJson<CSGOPipelineSignalsResponse>(`/api/csgo/pipeline/signals?${params}`)
  },

  syncCSGOPipeline: async () => {
    const response = await fetch(`${API_BASE}/api/csgo/pipeline/sync`, { method: 'POST' })
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
    return response.json() as Promise<CSGOPipelineSyncResponse>
  },

  // CS:GO Price Chart endpoints
  getCSGOPositionList: () =>
    fetchJson<CSGOPositionListResponse>('/api/csgo/positions/list'),

  getCSGOPriceHistory: (marketId: number) =>
    fetchJson<CSGOPriceHistoryResponse>(`/api/csgo/price-history/${marketId}`),

  // CSGO Engine (new real-time trading system) endpoints
  getCSGOEngineHealth: () =>
    fetchJson<CSGOEngineHealth>('/api/csgo-engine/health'),

  getCSGOEngineStats: () =>
    fetchJson<CSGOEngineStats>('/api/csgo-engine/stats'),

  getCSGOEngineStrategies: () =>
    fetchJson<CSGOEngineStrategyState[]>('/api/csgo-engine/strategies'),

  getCSGOEnginePositions: (status?: string, strategy?: string, limit = 100) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (status) params.set('status', status)
    if (strategy) params.set('strategy', strategy)
    return fetchJson<CSGOEnginePosition[]>(`/api/csgo-engine/positions?${params}`)
  },

  getCSGOEngineSpreads: (status?: string, strategy?: string, limit = 100) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (status) params.set('status', status)
    if (strategy) params.set('strategy', strategy)
    return fetchJson<CSGOEngineSpread[]>(`/api/csgo-engine/spreads?${params}`)
  },

  getCSGOEngineTrades: (positionId?: number, limit = 100) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (positionId) params.set('position_id', String(positionId))
    return fetchJson<CSGOEngineTrade[]>(`/api/csgo-engine/trades?${params}`)
  },

  getCSGOEngineStream: () =>
    fetchJson<{ length: number; first_entry?: string; last_entry?: string; error?: string }>('/api/csgo-engine/stream'),

  // CS:GO Analytics endpoints
  getCSGOStrategyAnalytics: (strategyName: string) =>
    fetchJson<CSGOStrategyAnalytics>(`/api/csgo/engine/strategy/${encodeURIComponent(strategyName)}/analytics`),

  getCSGOPerformanceByMarket: (limit = 50) =>
    fetchJson<CSGOPerformanceByMarket>(`/api/csgo/engine/performance-by-market?limit=${limit}`),

  getCSGOExitQuality: (strategy?: string, limit = 50) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (strategy) params.set('strategy', strategy)
    return fetchJson<CSGOExitQuality>(`/api/csgo/engine/exit-quality?${params}`)
  },

  // GRID Integration endpoints
  getGRIDStats: () => fetchJson<GRIDStats>('/api/grid/stats'),

  getGRIDEvents: (params?: { limit?: number; offset?: number; event_type?: string }) => {
    const searchParams = new URLSearchParams()
    if (params?.limit) searchParams.set('limit', String(params.limit))
    if (params?.offset) searchParams.set('offset', String(params.offset))
    if (params?.event_type) searchParams.set('event_type', params.event_type)
    const qs = searchParams.toString()
    return fetchJson<GRIDEventsResponse>(`/api/grid/events${qs ? `?${qs}` : ''}`)
  },

  getGRIDMatches: (includeClosed = false) =>
    fetchJson<GRIDMatchesResponse>(`/api/grid/matches?include_closed=${includeClosed}`),

  getGRIDPollerState: () => fetchJson<GRIDPollerStateResponse>('/api/grid/poller-state'),
}

// Analytics types
export interface CapitalAnalytics {
  timestamp: string
  totals: {
    allocated: number
    cash: number
    deployed: number
    position_value: number
    utilization_pct: number
  }
  strategies: {
    strategy_name: string
    allocated_usd: number
    cash_usd: number
    deployed_usd: number
    position_value_usd: number
    position_count: number
    utilization_pct: number
    available_usd: number
    is_blocked: boolean
  }[]
}

export interface PositionAnalytics {
  timestamp: string
  total_positions: number
  age_buckets: Record<string, number>
  pending_resolution_count: number
  by_strategy: Record<string, {
    count: number
    avg_age: number
    overdue: number
    pending_resolution: number
  }>
  pending_resolution: {
    strategy_name: string
    market_id: number
    market_question: string
    end_date: string
    entry_time: string
    cost_basis: number
    current_value: number
    unrealized_pnl: number
    age_hours: number
    expected_hold_hours: number
    is_overdue: boolean
    market_status: string
  }[]
  oldest_positions: {
    strategy_name: string
    market_id: number
    market_question: string
    age_hours: number
    is_overdue: boolean
    market_status: string
  }[]
}

export interface SignalAnalytics {
  timestamp: string
  period_hours: number
  strategies: {
    strategy_name: string
    total_signals: number
    executed: number
    rejected: number
    execution_rate_pct: number
    unique_markets: number
    last_signal: string
  }[]
  rejection_breakdown: { reason: string; count: number }[]
  missed_opportunities: { strategy_name: string; missed_markets: number }[]
  hourly_trend: { hour: string; signals: number; executed: number }[]
}

export interface MarketPipeline {
  timestamp: string
  summary: {
    total_in_window: number
    new_opportunities_now: number
    new_opportunities_approaching: number
    status: 'opportunities' | 'pipeline' | 'saturated'
  }
  by_window: Record<string, {
    window: string
    in_window: number
    in_window_new: number
    in_window_holding: number
    approaching: number
    approaching_new: number
    approaching_time: string
    markets_in_window: {
      id: number
      question: string
      price: number
      hours_to_close: number
      has_position: boolean
    }[]
    markets_approaching: {
      id: number
      question: string
      price: number
      hours_to_close: number
      has_position: boolean
    }[]
  }>
}

// Portfolio summary for dashboard header
export interface PortfolioSummary {
  timestamp: string
  cash: number
  position_value: number
  portfolio_value: number
  unrealized_pnl: number
  realized_pnl: number
  total_pnl: number
  total_return_pct: number
  open_positions: number
  strategies_count: number
  total_allocated: number
  high_water_mark: number
  current_drawdown_pct: number
}

// Live trading summary
export interface LiveTradingStrategy {
  name: string
  open_positions: number
  closed_positions: number
  realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
  wins: number
  losses: number
  win_rate: number
}

export interface LiveTradingPosition {
  id: number
  strategy_name: string
  market_title: string | null
  token_side: string
  entry_price: number | null
  current_price: number | null
  size_shares: number | null
  cost_basis: number | null
  current_value: number | null
  unrealized_pnl: number
  unrealized_pnl_pct: number
  entry_time: string | null
}

export interface LiveTradingSummary {
  success: boolean
  timestamp: string
  wallet: {
    address: string
    balance: number
    position_value: number
    total_value: number
  }
  pnl: {
    unrealized: number
    realized: number
    total: number
  }
  positions: {
    open: number
    closed: number
    cost_basis: number
  }
  trades_count: number
  strategies: LiveTradingStrategy[]
  open_positions: LiveTradingPosition[]
  error?: string
}

// CSGO Engine types (new real-time trading system)
export interface CSGOEngineHealth {
  status: 'healthy' | 'degraded' | 'unhealthy'
  message: string
}

export interface CSGOEngineStats {
  positions_open: number
  positions_closed: number
  spreads_open: number
  spreads_closed: number
  total_trades: number
  strategies_active: number
  total_realized_pnl: number
  total_unrealized_pnl: number
  stream_length: number
}

export interface CSGOEngineStrategyState {
  strategy_name: string
  allocated_usd: number
  available_usd: number
  total_realized_pnl: number
  total_unrealized_pnl: number
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number | null
  is_active: boolean
}

export interface CSGOEnginePosition {
  id: number
  strategy_name: string
  market_id: number
  token_type: string
  remaining_shares: number
  avg_entry_price: number
  current_price: number | null
  unrealized_pnl: number | null
  realized_pnl: number
  team_yes: string | null
  team_no: string | null
  format: string | null
  cost_basis: number
  entry_spread: number | null
  opened_at: string | null
  status: string
}

export interface CSGOEngineSpread {
  id: number
  strategy_name: string
  market_id: number
  spread_type: string
  total_cost_basis: number
  total_realized_pnl: number
  total_unrealized_pnl: number
  team_yes: string | null
  team_no: string | null
  status: string
}

export interface CSGOEngineTrade {
  id: number
  position_id: number
  side: string
  shares: number
  price: number
  cost_usd: number
  slippage: number | null
  // Orderbook state
  best_bid: number | null
  best_ask: number | null
  spread: number | null
  // Match context
  team_yes: string | null
  team_no: string | null
  format: string | null
  map_number: number | null
  game_start_time: string | null
  created_at: string
}

// CS:GO Analytics Types
export interface CSGOStrategyAnalytics {
  strategy_name: string
  summary: {
    allocated_usd: number
    available_usd: number
    total_realized_pnl: number
    total_unrealized_pnl: number
    trade_count: number
    win_count: number
    loss_count: number
    max_drawdown: number
  }
  execution_quality: {
    avg_slippage_pct: number
    avg_spread_pct: number
    avg_entry_exit_spread_pct: number
  }
  pnl_by_market_type: Record<string, { pnl: number; count: number }>
  hold_time_distribution: Record<string, number>
  capital_timeline: { timestamp: string | null; available_capital: number }[]
  position_count: number
  closed_positions: number
}

export interface CSGOPerformanceByMarket {
  summary: {
    total_markets: number
    profitable: number
    losing: number
    total_pnl: number
  }
  markets: {
    market_id: number
    match: string
    format: string | null
    market_type: string | null
    group_item_title: string | null
    game_start_time: string | null
    total_pnl: number
    position_count: number
    turnover: number
    strategies: { name: string; pnl: number; positions: number }[]
    best_strategy: string | null
    worst_strategy: string | null
  }[]
}

export interface CSGOExitQualityPosition {
  position_id: number
  strategy: string
  match: string
  token_type: string
  entry_price_pct: number
  exit_price_pct: number
  best_price_during_hold_pct: number | null
  resolution_price_pct: number | null
  left_on_table_vs_best: number | null
  left_on_table_vs_resolution: number | null
  exit_quality_score: number | null
  winner: boolean | null
  realized_pnl: number
  hold_time_mins: number | null
  exit_reason: string | null
}

export interface CSGOExitQuality {
  summary: {
    total_positions: number
    avg_exit_quality: number | null
    total_left_on_table_vs_best: number
    total_left_on_table_vs_resolution: number
    winners: number
    losers: number
  }
  strategy_breakdown: {
    strategy: string
    avg_exit_quality: number | null
    total_left_on_table: number
    position_count: number
  }[]
  positions: CSGOExitQualityPosition[]
}

// GRID Integration types
export interface GRIDStats {
  events_24h: number
  series_polling: number
  last_poll_at: string | null
  matches_linked: number
  markets_linked: number
}

export interface GRIDEvent {
  id: number
  market_id: number
  grid_series_id: string
  event_type: 'round' | 'map' | 'series'
  winner: 'YES' | 'NO'
  detected_at: string
  map_name: string | null
  map_number: number | null
  format: string | null
  is_overtime: boolean
  rounds_in_event: number | null
  // Scores
  prev_round_yes: number | null
  prev_round_no: number | null
  new_round_yes: number | null
  new_round_no: number | null
  prev_map_yes: number | null
  prev_map_no: number | null
  new_map_yes: number | null
  new_map_no: number | null
  // Prices
  price_at_detection: number | null
  spread_at_detection: number | null
  price_after_30sec: number | null
  price_after_1min: number | null
  price_after_5min: number | null
  price_move_30sec: number | null
  price_move_1min: number | null
  price_move_5min: number | null
  move_direction_correct: boolean | null
  // Team info
  team_yes: string | null
  team_no: string | null
}

export interface GRIDEventsResponse {
  total: number
  offset: number
  limit: number
  items: GRIDEvent[]
}

export interface GRIDMatch {
  id: number
  market_id: number
  team_yes: string | null
  team_no: string | null
  grid_series_id: string
  grid_yes_team_id: string | null
  grid_match_confidence: number | null
  game_start_time: string | null
  format: string | null
  market_type: string | null
  closed: boolean
  resolved: boolean
}

export interface GRIDMatchesResponse {
  total: number
  items: GRIDMatch[]
}

export interface GRIDPollerStateGame {
  map_name: string | null
  yes_rounds: number
  no_rounds: number
  winner: string | null
  finished: boolean
}

export interface GRIDPollerState {
  series_id: string
  market_id: number
  team_yes: string | null
  team_no: string | null
  last_poll_at: string | null
  polls_count: number
  format: string | null
  finished: boolean | null
  yes_maps: number | null
  no_maps: number | null
  games: GRIDPollerStateGame[]
}

export interface GRIDPollerStateResponse {
  total: number
  items: GRIDPollerState[]
}
