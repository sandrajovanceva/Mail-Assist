import { useEffect, useState } from "react";

export const dateKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
export const parseDay = (value: string) => {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
};

export function monthDays(month: Date) {
  const start = new Date(month.getFullYear(), month.getMonth(), 1);
  start.setDate(1 - (start.getDay() + 6) % 7);
  const last = new Date(month.getFullYear(), month.getMonth() + 1, 0);
  const length = Math.ceil(((new Date(month.getFullYear(), month.getMonth(), 1).getDay() + 6) % 7 + last.getDate()) / 7) * 7;
  return Array.from({ length }, (_, index) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + index));
}

export function MonthCalendar({ selectedDate, checkedStart, checkedDays, slots, disabled, onSelect }: {
  selectedDate: string; checkedStart?: string; checkedDays: number;
  slots: { start: string; end: string }[]; disabled: boolean; onSelect: (date: string) => void;
}) {
  const [month, setMonth] = useState(() => parseDay(selectedDate));
  useEffect(() => { if (selectedDate) setMonth(parseDay(selectedDate)); }, [selectedDate]);
  const today = dateKey(new Date());
  const counts = new Map<string, number>();
  slots.forEach(slot => { const key = slot.start.slice(0, 10); counts.set(key, (counts.get(key) || 0) + 1); });
  const checkedEnd = checkedStart ? parseDay(checkedStart) : null;
  checkedEnd?.setDate(checkedEnd.getDate() + checkedDays);
  const endKey = checkedEnd ? dateKey(checkedEnd) : "";
  const title = month.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  return <div className="month-calendar">
    <div className="month-calendar-toolbar">
      <h3 aria-live="polite">{title}</h3>
      <div className="month-calendar-navigation">
        <button type="button" onClick={() => { setMonth(new Date()); onSelect(today); }} disabled={disabled}>Today</button>
        <button type="button" aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>‹</button>
        <button type="button" aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>›</button>
      </div>
    </div>
    <div className="month-calendar-grid" role="group" aria-label={title}>
      {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(day => <div className="month-weekday" key={day}>{day}</div>)}
      {monthDays(month).map(day => {
        const key = dateKey(day);
        const checked = !!checkedStart && key >= checkedStart && key < endKey;
        const count = counts.get(key) || 0;
        return <button type="button" key={key} disabled={disabled}
          className={`month-day ${day.getMonth() !== month.getMonth() ? "outside-month" : ""} ${key === today ? "is-today" : ""} ${key === selectedDate ? "is-selected" : ""}`}
          aria-pressed={key === selectedDate} aria-current={key === today ? "date" : undefined}
          aria-label={`${day.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" })}, ${checked ? `${count} available times` : "availability not checked"}`}
          onClick={() => onSelect(key)}>
          <span className="month-day-number">{day.getDate()}</span>
          {checked && <span className={`month-day-status ${count ? "has-slots" : "no-slots"}`}>{count ? `${count} free slots` : "No free slots"}</span>}
        </button>;
      })}
    </div>
    <p className="calendar-footnote month-calendar-note">Select a date to view its times. Unmarked dates have not been checked.</p>
  </div>;
}
