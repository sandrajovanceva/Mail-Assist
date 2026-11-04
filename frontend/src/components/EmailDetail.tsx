import { useEffect, useRef, useState } from "react";
import { api, type Draft, type Email } from "../api";
import { ArchiveIcon } from "./Icons";
import { DraftPreferences } from "./DraftPreferences";

const TONES = ["professional", "friendly", "formal", "casual"];

export function EmailDetail({
  email,
  defaultTone,
  onError,
  onArchive,
  assistantRevision,
}: {
  email: Email;
  defaultTone: string;
  onError: (msg: string) => void;
  onArchive: () => void;
  assistantRevision?: { draftId: number; content: string } | null;
}) {
  const [current, setCurrent] = useState(email);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftText, setDraftText] = useState("");
  const [tone, setTone] = useState(defaultTone);
  const [toneTouched, setToneTouched] = useState(false);
  useEffect(() => { if (!toneTouched) setTone(defaultTone); }, [defaultTone, toneTouched]);
  const [instructions, setInstructions] = useState("");
  const [showOriginal, setShowOriginal] = useState(false);
  const [showRefine, setShowRefine] = useState(false);
  const [busy, setBusy] = useState<"" | "load" | "summarize" | "generate" | "send" | "save" | "status">("load");
  const mounted = useRef(true);
  const appliedRevision = useRef<typeof assistantRevision>(null);
  useEffect(() => {
    if (!assistantRevision || assistantRevision === appliedRevision.current || !draft || busy) return;
    appliedRevision.current = assistantRevision;
    if (draft.id !== assistantRevision.draftId) return;
    if (!["pending", "approved"].includes(draft.status)) {
      onError("This draft cannot be edited. Generate a new reply first.");
      return;
    }
    setDraftText(assistantRevision.content);
  }, [assistantRevision, draft, busy, onError]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // Refresh message metadata without resetting the draft being edited.
  useEffect(() => {
    setCurrent((previous) => ({ ...email, summary: email.summary || previous.summary }));
  }, [email]);

  useEffect(() => {
    let cancelled = false;
    setBusy("load");
    setDraft(null);
    setDraftText("");
    setInstructions("");
    setShowOriginal(false);
    setShowRefine(false);
    api
      .draftsFor(email.id)
      .then((drafts) => {
        if (cancelled) return;
        if (drafts.length > 0) {
          setDraft(drafts[0]);
          setDraftText(drafts[0].content);
        }
      })
      .catch((error) => { if (!cancelled) onError(error.message); })
      .finally(() => { if (!cancelled) setBusy(""); });
    return () => { cancelled = true; };
  }, [email.id, onError]);

  const run = async (label: typeof busy, fn: () => Promise<void>) => {
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      if (mounted.current) onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  };

  const summarize = () =>
    run("summarize", async () => {
      setCurrent(await api.summarize(current.id));
    });

  const generate = () =>
    run("generate", async () => {
      const d = await api.generateDraft(current.id, tone, instructions);
      setDraft(d);
      setDraftText(d.content);
      setShowOriginal(false);
      setShowRefine(false);
    });

  const save = () =>
    run("save", async () => {
      if (!draft) return;
      setDraft(await api.updateDraft(draft.id, { content: draftText }));
    });

  const setStatus = (status: string) =>
    run("status", async () => {
      if (!draft) return;
      // save wording first so approving never discards an in-progress edit
      if (draftText !== draft.content) {
        await api.updateDraft(draft.id, { content: draftText });
      }
      setDraft(await api.updateDraft(draft.id, { status }));
    });

  const send = () =>
    run("send", async () => {
      if (!draft) return;
      if (draftText !== draft.content) {
        await api.updateDraft(draft.id, { content: draftText });
      }
      setDraft(await api.sendDraft(draft.id));
    });

  const sent = draft?.status === "sent";
  const superseded = draft?.status === "superseded";
  const uncertain = draft?.status === "sending" || draft?.status === "send_unknown";
  const edited = draft ? draftText.trim() !== draft.original_content.trim() : false;
  /** unsaved changes, distinct from `edited`, which compares against the AI's version */
  const dirty = draft ? draftText !== draft.content : false;

  return (
    <div>
      <div className="detail-head">
        <h2>{current.subject || "(no subject)"}</h2>
        {/* status sits beside the title rather than stacked under it, it
            labels the email, so it belongs on the same line */}
        <div className="detail-tags">
          {current.category && (
            <span className={`chip mono ${current.category}`}>{current.category}</span>
          )}
          {current.needs_attention && <span className="chip mono attention">needs you</span>}
          {current.needs_reply_reason && !current.needs_reply && (
            <span className="chip mono skipped">no reply needed</span>
          )}
        </div>
        <button className="icon-btn" onClick={onArchive} title="Archive">
          <ArchiveIcon />
        </button>
      </div>
      <div className="meta-line mono">
        {current.sender} · {new Date(current.received_at).toLocaleString()}
      </div>

      {/* The triage and skip reasons explain a decision. They are footnotes to
          the email, not content. A tinted box each made the header shout over
          the message it was describing. */}
      {(current.classification_reason || (!current.needs_reply && current.needs_reply_reason)) && (
        <p className="why">
          {current.classification_reason}
          {!current.needs_reply && current.needs_reply_reason && (
            <span className="why-sep">· {current.needs_reply_reason}</span>
          )}
        </p>
      )}

      {/* A summary is real content the user reads, so it keeps its panel. */}
      {current.summary ? (
        <div className="callout">
          <span className="mono">Thread summary</span>
          <div>{current.summary}</div>
        </div>
      ) : (
        <button className="subtle-action" onClick={summarize} disabled={busy !== ""}>
          {busy === "summarize" ? "Summarizing…" : "Summarize thread"}
        </button>
      )}

      <div className="email-body">{current.body_text || current.snippet}</div>

      <div className="draft-box">
        <div className="draft-head">
          <h3>Reply draft</h3>
          {draft && (
            <span className={`chip mono status-${draft.status}`}>
              {draft.auto_sent ? "auto-sent" : draft.status}
            </span>
          )}
        </div>

        {/* Before a draft exists, generating is the only thing to do, so the
            controls lead. Once there is one, reading and sending it matter
            more, so the regenerate controls fold away until asked for. */}
        {!sent && (!draft || showRefine || superseded) && (
          <div className="draft-controls">
            <select value={tone} onChange={(e) => { setToneTouched(true); setTone(e.target.value); }}>
              {TONES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input
              placeholder="Optional instructions, e.g. 'decline politely, suggest next week'"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
            />
            <button className="primary" onClick={generate} disabled={busy !== ""}>
              {busy === "generate" ? "Drafting…" : draft ? "Regenerate" : "Generate draft"}
            </button>
          </div>
        )}

        {draft && (
          <>
            <DraftPreferences draft={draft} />
            <textarea
              value={draftText}
              onFocus={() => {
                // Taking an automatic draft into the editor reserves it for
                // manual review, even before the first wording change is saved.
                if (draft.auto_send_eligible && !sent && !busy) {
                  run("save", async () => {
                    setDraft(await api.updateDraft(draft.id, { status: draft.status }));
                  });
                }
              }}
              onChange={(e) => setDraftText(e.target.value)}
              readOnly={sent || superseded || uncertain}
            />

            {edited && (
              <div className="edit-note">
                <span>You rewrote this draft. MailAssist can learn from that.</span>
                <button className="link" onClick={() => setShowOriginal((v) => !v)}>
                  {showOriginal ? "Hide" : "Show"} what the AI wrote
                </button>
              </div>
            )}
            {showOriginal && <pre className="original-draft">{draft.original_content}</pre>}

            {uncertain && <p className="voice-hint">Delivery is not confirmed. Check Sent mail and resolve this draft in the Draft queue.</p>}
            {superseded && <p className="voice-hint">This version was replaced and cannot be sent. Reopen the email to load the latest draft.</p>}
            {!sent && !superseded && !uncertain && (
              <div className="draft-actions">
                {/* Left: ways to change the draft. Right: ways to finish with
                    it. Send is the only filled button so the eye lands on it. */}
                <button
                  className={`subtle-action ${showRefine ? "on" : ""}`}
                  onClick={() => setShowRefine((v) => !v)}
                  disabled={busy !== ""}
                >
                  Regenerate…
                </button>
                {/* only offered when there is actually something unsaved,
                    sending and approving save the text anyway */}
                {dirty && (
                  <button className="subtle-action" onClick={save} disabled={busy !== ""}>
                    {busy === "save" ? "Saving…" : "Save"}
                  </button>
                )}

                <div className="grow" />

                <button
                  className="subtle-action danger"
                  onClick={() => setStatus("rejected")}
                  disabled={busy !== "" || draft.status === "rejected"}
                >
                  Reject
                </button>
                <button
                  onClick={() => setStatus(draft.status === "approved" ? "pending" : "approved")}
                  disabled={busy !== ""}
                >
                  {draft.status === "approved" ? "Un-approve" : "Approve"}
                </button>
                <button
                  className="primary"
                  onClick={send}
                  disabled={busy !== "" || draft.status === "rejected"}
                >
                  {busy === "send" ? "Sending…" : "Send"}
                </button>
              </div>
            )}
            {sent && (
              <div className="draft-status mono">
                sent {draft.sent_at ? new Date(draft.sent_at).toLocaleString() : ""}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
