import { readChatEvents, type ChatEvent } from "./chatStream";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
let sessionVersion = 0;
const requests = new Set<AbortController>();

export interface User {
  id: number;
  email: string;
  name: string;
}

export interface Account {
  id: number;
  provider: string;
  email_address: string;
}

export interface Email {
  id: number;
  sender: string;
  subject: string;
  snippet: string;
  body_text: string;
  received_at: string;
  category: string;
  needs_attention: boolean;
  classification_reason: string;
  summary: string;
  thread_id: string;
  is_read: boolean;
  is_archived: boolean;
  is_bulk: boolean;
  needs_reply: boolean;
  needs_reply_reason: string;
}

export interface Draft {
  id: number;
  email_id: number;
  content: string;
  original_content: string;
  preferences_used: { kind: string; title: string; content: string }[] | null;
  tone: string;
  status: string;
  auto_sent: boolean;
  auto_send_eligible: boolean;
  created_at: string;
  sent_at: string | null;
}

export interface DraftEdit {
  id: number;
  draft_id: number;
  subject: string;
  original: string;
  edited: string;
  applied: boolean;
  created_at: string;
}

export interface SyncJob {
  id: number;
  status: "running" | "done" | "error";
  stage: string;
  total: number;
  processed: number;
  new_count: number;
  drafted_count: number;
  auto_sent_count: number;
  error: string;
  finished_at: string | null;
}

export interface KnowledgeItem {
  id: number;
  kind: string;
  title: string;
  content: string;
}

export interface AppSettings {
  user_name: string;
  signature: string;
  default_tone: string;
  style_profile: string;
  auto_draft_enabled: boolean;
  auto_draft_limit: number;
  auto_send_enabled: boolean;
  auto_send_categories: string;
  auto_send_daily_limit: number;
}

export interface UsageRow {
  unpriced_calls: number;
  operation: string;
  model: string;
  calls: number;
  failures: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  avg_latency_ms: number;
  p95_latency_ms: number;
}

export interface UsageSummary {
  unpriced_calls: number;
  rows: UsageRow[];
  total_calls: number;
  total_cost_usd: number | null;
  total_input_tokens: number;
  total_output_tokens: number;
}

export interface EmailQuery {
  category?: string;
  needs_attention?: boolean;
  needs_reply_only?: boolean;
  unread?: boolean;
  archived?: boolean;
  q?: string;
}

const TOKEN_KEY = "mailassist_token";

export const auth = {
  token: (): string | null => localStorage.getItem(TOKEN_KEY),
  setToken: (t: string) => { sessionVersion++; for (const controller of requests) controller.abort(); localStorage.setItem(TOKEN_KEY, t); },
  clear: () => { sessionVersion++; for (const controller of requests) controller.abort(); localStorage.removeItem(TOKEN_KEY); },
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = auth.token();
  if (token) headers.Authorization = `Bearer ${token}`;

  const version = sessionVersion;
  const controller = new AbortController();
  requests.add(controller);
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, { headers, ...init,
      signal: init?.signal ? AbortSignal.any([controller.signal, init.signal]) : controller.signal });
  } finally { requests.delete(controller); }
  if (version !== sessionVersion) throw new Error("Session changed");
  if (res.status === 401 && token) {
    auth.clear();
    window.location.reload();
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${res.status})`);
  }
  const body = await res.json();
  if (version !== sessionVersion) throw new Error("Session changed");
  return body;
}

function queryString(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function allPages<T>(path: string, params: Record<string, unknown> = {}): Promise<T[]> {
  const version = sessionVersion;
  const rows: T[] = [];
  for (let offset = 0; ; offset += 200) {
    const page = await request<T[]>(`${path}${queryString({ ...params, limit: 200, offset })}`);
    if (version !== sessionVersion) throw new Error("Session changed");
    rows.push(...page);
    if (page.length < 200) return rows;
  }
}

export const api = {
  chatStream: async (body: { email_id: number | null; message: string; history: { role: "user" | "assistant"; content: string }[] }, onEvent: (event: ChatEvent) => void, signal: AbortSignal) => {
    const version = sessionVersion;
    const controller = new AbortController();
    requests.add(controller);
    try {
      const response = await fetch(`${API}/assistant/chat/stream`, { method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${auth.token()}` },
        body: JSON.stringify(body), signal: AbortSignal.any([signal, controller.signal, AbortSignal.timeout(70000)]) });
      if (version !== sessionVersion) throw new Error("Session changed");
      if (response.status === 401) { auth.clear(); window.location.reload(); }
      if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.detail || "Could not connect to MailAssist."); }
      if (!response.body) throw new Error("Streaming is unavailable in this browser.");
      await readChatEvents(response.body, event => { if (version === sessionVersion) onEvent(event); });
    } finally { requests.delete(controller); }
  },
  chat: (body: { email_id: number | null; message: string; history: { role: "user" | "assistant"; content: string }[] }) =>
    request<{ answer: string; suggested_reply: string | null; draft_id: number | null }>("/assistant/chat", { method: "POST", body: JSON.stringify(body) }),
  calendarLoginUrl: () => `${API}/auth/google/login?token=${auth.token()}&calendar=true`,
  calendarStatus: () => request<{ connected: boolean; email: string | null; preferences?: { days: number; timezone: string; work_start: string; work_end: string; duration_minutes: number; weekdays: number[] } }>("/calendar/status"),
  availability: (body: { start_date: string; days: number; timezone: string; work_start: string; work_end: string; duration_minutes: number; weekdays: number[] }) =>
    request<{ slots: { start: string; end: string }[]; timezone: string; email: string; checked_at: string }>("/calendar/availability", { method: "POST", body: JSON.stringify(body) }),
  googleLoginUrl: () => `${API}/auth/google/login?token=${auth.token()}`,

  register: (email: string, password: string, name: string) =>
    request<{ token: string; user: User }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    }),
  login: (email: string, password: string) =>
    request<{ token: string; user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<User>("/auth/me"),

  accounts: () => request<Account[]>("/auth/accounts"),

  // sync runs in the background — start it, then poll the job
  startSync: () => request<SyncJob>("/emails/sync", { method: "POST", signal: AbortSignal.timeout(20_000) }),
  syncStatus: (id: number) => request<SyncJob>(`/emails/sync/${id}`, { signal: AbortSignal.timeout(20_000) }),

  emails: (params: EmailQuery = {}) =>
    allPages<Email>("/emails", { ...params }),
  email: (id: number) => request<Email>(`/emails/${id}`),
  patchEmail: (id: number, patch: { is_read?: boolean; is_archived?: boolean }) =>
    request<Email>(`/emails/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  summarize: (id: number) => request<Email>(`/emails/${id}/summarize`, { method: "POST" }),

  drafts: (params: { status?: string; email_id?: number } = {}) =>
    allPages<Draft>("/drafts", { ...params }),
  draftsFor: (emailId: number) => allPages<Draft>("/drafts", { email_id: emailId }),
  generateDraft: (emailId: number, tone: string, instructions: string) =>
    request<Draft>(`/emails/${emailId}/draft`, {
      method: "POST",
      body: JSON.stringify({ tone, instructions }),
    }),
  updateDraft: (id: number, patch: { content?: string; status?: string }) =>
    request<Draft>(`/drafts/${id}`, { method: "PUT", body: JSON.stringify(patch) }),
  resolveDelivery: (id: number, sent: boolean) => request<Draft>(`/drafts/${id}/resolve`, { method: "POST", body: JSON.stringify({ status: sent ? "sent" : "pending" }) }),
  sendDraft: (id: number) => request<Draft>(`/drafts/${id}/send`, { method: "POST" }),
  sendApproved: () =>
    request<{ sent: number; failed: number; errors: string[] }>("/drafts/send-approved", {
      method: "POST",
    }),

  draftEdits: (applied?: boolean) =>
    allPages<DraftEdit>("/draft-edits", { applied }),
  applyEdits: () =>
    request<{ applied: number; style_profile: string }>("/draft-edits/apply", {
      method: "POST",
    }),
  deleteEdit: (id: number) =>
    request<{ deleted: number }>(`/draft-edits/${id}`, { method: "DELETE" }),

  usage: (days?: number) => request<UsageSummary>(`/usage/summary${queryString({ days })}`),

  seedDemo: () => request<{ created: number }>("/dev/seed", { method: "POST" }),
  clearDemo: () => request<{ deleted: number }>("/dev/clear", { method: "POST" }),

  settings: () => request<AppSettings>("/settings"),
  learnStyle: () => request<AppSettings>("/settings/style/learn", { method: "POST" }),
  saveSettings: (s: AppSettings) =>
    request<AppSettings>("/settings", { method: "PUT", body: JSON.stringify(s) }),
  knowledge: () => request<KnowledgeItem[]>("/settings/knowledge"),
  addKnowledge: (item: Omit<KnowledgeItem, "id">) =>
    request<KnowledgeItem>("/settings/knowledge", {
      method: "POST",
      body: JSON.stringify(item),
    }),
  deleteKnowledge: (id: number) =>
    request<{ deleted: number }>(`/settings/knowledge/${id}`, { method: "DELETE" }),
};
