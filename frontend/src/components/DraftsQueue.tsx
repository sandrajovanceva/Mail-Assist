import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Draft, type Email } from "../api";
import { DraftPreferences } from "./DraftPreferences";

const TABS = [
  { key: "pending", label: "Pending" },
  { key: "approved", label: "Approved" },
  { key: "sent", label: "Sent" },
  { key: "delivery", label: "Needs checking" },
] as const;

/** Every draft in one place, so nothing sits forgotten inside an email. */
export function DraftsQueue({
  onError,
  onOpenEmail,
}: {
  onError: (msg: string) => void;
  onOpenEmail: (emailId: number) => void;
}) {
  const [status, setStatus] = useState<string>("pending");
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [emails, setEmails] = useState<Record<number, Email>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [sendingAll, setSendingAll] = useState(false);
  const requestVersion = useRef(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    const version = ++requestVersion.current;
    setLoading(true);
    const rows = status === "delivery"
      ? Promise.all([api.drafts({ status: "send_unknown" }), api.drafts({ status: "sending" })])
          .then(groups => groups.flat().sort((a, b) => b.created_at.localeCompare(a.created_at)))
      : api.drafts({ status });
    Promise.all([rows, api.emails({})])
      .then(async ([rows, allEmails]) => {
        const missing = [...new Set(rows.map(d => d.email_id))].filter(id => !allEmails.some(e => e.id === id));
        allEmails.push(...await Promise.all(missing.map(id => api.email(id))));
        if (version !== requestVersion.current) return;
        setDrafts(rows);
        setEmails(Object.fromEntries(allEmails.map((e) => [e.id, e])));
      })
      .catch((e) => { if (version === requestVersion.current) onError(e.message); })
      .finally(() => { if (version === requestVersion.current) setLoading(false); });
  }, [status, onError]);

  const cancelLoad = useCallback(() => { requestVersion.current++; }, []);
  useEffect(() => {
    load();
    return cancelLoad;
  }, [load, cancelLoad]);

  const act = async (draft: Draft, action: "approve" | "reject" | "send") => {
    setBusyId(draft.id);
    try {
      if (action === "send") await api.sendDraft(draft.id);
      else await api.updateDraft(draft.id, { status: action === "approve" ? "approved" : "rejected" });
      load();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  /** What approving is for: stage a batch, then send it in one go. */
  const sendAllApproved = async () => {
    setSendingAll(true);
    try {
      const result = await api.sendApproved();
      if (result.failed) {
        onError(`Sent ${result.sent}, ${result.failed} failed: ${result.errors[0] ?? ""}`);
      }
      load();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSendingAll(false);
    }
  };

  return (
    <section className="glass queue-panel">
      <h2>Draft queue</h2>
      <p className="panel-sub">
        Replies MailAssist has written, waiting on your call. Anything sent on auto-pilot is
        marked so you can see exactly what went out on your behalf.
      </p>

      <nav className="queue-filters" aria-label="Draft status">
      <div className="segmented queue-tabs">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            className={status === tab.key ? "active" : ""}
            aria-pressed={status === tab.key}
            onClick={() => setStatus(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <details className="queue-more"
        onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false; }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.currentTarget.open = false;
            event.currentTarget.querySelector("summary")?.focus();
          }
        }}>
        <summary className={status === "rejected" || status === "superseded" ? "selected" : ""}>
          {status === "rejected" ? "Rejected" : status === "superseded" ? "Previous versions" : "More"}
          <span aria-hidden="true">⌄</span>
        </summary>
        <div className="queue-more-options">
          {[{ key: "rejected", label: "Rejected" }, { key: "superseded", label: "Previous versions" }].map(item => (
            <button key={item.key} aria-pressed={status === item.key} onClick={(event) => {
              setStatus(item.key);
              const menu = event.currentTarget.closest("details");
              if (menu) { menu.open = false; menu.querySelector("summary")?.focus(); }
            }}>{item.label}</button>
          ))}
        </div>
      </details>
      </nav>

      {!loading && status === "approved" && drafts.length > 0 && (
        <div className="bulk-bar">
          <span>
            {drafts.length} draft{drafts.length > 1 ? "s" : ""} reviewed and ready.
          </span>
          <button className="primary" onClick={sendAllApproved} disabled={sendingAll}>
            {sendingAll ? "Sending…" : `Send all ${drafts.length}`}
          </button>
        </div>
      )}

      {loading && <p className="kb-empty" role="status">Loading drafts…</p>}
      {!loading && drafts.length === 0 && (
        <p className="kb-empty">
          {status === "pending" ? "No pending drafts. Generate a reply from an email to add one." : "No drafts in this view."}
        </p>
      )}

      {!loading && drafts.map((draft) => {
        const email = emails[draft.email_id];
        return (
          <article className="queue-item" key={draft.id}>
            <div className="queue-top">
              <div className="queue-subject">
                <strong>{email?.subject || `Email #${draft.email_id}`}</strong>
                <span className="mono">
                  {email ? email.sender.replace(/<.*>/, "").trim() : ""} · tone: {draft.tone}
                </span>
              </div>
              {draft.auto_sent && <span className="chip mono auto">auto-sent</span>}
            </div>

            <pre className="queue-body">{draft.content}</pre>
            <DraftPreferences draft={draft} />

            <div className="queue-actions">
              {email && (
                <button onClick={() => onOpenEmail(email.id)}>Open email</button>
              )}
              {["sending", "send_unknown"].includes(draft.status) && <div>
                <p>Check Sent mail before confirming delivery. If a send is still running, wait for it to finish.</p>
                <button onClick={async () => { try { await api.resolveDelivery(draft.id, true); load(); } catch (e) { onError(String(e)); } }}>Confirmed sent</button>
                <button onClick={async () => { try { await api.resolveDelivery(draft.id, false); load(); } catch (e) { onError(String(e)); } }}>Confirmed not sent</button>
              </div>}
              {["pending", "approved", "rejected"].includes(draft.status) && (
                <>
                  {draft.status !== "approved" && (
                    <button onClick={() => act(draft, "approve")} disabled={busyId === draft.id}>
                      Approve
                    </button>
                  )}
                  {draft.status !== "rejected" && (
                    <button
                      className="danger"
                      onClick={() => act(draft, "reject")}
                      disabled={busyId === draft.id}
                    >
                      Reject
                    </button>
                  )}
                  <button
                    className="primary"
                    onClick={() => act(draft, "send")}
                    disabled={busyId === draft.id || draft.status === "rejected"}
                  >
                    {busyId === draft.id ? "Working…" : "Send"}
                  </button>
                </>
              )}
              {draft.sent_at && (
                <span className="draft-status mono">
                  sent {new Date(draft.sent_at).toLocaleString()}
                </span>
              )}
            </div>
          </article>
        );
      })}
    </section>
  );
}
