import { useEffect, useRef, useState } from "react";
import { api, type Email } from "../api";
import { ChatIcon } from "./Icons";

type Turn = { role: "user" | "assistant"; content: string; revision?: string; draftId?: number; incomplete?: boolean; sources?: { id: number; subject: string }[] };
export function AssistantChat({ email, onCalendar, onUseRevision, onOpenEmail }: {
  email: Email | null; onCalendar: () => void;
  onUseRevision: (emailId: number, draftId: number, content: string) => void;
  onOpenEmail: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [activity, setActivity] = useState("Thinking…");
  const generation = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const composer = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    generation.current++;
    activeRequest.current?.abort();
    setMessages([]); setInput(""); setError(""); setBusy(false);
    return () => { generation.current++; activeRequest.current?.abort(); };
  }, [email?.id]);
  useEffect(() => { if (open) composer.current?.focus(); }, [open]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [messages, busy, open]);
  const ask = async (question: string) => {
    if (!question.trim() || busy) return;
    const owner = generation.current;
    const history = messages.filter(message => !message.incomplete).slice(-10).map(({ role, content }) => ({ role, content: content.slice(0, 6000) }));
    const responseIndex = messages.length + 1;
    const controller = new AbortController();
    activeRequest.current = controller;
    setMessages(old => [...old, { role: "user", content: question }, { role: "assistant", content: "", incomplete: true }]);
    setInput(""); setError(""); setBusy(true); setActivity("Thinking…");
    try {
      await api.chatStream({ email_id: email?.id ?? null, message: question, history }, event => {
        if (generation.current !== owner) return;
        if (event.type === "status") { setActivity(event.text); return; }
        setMessages(old => old.map((message, index) => {
          if (index !== responseIndex) return message;
          if (event.type === "delta") return { ...message, content: message.content + event.text };
          if (event.type === "done") return { role: "assistant", content: event.answer,
            revision: event.suggested_reply || undefined, draftId: event.draft_id || undefined, sources: event.sources };
          return message;
        }));
      }, controller.signal);
    } catch (e) { if (generation.current === owner) { setError(e instanceof Error ? e.message : String(e)); setInput(question); } }
    finally { if (generation.current === owner) setBusy(false); }
  };
  return <div className="assistant-widget">
    {open && <section className="assistant-panel" aria-label="Ask MailAssist" onKeyDown={event => { if (event.key === "Escape") setOpen(false); }}>
      <header className="assistant-header">
        <div className="assistant-brand">
          <div className="assistant-brand-icon" aria-hidden="true"><svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="5" width="18" height="14" rx="3" /><path d="m4 7 8 6 8-6" /></svg></div>
          <div className="assistant-brand-text"><strong className="assistant-wordmark">Mail<span>Assist</span></strong></div>
        </div>
        <button className="icon-btn" aria-label="Close assistant" onClick={() => setOpen(false)}>×</button>
      </header>
      <div className="assistant-conversation" role="log" aria-label="Conversation" aria-live="polite">
        {!messages.length && <div className="assistant-welcome"><span className="assistant-sparkle" aria-hidden="true">✦</span><h3>A little help with your day.</h3><p>Search your inbox, find replies that need attention, or check when you’re free.{email ? " I can also help with the selected email." : ""}</p></div>}
        {messages.map((message, index) => <div key={index} className={`assistant-message ${message.role}`}>
          <p>{message.content || (message.incomplete && busy ? activity : "")}{message.incomplete && busy && message.content && <span className="assistant-cursor" aria-hidden="true">▍</span>}</p>
          {!!message.sources?.length && <details className="assistant-sources"><summary>Source emails · {message.sources.length}</summary>{message.sources.map(source => <button key={source.id} onClick={() => { onOpenEmail(source.id); setOpen(false); }}>{source.subject || "(no subject)"} ↗</button>)}</details>}
          {message.incomplete && !busy && message.content && <small>Response interrupted</small>}
          {message.revision && <div className="assistant-revision"><strong>Suggested reply</strong><pre>{message.revision}</pre>
            <p>Based on the saved draft. Replacing editor text also replaces unsaved edits. Review and save before sending.</p>
            <button disabled={!email || busy} onClick={() => {
              if (email && message.draftId) { onUseRevision(email.id, message.draftId, message.revision!); setOpen(false); }
            }}>Replace editor text</button>
          </div>}
        </div>)}
        <div ref={bottom} />
      </div>
      <div className="assistant-prompts">
        {["What needs my attention?", "When am I free this week?", ...(email ? ["What do I need to answer?", "Make the saved draft shorter"] : ["How does learning from edits work?"])].map(prompt => <button key={prompt} disabled={busy} onClick={() => ask(prompt)}>{prompt}</button>)}
        <button onClick={() => { setOpen(false); onCalendar(); }}>Find a meeting time ↗</button>
      </div>
      {error && <p className="assistant-error" role="alert">{error}</p>}
      <form className="assistant-composer" onSubmit={event => { event.preventDefault(); ask(input); }}>
        <input ref={composer} aria-label="Message MailAssist" placeholder="Ask about your email…" maxLength={2000} value={input} disabled={busy} onChange={event => setInput(event.target.value)} />
        <button type="submit" disabled={busy || !input.trim()} aria-label="Send message">↑</button>
      </form>
      <p className="assistant-disclaimer">Suggestions only. Nothing is sent or changed automatically.</p>
    </section>}
    {!open && <button className="assistant-launcher" title="Ask MailAssist" aria-expanded={false} aria-label="Ask MailAssist" onClick={() => setOpen(true)}><ChatIcon /></button>}
  </div>;
}
