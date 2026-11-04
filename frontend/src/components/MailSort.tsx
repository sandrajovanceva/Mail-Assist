import { useEffect, useRef, useState } from "react";

const OPTIONS = [
  ["newest", "Newest first"],
  ["oldest", "Oldest first"],
  ["unread", "Unread first"],
  ["sender", "Sender A–Z"],
] as const;

export function MailSort({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className="mail-sort" ref={root} onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
    }}>
      <button className={`mail-sort-trigger ${open ? "is-open" : ""}`} ref={trigger}
        aria-expanded={open} aria-controls="mail-sort-options" onClick={() => setOpen(!open)}>
        <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
          <path d="M3 5h14M3 10h10M3 15h6" />
        </svg>
        {OPTIONS.find(([key]) => key === value)?.[1]}
        <svg className="mail-sort-chevron" width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m3 4.5 3 3 3-3" /></svg>
      </button>
      {open && <div className="mail-sort-popover" id="mail-sort-options" role="group" aria-label="Sort emails">
        <div className="mail-sort-heading">SORT BY</div>
        {OPTIONS.map(([key, label]) => <button key={key} className="mail-sort-option" aria-pressed={value === key}
          onClick={() => { onChange(key); setOpen(false); trigger.current?.focus(); }}>
          <span className="mail-sort-check" aria-hidden="true">{value === key && <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m3 8 3 3 7-7" /></svg>}</span>
          {label}
        </button>)}
      </div>}
    </div>
  );
}
