import { useEffect, useState } from "react";
import { api } from "../api";
import { MonthCalendar, dateKey, parseDay } from "./MonthCalendar";
import { CalendarSelect } from "./CalendarSelect";
import { CalendarDateTime } from "./CalendarDateTime";

export function CalendarPage({ onChoose }: { onChoose?: (instructions: string) => void }) {
  const [connection, setConnection] = useState<{ connected: boolean; email: string | null } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.availability>> | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);
  const [applied, setApplied] = useState(false);
  const [focusedDate, setFocusedDate] = useState<string | null>(null);
  const [form, setForm] = useState(() => {
    const today = new Date();
    const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    return { start_date: localDate, days: 7, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Skopje",
      work_start: "09:00", work_end: "17:00", duration_minutes: 30, weekdays: [0, 1, 2, 3, 4] };
  });
  useEffect(() => {
    let cancelled = false;
    api.calendarStatus().then(value => { if (!cancelled) { setConnection(value); if (value.preferences) setForm(old => ({ ...old, ...value.preferences })); } })
      .catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, []);
  const update = (patch: Partial<typeof form>) => {
    setForm(previous => ({ ...previous, ...patch })); setResult(null); setSelected([]); setCopied(false); setApplied(false);
  };
  const label = (slot: { start: string; end: string }) => {
    const timeZone = result?.timezone || form.timezone;
    return `${new Date(slot.start).toLocaleString(undefined, { timeZone, weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} – ${new Date(slot.end).toLocaleTimeString(undefined, { timeZone, hour: "2-digit", minute: "2-digit" })}`;
  };
  const text = result ? `Available meeting times (${result.timezone}):\n${result.slots.filter(slot => selected.includes(slot.start)).map(label).join("\n")}` : "";
  const visibleSlots = result?.slots.filter(slot => !focusedDate || slot.start.slice(0, 10) === focusedDate) || [];
  return <section className="calendar-page glass">
    <div className="calendar-heading"><div><span className="calendar-eyebrow">GOOGLE CALENDAR</span>
      <h2>Find a time</h2></div>
      <a className="calendar-connect" href={api.calendarLoginUrl()}>{connection?.connected ? "Reconnect" : "Connect Calendar"}</a>
    </div>
    {connection && <p className="calendar-connection">{connection.connected ? `Connected · ${connection.email}` : "Connect Calendar to allow read-only availability checks."}</p>}
    {error && <p className="calendar-error" role="alert">{error}</p>}
    <form className="calendar-form" onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError(""); setResult(null); setSelected([]); setCopied(false); setApplied(false);
      try { setResult(await api.availability(form)); }
      catch (e) { setError(e instanceof Error ? e.message : String(e)); }
      finally { setBusy(false); }
    }}>
      <fieldset disabled={busy}>
        <div className="calendar-quick-controls">
          <CalendarDateTime label="Starting" kind="date" value={form.start_date} onChange={start_date => { update({ start_date }); setFocusedDate(null); }} />
          <CalendarSelect label="Meeting length" value={form.duration_minutes} options={[15, 30, 45, 60, 90].map(value => ({ value, label: `${value} minutes` }))} onChange={duration_minutes => update({ duration_minutes })} />
          <button className="primary calendar-find-button" disabled={!connection?.connected}>{busy ? "Checking calendar…" : "Find available times"}</button>
        </div>
        <details className="calendar-preferences">
          <summary><span>Availability preferences</span><span className="calendar-preferences-summary">{form.work_start}–{form.work_end} · {form.timezone}</span><svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m3 4.5 3 3 3-3" /></svg></summary>
          <div className="calendar-fields">
          <CalendarSelect label="Look ahead" value={form.days} options={[{ value: 7, label: "One week" }, { value: 14, label: "Two weeks" }]} onChange={days => update({ days })} />
          <label>Timezone<input required value={form.timezone} onChange={e => update({ timezone: e.target.value })} /></label>
          <CalendarDateTime label="Work starts" kind="time" value={form.work_start} onChange={work_start => update({ work_start })} />
          <CalendarDateTime label="Work ends" kind="time" value={form.work_end} onChange={work_end => update({ work_end })} />
        </div>
        <div className="calendar-days" role="group" aria-label="Working days">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, index) => <button key={day} type="button" aria-pressed={form.weekdays.includes(index)} onClick={() => update({ weekdays: form.weekdays.includes(index) ? form.weekdays.filter(n => n !== index) : [...form.weekdays, index] })}>{day}</button>)}</div>
        </details>
      </fieldset>
    </form>
    <MonthCalendar selectedDate={focusedDate || form.start_date} checkedStart={result ? form.start_date : undefined}
      checkedDays={form.days} slots={result?.slots || []} disabled={busy} onSelect={day => {
        setFocusedDate(day);
        const end = parseDay(form.start_date); end.setDate(end.getDate() + form.days);
        if (!result || day < form.start_date || day >= dateKey(end)) update({ start_date: day });
      }} />
    {result && <div className="calendar-results">
      <div className="calendar-results-heading"><strong>{focusedDate ? `Available times · ${parseDay(focusedDate).toLocaleDateString(undefined, { month: "long", day: "numeric" })}` : "Available times"}</strong><span>{result.timezone}</span></div>
      {focusedDate && <button className="link" onClick={() => setFocusedDate(null)}>Show all checked dates</button>}
      {!visibleSlots.length && <p className="calendar-footnote">No available times for your chosen working hours and meeting length.</p>}
      <p className="calendar-footnote">Primary calendar only. Times are not reserved and may change. Checked {new Date(result.checked_at).toLocaleTimeString()}.</p>
      <div className="calendar-slots">{visibleSlots.map(slot => <button key={slot.start} aria-pressed={selected.includes(slot.start)} onClick={() => { setSelected(old => old.includes(slot.start) ? old.filter(s => s !== slot.start) : [...old, slot.start]); setCopied(false); }}>{label(slot)}</button>)}</div>
      {result.slots.length > 0 && <button className="primary" disabled={!selected.length} onClick={async () => {
        if (onChoose) { onChoose(`Suggest only these calendar-checked times, preserving their timezone. Do not claim a meeting is booked.\n${text}\nAvailability checked at ${result.checked_at}.`); setApplied(true); }
        else { try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setError("Could not copy times. Please select and copy the times manually."); } }
      }}>{onChoose ? "Use selected times in draft" : copied ? "Copied" : "Copy selected times"}</button>}
      {applied && <p className="calendar-footnote" role="status">Times added to your instructions. Generate or regenerate the draft below to include them.</p>}
    </div>}
    <p className="calendar-footnote">Working preferences are saved after a successful availability check and used for meeting-related drafts. MailAssist checks fresh busy periods; it does not create events or read event titles.</p>
  </section>;
}
