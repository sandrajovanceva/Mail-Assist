import { useEffect, useRef, useState } from "react";
import { dateKey, monthDays, parseDay } from "./MonthCalendar";

export function CalendarDateTime({ label, value, kind, onChange }: {
  label: string; value: string; kind: "date" | "time"; onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(() => kind === "date" ? parseDay(value) : new Date());
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  const [hour, minute] = kind === "time" ? value.split(":").map(Number) : [0, 0];
  const display = kind === "date" ? parseDay(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
    : `${String(hour % 12 || 12).padStart(2, "0")}:${String(minute).padStart(2, "0")} ${hour >= 12 ? "PM" : "AM"}`;
  const choose = (next: string) => { onChange(next); setOpen(false); trigger.current?.focus(); };
  const changeTime = (h: number, m: number) => onChange(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
  return <div className="calendar-select-field">
    <span>{label}</span>
    <div className="calendar-select" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
      onKeyDown={event => { if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); } }}>
      <button type="button" className={`calendar-select-trigger ${open ? "is-open" : ""}`} ref={trigger} aria-label={`${label}: ${display}`} aria-expanded={open} aria-haspopup="dialog"
        onClick={() => { if (!open && kind === "date") setMonth(parseDay(value)); setOpen(!open); }}>
        {display}
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
          {kind === "date" ? <><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M7 3v4m10-4v4M3 11h18" /></> : <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>}
        </svg>
      </button>
      {open && <div className={`calendar-input-popover ${kind === "time" ? "time-popover" : "date-popover"}`} role="dialog" aria-label={`Choose ${label.toLowerCase()}`}>
        {kind === "date" ? <>
          <div className="date-picker-head"><strong aria-live="polite">{month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</strong>
            <button type="button" aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>‹</button>
            <button type="button" aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>›</button>
          </div>
          <div className="date-picker-grid">
            {["M", "T", "W", "T", "F", "S", "S"].map((day, index) => <span key={index}>{day}</span>)}
            {monthDays(month).map(day => <button type="button" key={dateKey(day)} aria-label={day.toLocaleDateString(undefined, { dateStyle: "full" })}
              aria-pressed={dateKey(day) === value} className={day.getMonth() !== month.getMonth() ? "muted" : ""}
              onClick={() => choose(dateKey(day))}>{day.getDate()}</button>)}
          </div>
          <button type="button" className="date-picker-today" onClick={() => choose(dateKey(new Date()))}>Today</button>
        </> : <>
          <div className="time-picker-columns">
            <div role="group" aria-label="Hour">{Array.from({ length: 12 }, (_, i) => i + 1).map(h => <button type="button" key={h} aria-pressed={hour % 12 === h % 12} onClick={() => changeTime(h % 12 + (hour >= 12 ? 12 : 0), minute)}>{String(h).padStart(2, "0")}</button>)}</div>
            <span className="time-picker-colon">:</span>
            <div role="group" aria-label="Minute">{Array.from({ length: 60 }, (_, m) => <button type="button" key={m} aria-pressed={minute === m} onClick={() => changeTime(hour, m)}>{String(m).padStart(2, "0")}</button>)}</div>
            <div role="group" aria-label="AM or PM">{["AM", "PM"].map((period, index) => <button type="button" key={period} aria-pressed={Number(hour >= 12) === index} onClick={() => changeTime(hour % 12 + index * 12, minute)}>{period}</button>)}</div>
          </div>
          <button type="button" className="time-picker-done" onClick={() => { setOpen(false); trigger.current?.focus(); }}>Done</button>
        </>}
      </div>}
    </div>
  </div>;
}
