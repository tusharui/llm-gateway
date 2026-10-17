export interface SessionUser {
  id: string;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  created_at: string | null;
  last_login_at: string | null;
}

export interface AuthResponse {
  user: SessionUser;
}

export interface AnalyticsSummary {
  total_requests: number;
  total_tokens: number;
  total_cost: number;
  cache_savings_usd: number;
  cache_hit_rate: number;
  avg_latency: number;
  successful: number;
  failed: number;
  cached: number;
}

export interface ModelUsageRow {
  model: string;
  provider: string;
  requests: number;
  tokens: number;
  cost: number;
  saved_cost: number;
  avg_latency: number;
}

export interface ModelUsageResponse {
  models: ModelUsageRow[];
}

export interface ProviderUsageRow {
  provider: string;
  requests: number;
  tokens: number;
  cost: number;
  saved_cost: number;
  avg_latency: number;
  successful: number;
}

export interface ProviderUsageResponse {
  providers: ProviderUsageRow[];
}

export interface UsageTimelineRow {
  day: string;
  requests: number;
  tokens: number;
  cost: number;
  avg_latency: number;
}

export interface UsageTimelineResponse {
  timeline: UsageTimelineRow[];
}

export interface RecentRequestRow {
  id: string;
  api_key_id: string;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  latency_ms: number;
  success: boolean;
  cached: boolean;
  timestamp: string | null;
}

export interface RecentRequestsResponse {
  requests: RecentRequestRow[];
}

export interface ChatSession {
  id: string;
  title: string;
  provider: string;
  model: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ChatSessionListResponse {
  sessions: ChatSession[];
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface ChatSessionDetail extends ChatSession {
  messages: ChatMessage[];
}
