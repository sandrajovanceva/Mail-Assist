import { useEffect, useState } from "react";
import { api, type AppSettings, type DraftEdit, type KnowledgeItem } from "../api";

const KINDS = ["template", "fact"];
const AUTO_SEND_CATEGORIES = ["routine", "newsletter"];

type Tab = "voice" | "automation" | "knowledge";
const TABS: { key: Tab; label: string; blurb: string }[] = [
  { key: "voice", label: "Writing style", blurb: "Choose how MailAssist writes and signs your replies." },
  { key: "automation", label: "Automation", blurb: "Choose when MailAssist creates drafts and sends replies." },
  { key: "knowledge", label: "Templates & facts", blurb: "Give MailAssist reusable replies and information to draw on." },
];

export function SettingsPage({ onError, onToneChange, active: isActive = true }: { onError: (msg: string) => void; onToneChange?: (tone: string) => void; active?: boolean }) {
  const [settings, setSettings] = useState<AppSettings>({
    user_name: "",
    signature: "",
    default_tone: "professional",
    style_profile: "",
    auto_draft_enabled: true,
    auto_draft_limit: 5,
    auto_send_enabled: false,
    auto_send_categories: "routine",
    auto_send_daily_limit: 5,
  });
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [edits, setEdits] = useState<DraftEdit[]>([]);
  const [form, setForm] = useState({ kind: "template", title: "", content: "" });
  const [baseline, setBaseline] = useState<AppSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [showStyle, setShowStyle] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [learning, setLearning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [tab, setTab] = useState<Tab>("voice");

  const loadEdits = () => {
    api.draftEdits().then(setEdits).catch(() => {});
  };

  useEffect(() => {
    api.settings().then((value) => { setSettings(value); setBaseline(value); setLoaded(true); onToneChange?.(value.default_tone); }).catch((e) => onError(e.message));
    api.knowledge().then(setItems).catch((e) => onError(e.message));
  }, [onError, onToneChange]);

  // Settings stays mounted to preserve unsaved preferences. Refresh examples
  // when reopened so edits saved elsewhere are visible without a page reload.
  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    api.draftEdits().then((rows) => { if (!cancelled) setEdits(rows); })
      .catch((error) => { if (!cancelled) onError(error.message); });
    return () => { cancelled = true; };
  }, [isActive, onError]);

  const learnStyle = async () => {
    setLearning(true);
    try {
      const result = await api.learnStyle();
      setSettings((s) => ({ ...s, style_profile: result.style_profile }));
      setBaseline((s) => s ? { ...s, style_profile: result.style_profile } : s);
      setNotice("Writing style learned and saved.");
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setLearning(false);
    }
  };

  const applyEdits = async () => {
    setApplying(true);
    try {
      const result = await api.applyEdits();
      setSettings((s) => ({ ...s, style_profile: result.style_profile }));
      setBaseline((s) => s ? { ...s, style_profile: result.style_profile } : s);
      setNotice("Writing style updated and saved.");
      loadEdits();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const result = await api.saveSettings(settings);
      setSettings(result);
      setBaseline(result);
      onToneChange?.(result.default_tone);
      setNotice("Changes saved.");
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const addItem = async () => {
    if (!form.title || !form.content) return;
    try {
      const created = await api.addKnowledge(form);
      setItems([created, ...items]);
      setForm({ kind: "template", title: "", content: "" });
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async (id: number) => {
    try {
      await api.deleteKnowledge(id);
      setItems(items.filter((i) => i.id !== id));
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const dropEdit = async (id: number) => {
    try {
      await api.deleteEdit(id);
      setEdits(edits.filter((e) => e.id !== id));
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const selectedCategories = new Set(
    settings.auto_send_categories.split(",").filter(Boolean),
  );
  const toggleCategory = (category: string) => {
    const next = new Set(selectedCategories);
    if (next.has(category)) next.delete(category);
    else next.add(category);
    setSettings({ ...settings, auto_send_categories: [...next].join(",") });
  };

  const unapplied = edits.filter((e) => !e.applied);
  const active = TABS.find((t) => t.key === tab)!;

  const dirty = baseline !== null && JSON.stringify(settings) !== JSON.stringify(baseline);
  const busy = saving || learning || applying;
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const toggle = (label: string, checked: boolean, onChange: (v: boolean) => void) => (
    <label className="switch">
      <input aria-label={label} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="slider" />
    </label>
  );

  const examples: Record<string, string> = {
    professional: "Thanks for reaching out. Tuesday works well for me. Please send over a time that suits you.",
    friendly: "Thanks for getting in touch! Tuesday sounds great. Let me know what time works for you.",
    formal: "Thank you for your message. I am available on Tuesday. Please advise of your preferred time.",
    casual: "Tuesday works for me! What time were you thinking?",
  };

  return (
    <section className="glass settings-page">
      <header className="settings-header">
        <h1>Settings</h1>
        <nav className="settings-tabs" aria-label="Settings sections">
          {TABS.map((t) => (
            <button key={t.key} aria-current={tab === t.key ? "page" : undefined}
              className={tab === t.key ? "selected" : ""} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <div className="settings-content" aria-busy={!loaded || busy}>
        <fieldset className="settings-controls" disabled={!loaded || busy}>
        <header className="settings-intro">
          <p>{active.blurb}</p>
        </header>

        {tab === "voice" && (
          <>
            <div className="voice-layout">
            <div className="setting-card identity-card">
              <h3>Your details</h3>
              <div className="field">
                <label htmlFor="settings-name">Your name</label>
                <input
                  id="settings-name" value={settings.user_name}
                  onChange={(e) => setSettings({ ...settings, user_name: e.target.value })}
                />
                <span className="voice-hint">Used to sign replies.</span>
              </div>
              <div className="field">
                <label htmlFor="settings-signature">Signature</label>
                <textarea
                  placeholder={"Best regards,\nSandra"}
                  id="settings-signature" value={settings.signature}
                  onChange={(e) => setSettings({ ...settings, signature: e.target.value })}
                />
              </div>
              <div className="field last">
                <span id="tone-label" className="field-label">Default tone</span>
                <div className="tone-options" role="group" aria-labelledby="tone-label">
                  {["professional", "friendly", "formal", "casual"].map((tone) => (
                    <button key={tone} aria-pressed={settings.default_tone === tone}
                      onClick={() => setSettings({ ...settings, default_tone: tone })}>{tone}</button>
                  ))}
                </div>
              </div>
            </div>
            <aside className="reply-preview">
              <div className="preview-label">Example reply <span>Illustrative preview</span></div>
              <div className="preview-message">
                <p>{settings.default_tone === "formal" ? "Dear Alex," : "Hi Alex,"}</p>
                <p>{examples[settings.default_tone]}</p>
                <p className="preview-signature">{settings.signature || settings.user_name || "Your signature"}</p>
              </div>
              <p className="preview-note">Shows your selected tone and signature. Actual drafts also use your learned style and the email context.</p>
            </aside>
            </div>

            <div className="setting-card">
              <div className="card-head">
                <h3>Personal writing style</h3>
                <button className="secondary" onClick={learnStyle} disabled={learning || !loaded || saving || applying || settings.style_profile !== baseline?.style_profile}>
                  {learning
                    ? "Reading your emails"
                    : settings.style_profile
                      ? "Learn again"
                      : "Learn from sent emails"}
                </button>
              </div>
              <p className="card-sub">
                Reads your ~30 most recent sent emails. The result is plain text, so you can
                correct anything it got wrong.
              </p>
              <p className="style-status">{settings.style_profile ? "Your personal style guide is ready." : "No style learned yet. Your selected tone will guide drafts."}</p>
              {(settings.style_profile || baseline?.style_profile || showStyle) && <details open={showStyle} onToggle={(e) => setShowStyle(e.currentTarget.open)}>
              <summary>Edit style guide</summary>
              <label className="sr-only" htmlFor="style-guide">Personal style guide</label>
              <textarea id="style-guide"
                className="style-profile"
                placeholder="Your learned style guide will appear here: greetings, sign-offs, phrasing habits."
                value={settings.style_profile}
                onChange={(e) => setSettings({ ...settings, style_profile: e.target.value })}
              />
              </details>}
              <p className="save-hint">Save or discard manual style-guide edits before learning again. Learning saves the new guide immediately.</p>
            </div>

          </>
        )}

        {tab === "automation" && (
          <>
            <p className="automation-summary">
              {settings.auto_draft_enabled ? `Create up to ${settings.auto_draft_limit} drafts per sync.` : "Automatic drafting is off."}{" "}
              {settings.auto_send_enabled
                ? `Send up to ${settings.auto_send_daily_limit} existing automatic drafts per day for ${[...selectedCategories].join(", ") || "no selected categories"}.`
                : "Every reply needs your approval before sending."}
              {dirty && " Changes take effect after saving."}
            </p>
            <div className="setting-card">
              <div className="card-head">
                <h3>Automatically create drafts</h3>
                {toggle("Automatically create drafts", settings.auto_draft_enabled, (v) =>
                  setSettings({ ...settings, auto_draft_enabled: v }),
                )}
              </div>
              <p className="card-sub">
                When new mail arrives that wants an answer, write the reply in advance so it is
                waiting in your draft queue.
              </p>
              {settings.auto_draft_enabled && (
                <div className="field last inset">
                  <label htmlFor="draft-limit">Maximum drafts per sync</label>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    id="draft-limit" value={settings.auto_draft_limit}
                    onChange={(e) =>
                      setSettings({ ...settings, auto_draft_limit: Number(e.target.value) })
                    }
                  />
                  <span className="voice-hint">
                    Limits how many replies are prepared each time your inbox syncs.
                  </span>
                </div>
              )}
            </div>

            <div className="setting-card">
              <div className="card-head">
                <h3>Automatically send replies</h3>
                {toggle("Automatically send replies", settings.auto_send_enabled, (v) =>
                  setSettings({ ...settings, auto_send_enabled: v }),
                )}
              </div>
              <p className="card-sub">
                Send untouched, automatically prepared drafts for eligible mail. No new replies are generated here. Important mail, and anything flagged
                as needing your attention, is never sent automatically.
              </p>
              {settings.auto_send_enabled && (
                <div className="inset">
                  <div className="field">
                    <label>Categories eligible for auto-send</label>
                    <div className="chip-picker">
                      {AUTO_SEND_CATEGORIES.map((category) => (
                        <button
                          key={category}
                          aria-pressed={selectedCategories.has(category)} className={`pick ${selectedCategories.has(category) ? "on" : ""}`}
                          onClick={() => toggleCategory(category)}
                        >
                          {category}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="field last">
                    <label htmlFor="send-limit">Maximum auto-sent replies per day</label>
                    <input
                      type="number"
                      min={0}
                      max={50}
                      id="send-limit" value={settings.auto_send_daily_limit}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          auto_send_daily_limit: Number(e.target.value),
                        })
                      }
                    />
                    <span className="voice-hint">
                      MailAssist stops sending automatically once this daily limit is reached.
                    </span>
                  </div>
                </div>
              )}
            </div>

          </>
        )}

        {tab === "voice" && (
          <details className="learning-examples">
            <summary>Review learning examples <span className="count">{unapplied.length} new</span></summary>
            {edits.length === 0 ? (
              <div className="empty-card">
                <strong>No examples yet</strong>
                <p>
                  Edit a generated draft before sending it, and the before and after will appear
                  here for you to review.
                </p>
              </div>
            ) : (
              <>
                <div className="setting-card apply-card">
                  <div className="apply-text">
                    <strong>
                      {unapplied.length > 0
                        ? `${unapplied.length} new example${unapplied.length > 1 ? "s" : ""}`
                        : "All examples applied"}
                    </strong>
                    <p className="card-sub">
                      Folding these into your style guide rewrites it. You can edit the result
                      afterwards.
                    </p>
                  </div>
                  <button
                    className="primary"
                    onClick={applyEdits}
                    disabled={applying || unapplied.length === 0 || settings.style_profile !== baseline?.style_profile}
                  >
                    {applying ? "Updating" : "Learn from these"}
                  </button>
                </div>

                {edits.map((edit) => (
                  <div className="setting-card" key={edit.id}>
                    <div className="card-head">
                      <h3>{edit.subject || "(no subject)"}</h3>
                      <span className={`chip mono ${edit.applied ? "routine" : "attention"}`}>
                        {edit.applied ? "learned" : "new"}
                      </span>
                    </div>
                    <div className="diff">
                      <div>
                        <span className="mono">AI wrote</span>
                        <pre>{edit.original}</pre>
                      </div>
                      <div>
                        <span className="mono">Your saved edit</span>
                        <pre>{edit.edited}</pre>
                      </div>
                    </div>
                    <button className="subtle-action danger" onClick={() => dropEdit(edit.id)}>
                      Delete this example
                    </button>
                  </div>
                ))}
              </>
            )}
          </details>
        )}

        {tab === "knowledge" && (
          <>
            <div className="setting-card">
              <div className="card-head">
                <h3>Add a template or fact</h3>
              </div>
              <div className="kb-form">
                <div className="row">
                  <div className="material-options" role="group" aria-label="Material type">
                    {KINDS.map((kind) => (
                      <label key={kind} className="segment-option">
                        <input type="radio" name="material-type" value={kind}
                          checked={form.kind === kind}
                          onChange={() => setForm({ ...form, kind })} />
                        <span>{kind === "template" ? "Template" : "Fact"}</span>
                      </label>
                    ))}
                  </div>
                  <input
                    aria-label="Title" placeholder="Title, for example: Meeting decline template"
                    value={form.title}
                    onChange={(e) => setForm({ ...form, title: e.target.value })}
                  />
                </div>
                <textarea
                  aria-label="Content" placeholder="Content the AI should use or follow"
                  value={form.content}
                  onChange={(e) => setForm({ ...form, content: e.target.value })}
                />
                <div>
                  <button
                    className="primary"
                    onClick={addItem}
                    disabled={!form.title || !form.content}
                  >
                    Add
                  </button>
                </div>
              </div>
            </div>

            {items.length === 0 ? (
              <div className="empty-card">
                <strong>Nothing saved yet</strong>
                <p>
                  Add a reply template or a fact above, and the AI will start drawing on it in
                  your drafts.
                </p>
              </div>
            ) : (
              items.map((item) => (
                <div className="setting-card" key={item.id}>
                  <div className="card-head">
                    <h3>{item.title}</h3>
                    <span className="chip mono routine">{item.kind}</span>
                  </div>
                  <p className="kb-body">{item.content}</p>
                  <button className="subtle-action danger" onClick={() => remove(item.id)}>
                    Delete
                  </button>
                </div>
              ))
            )}
          </>
        )}
        </fieldset>
      </div>
      <footer className="settings-save">
        <span role="status">{!loaded ? "Loading settings..." : dirty ? "Unsaved changes" : notice || "All changes saved"}</span>
        <div className="save-actions">
          {dirty && <button disabled={busy} onClick={() => { if (baseline) setSettings(baseline); setNotice(""); }}>Discard</button>}
          <button className="primary" disabled={!loaded || !dirty || busy} onClick={save}>{saving ? "Saving..." : "Save changes"}</button>
        </div>
      </footer>
    </section>
  );
}
