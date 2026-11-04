import { useEffect, useId, useRef, useState } from "react";

export function CalendarSelect({ label, value, options, onChange }: {
  label: string; value: number; options: { value: number; label: string }[]; onChange: (value: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  const move = (index: number) => root.current?.querySelectorAll<HTMLButtonElement>('[role="option"]')[index]?.focus();
  return <div className="calendar-select-field">
    <span id={`${id}-label`}>{label}</span>
    <div className="calendar-select" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
      onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
      }}>
      <button type="button" className={`calendar-select-trigger ${open ? "is-open" : ""}`} ref={trigger}
        aria-labelledby={`${id}-label ${id}-value`} aria-haspopup="listbox" aria-expanded={open} aria-controls={`${id}-options`}
        onClick={() => setOpen(!open)} onKeyDown={event => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); setOpen(true);
            requestAnimationFrame(() => move(Math.max(0, options.findIndex(option => option.value === value))));
          }
        }}>
        <span id={`${id}-value`}>{options.find(option => option.value === value)?.label}</span>
        <svg width="12" height="16" viewBox="0 0 12 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m3 5 3-3 3 3m-6 6 3 3 3-3" /></svg>
      </button>
      {open && <div id={`${id}-options`} className="calendar-select-menu" role="listbox" aria-labelledby={`${id}-label`}>
        {options.map((option, index) => <button type="button" key={option.value} role="option" aria-selected={value === option.value}
          onKeyDown={event => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); move((index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length); }
            if (event.key === "Home" || event.key === "End") { event.preventDefault(); move(event.key === "Home" ? 0 : options.length - 1); }
          }} onClick={() => { onChange(option.value); setOpen(false); trigger.current?.focus(); }}>
          <span className="calendar-select-check" aria-hidden="true">{value === option.value && <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m3 8 3 3 7-7" /></svg>}</span>
          {option.label}
        </button>)}
      </div>}
    </div>
  </div>;
}
