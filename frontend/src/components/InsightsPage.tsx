import { useEffect, useState } from "react";
import { api, type Email, type UsageSummary } from "../api";

const CATEGORY_ORDER = ["important", "routine", "newsletter", "spam"] as const;

function formatCost(usd: number | null): string {
  if (usd === null) return "Unknown";
  if (usd === 0) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(5)}` : `$${usd.toFixed(3)}`;
}

/**
 * The measurement view: what the assistant did, what it cost, how fast it was.
 * These are the same numbers `eval/cost_report.py` prints for the thesis.
 */
export function InsightsPage({ onError }: { onError: (msg: string) => void }) {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [emails, setEmails] = useState<Email[]>([]);

  useEffect(() => {
    api.usage().then(setUsage).catch((e) => onError(e.message));
    api.emails({}).then(setEmails).catch(() => {});
  }, [onError]);

  const counts = CATEGORY_ORDER.map((key) => ({
    key,
    count: emails.filter((e) => e.category === key).length,
  }));
  const maxCount = Math.max(1, ...counts.map((c) => c.count));
  const attention = emails.filter((e) => e.needs_attention).length;
  const classified = emails.filter((e) => e.category).length;

  return (
    <section className="glass settings-panel insights">
      <h2>Insights</h2>
      <p className="panel-sub">
        What the assistant has done for this account, measured rather than estimated. Every
        model call is logged with its tokens, latency and list-price cost.
      </p>

      <div className="stat-row">
        <div className="stat">
          <span className="stat-value">{emails.length}</span>
          <span className="stat-label">emails</span>
        </div>
        <div className="stat">
          <span className="stat-value">{classified}</span>
          <span className="stat-label">classified</span>
        </div>
        <div className="stat">
          <span className="stat-value">{attention}</span>
          <span className="stat-label">need you</span>
        </div>
        <div className="stat">
          <span className="stat-value">{usage?.total_calls ?? 0}</span>
          <span className="stat-label">API attempts</span>
        </div>
        <div className="stat">
          <span className="stat-value">{usage?.unpriced_calls ? "Unknown" : formatCost(usage?.total_cost_usd ?? 0)}</span>
          <span className="stat-label">at list price</span>
        </div>
      </div>

      <h3 className="section-head">Inbox composition</h3>
      <div className="bars">
        {counts.map(({ key, count }) => (
          <div className="bar-row" key={key}>
            <span className="bar-label">{key}</span>
            <div className="bar-track">
              <div className={`bar-fill ${key}`} style={{ width: `${(count / maxCount) * 100}%` }} />
            </div>
            <span className="bar-value mono">{count}</span>
          </div>
        ))}
      </div>

      <h3 className="section-head">Model usage</h3>
      {usage && usage.rows.length > 0 ? (
        <div className="table-wrap">
          <table className="usage-table">
            <thead>
              <tr>
                <th>Operation</th>
                <th>Model</th>
                <th className="num">Attempts</th>
                <th className="num">Failed</th>
                <th className="num">In</th>
                <th className="num">Out</th>
                <th className="num">Avg ms</th>
                <th className="num">p95 ms</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {usage.rows.map((row) => (
                <tr key={`${row.operation}-${row.model}`}>
                  <td>{row.operation}</td>
                  <td className="mono model">{row.model}</td>
                  <td className="num">{row.calls}</td>
                  <td className="num">{row.failures || "0"}</td>
                  <td className="num">{row.input_tokens.toLocaleString()}</td>
                  <td className="num">{row.output_tokens.toLocaleString()}</td>
                  <td className="num">{row.avg_latency_ms}</td>
                  <td className="num">{row.p95_latency_ms}</td>
                  <td className="num">{row.unpriced_calls ? "Unknown" : formatCost(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="kb-empty">
          No model calls recorded yet. Sync your inbox or generate a draft.
        </p>
      )}

      <p className="voice-hint">
        Cost is Google's published paid-tier price for the tokens actually used. On a free-tier
        key you are charged nothing; the figure answers what this would cost at scale.
      </p>
    </section>
  );
}
