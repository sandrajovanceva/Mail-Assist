import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  auth,
  type Account,
  type Email,
  type EmailQuery,
  type SyncJob,
  type User,
} from "./api";
import { AuthPage } from "./components/AuthPage";
import { MailSort } from "./components/MailSort";
import { CalendarPage } from "./components/CalendarPage";
import { AssistantChat } from "./components/AssistantChat";
import { DraftsQueue } from "./components/DraftsQueue";
import { EmailDetail } from "./components/EmailDetail";
import {
  AlertIcon,
  ArchiveIcon,
  BanIcon,
  ChartIcon,
  CalendarIcon,
  ChatIcon,
  InboxIcon,
  LogoutIcon,
  NewsIcon,
  PenIcon,
  SearchIcon,
  SettingsIcon,
  SidebarIcon,
  StarIcon,
  TrashIcon,
} from "./components/Icons";
import { InsightsPage } from "./components/InsightsPage";
import { SettingsPage } from "./components/SettingsPage";
import { SyncProgress } from "./components/SyncProgress";
import "./App.css";
import { watchSync } from "./syncPolling";

const CATEGORIES = [
  { key: "important", label: "Important", Icon: AlertIcon, className: "cat-important" },
  { key: "routine", label: "Routine", Icon: ChatIcon, className: "cat-routine" },
  { key: "newsletter", label: "Newsletters", Icon: NewsIcon, className: "cat-newsletter" },
  { key: "spam", label: "Spam", Icon: BanIcon, className: "cat-spam" },
] as const;

/** Remembered per browser; a layout preference, not data worth syncing. */
const SIDEBAR_KEY = "mailassist_sidebar_collapsed";

/** How often to check Gmail for new mail while the tab is open and visible. */
const AUTO_SYNC_INTERVAL_MS = 3 * 60 * 1000;

type View = "inbox" | "drafts" | "settings" | "insights" | "calendar";
/** Which slice of the mailbox the list is showing. */
type Filter =
  | { kind: "all" | "attention" | "reply" | "archived" }
  | { kind: "category"; key: string };

function initials(user: User): string {
  const source = user.name.trim() || user.email;
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

function toQuery(filter: Filter, search: string): EmailQuery {
  const base: EmailQuery = search ? { q: search } : {};
  switch (filter.kind) {
    case "attention":
      return { ...base, needs_attention: true };
    case "reply":
      return { ...base, needs_reply_only: true };
    case "archived":
      return { ...base, archived: true };
    case "category":
      return { ...base, category: filter.key };
    default:
      return base;
  }
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [view, setView] = useState<View>("inbox");
  const [account, setAccount] = useState<Account | null>(null);
  const [hasDemo, setHasDemo] = useState(false);
  const [emails, setEmails] = useState<Email[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [assistantRevision, setAssistantRevision] = useState<{ draftId: number; content: string } | null>(null);
  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("newest");
  const [defaultTone, setDefaultTone] = useState("professional");
  const [syncJob, setSyncJob] = useState<SyncJob | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [firstRun, setFirstRun] = useState(false);
  const [lastSynced, setLastSynced] = useState<Date | null>(null);
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_KEY) === "1",
  );
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const cancelSyncPoll = useRef<(() => void) | null>(null);
  const syncGeneration = useRef(0);
  // mirrors of state the auto-sync interval reads, so it can see current values
  // without being torn down and rebuilt on every sync
  const syncingRef = useRef(false);
  const lastSyncedRef = useRef<Date | null>(null);

  const session = useRef(0);
  const refreshRequest = useRef(0);
  const onError = useCallback((msg: string) => {
    if (/aborted|Session changed/i.test(msg)) return;
    setError(msg);
    setTimeout(() => setError(""), 6000);
  }, []);

  // restore session on load
  useEffect(() => {
    if (!auth.token()) {
      setAuthChecked(true);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => auth.clear())
      .finally(() => setAuthChecked(true));
  }, []);

  const refresh = useCallback(() => {
    const owner = session.current;
    const requestId = ++refreshRequest.current;
    api
      .emails(toQuery(filter, search))
      .then(rows => { if (owner === session.current && requestId === refreshRequest.current) setEmails(rows); })
      .catch((e) => onError(e.message));
    // the sidebar counts always describe the whole mailbox, not the current filter
    api
      .emails({})
      .then((all) => {
        if (owner !== session.current) return;
        const next: Record<string, number> = { all: all.length, attention: 0, reply: 0 };
        for (const e of all) {
          next[e.category] = (next[e.category] ?? 0) + 1;
          if (e.needs_attention) next.attention += 1;
          if (e.needs_reply) next.reply += 1;
        }
        setCounts(next);
      })
      .catch(() => {});
  }, [filter, search, onError]);

  useEffect(() => {
    if (!user) return;
    const owner = session.current;
    api
      .accounts()
      .then((accounts) => {
        if (owner !== session.current) return;
        setAccount(accounts.find((a) => a.provider !== "demo") ?? null);
        setHasDemo(accounts.some((a) => a.provider === "demo"));
      })
      .catch((e) => { if (owner === session.current) onError(e.message); });
    api.settings().then((s) => { if (owner === session.current) setDefaultTone(s.default_tone); }).catch(() => {});
  }, [user, onError]);

  useEffect(() => {
    if (!user) return;
    const timer = window.setTimeout(refresh, search ? 250 : 0); // debounce typing
    return () => window.clearTimeout(timer);
  }, [user, refresh, search]);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
    } catch {
      // private browsing can refuse storage; the layout still works
    }
  }, [collapsed]);

  useEffect(() => {
    syncingRef.current = syncing;
  }, [syncing]);

  useEffect(() => {
    lastSyncedRef.current = lastSynced;
  }, [lastSynced]);

  useEffect(() => () => {
    syncGeneration.current += 1;
    cancelSyncPoll.current?.();
  }, []);

  /**
   * Sync now runs in the background, so follow the job until it finishes.
   *
   * `silent` is used by the automatic background sync: it should not announce
   * "0 new" every few minutes, and a transient failure (laptop asleep, backend
   * restarting) should not throw a red banner at someone who never asked for
   * the sync in the first place.
   */
  const sync = useCallback(
    async ({ silent = false }: { silent?: boolean } = {}) => {
      if (syncingRef.current) return;
      syncingRef.current = true;
      const generation = ++syncGeneration.current;
      cancelSyncPoll.current?.();
      setSyncing(true);
      setSyncJob(null);
      try {
        const started = await api.startSync();
        if (generation !== syncGeneration.current) return;
        const fail = (error: unknown) => {
          if (generation !== syncGeneration.current) return;
          syncingRef.current = false;
          setSyncing(false);
          setFirstRun(false);
          setSyncJob(null);
          if (!silent) onError(`Could not check sync progress. You can try again. ${error instanceof Error ? error.message : String(error)}`);
        };
        cancelSyncPoll.current = watchSync({
          request: () => api.syncStatus(started.id),
          update: setSyncJob,
          fail,
          finish: (status) => {
            syncingRef.current = false;
            setSyncing(false);
            setFirstRun(false);
            if (status.status === "done") setLastSynced(new Date());
            if (status.status === "error") {
              // A transient failure during a background sync isn't worth a banner,
              // but an expired Gmail connection is, otherwise mail silently
              // stops arriving and nothing says why.
              const needsReconnect = status.error.includes("Connect Gmail");
              if (!silent || needsReconnect) onError(status.error || "Sync failed");
            } else if (!silent || status.new_count > 0) {
              const parts = [`${status.new_count} new`];
              if (status.auto_sent_count) parts.push(`${status.auto_sent_count} auto-sent`);
              setNotice(`Sync complete: ${parts.join(", ")}.`);
              setTimeout(() => setNotice(""), 5000);
            }
            refresh();
          },
        });
      } catch (e) {
        if (generation !== syncGeneration.current) return;
        syncingRef.current = false;
        setSyncing(false);
        setFirstRun(false);
        if (!silent) onError(e instanceof Error ? e.message : String(e));
      }
    },
    [onError, refresh],
  );

  /**
   * Straight after the OAuth redirect, start the first sync without being
   * asked. An empty inbox is a dead end at the moment the user has just
   * granted access. The query parameter is stripped first so a refresh can't
   * trigger a second sync and burn API quota.
   */
  useEffect(() => {
    if (!user) return;
    const connected = new URLSearchParams(window.location.search).get("connected");
    if (!connected) return;
    window.history.replaceState({}, "", window.location.pathname);
    setFirstRun(true);
    setNotice(`Connected ${connected}`);
    setTimeout(() => setNotice(""), 5000);
    sync();
  }, [user, sync]);

  /**
   * Check for new mail on a timer so the inbox stays current without the user
   * pressing anything.
   *
   * Cheap on purpose: the backend only classifies messages it has not seen
   * before, so a poll that finds nothing new costs one Gmail API call and zero
   * model calls. It does not eat into the free-tier Gemini quota.
   *
   * Polling pauses while the tab is hidden, so a forgotten tab doesn't sync all
   * night, and resumes (syncing immediately if it has gone stale) on return.
   */
  useEffect(() => {
    if (!user || !account) return;

    const maybeSync = () => {
      if (document.visibilityState !== "visible") return;
      if (syncingRef.current) return;
      const since = lastSyncedRef.current ? Date.now() - lastSyncedRef.current.getTime() : Infinity;
      if (since < AUTO_SYNC_INTERVAL_MS) return;
      sync({ silent: true });
    };

    const ticker = window.setInterval(() => {
      setNow(Date.now()); // keeps the "synced Nm ago" label honest
      maybeSync();
    }, 30_000);
    document.addEventListener("visibilitychange", maybeSync);

    return () => {
      window.clearInterval(ticker);
      document.removeEventListener("visibilitychange", maybeSync);
    };
  }, [user, account, sync]);

  const loadDemo = async () => {
    try {
      await api.seedDemo();
      setHasDemo(true);
      refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const clearDemo = async () => {
    try {
      await api.clearDemo();
      setHasDemo(false);
      setSelectedId(null);
      refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const logout = () => {
    syncGeneration.current += 1;
    cancelSyncPoll.current?.();
    syncingRef.current = false;
    setSyncing(false);
    setSyncJob(null);
    setFirstRun(false);
    setLastSynced(null);
    session.current++;
    refreshRequest.current++;
    auth.clear();
    setCounts({}); setHasDemo(false); setSearch(""); setFilter({ kind: "all" });
    setSort("newest");
    setView("inbox"); setDefaultTone("professional"); setError(""); setNotice("");
    setUser(null);
    setEmails([]);
    setAccount(null);
    setSelectedId(null);
  };

  const show = (next: Filter) => {
    setFilter(next);
    setView("inbox");
    if (next.kind !== filter.kind ||
        (next.kind === "category" && filter.kind === "category" && next.key !== filter.key)) {
      setSelectedId(null);
    }
  };

  /** Opening an email marks it read, optimistically so the list doesn't flicker. */
  const open = (email: Email) => {
    setSelectedId(email.id);
    if (!email.is_read) {
      setEmails((rows) =>
        rows.map((r) => (r.id === email.id ? { ...r, is_read: true } : r)),
      );
      api.patchEmail(email.id, { is_read: true }).catch(() => {});
    }
  };

  const archive = async (email: Email) => {
    try {
      await api.patchEmail(email.id, { is_archived: !email.is_archived });
      if (selectedId === email.id) setSelectedId(null);
      refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const activeKey = useMemo(
    () => (filter.kind === "category" ? filter.key : filter.kind),
    [filter],
  );
  const selected = emails.find((e) => e.id === selectedId) ?? null;
  const sortedEmails = useMemo(() => [...emails].sort((a, b) => {
    const newest = new Date(b.received_at).getTime() - new Date(a.received_at).getTime() || b.id - a.id;
    if (sort === "oldest") return -newest;
    if (sort === "unread") return Number(a.is_read) - Number(b.is_read) || newest;
    if (sort === "sender") {
      const sender = (email: Email) => email.sender.replace(/<.*>/, "").trim() || email.sender;
      return sender(a).localeCompare(sender(b)) || newest;
    }
    return newest;
  }), [emails, sort]);
  const syncLabel =
    syncJob && syncJob.total
      ? `${syncJob.stage} ${syncJob.processed}/${syncJob.total}`
      : (syncJob?.stage ?? "starting");

  const syncedAgo = (() => {
    if (!lastSynced) return "";
    const seconds = Math.max(0, Math.round((now - lastSynced.getTime()) / 1000));
    if (seconds < 60) return "synced just now";
    const minutes = Math.round(seconds / 60);
    return minutes < 60 ? `synced ${minutes}m ago` : `synced ${Math.round(minutes / 60)}h ago`;
  })();

  if (!authChecked) return null;
  if (!user) return <AuthPage onAuth={setUser} />;

  const navItem = (
    key: string,
    label: string,
    Icon: typeof InboxIcon,
    target: Filter,
    className = "",
    count?: number,
  ) => (
    <button
      key={key}
      className={`nav-item ${className} ${view === "inbox" && activeKey === key ? "active" : ""}`}
      onClick={() => show(target)}
      title={count === undefined ? label : `${label} (${count})`}
    >
      <Icon />
      <span className="nav-text">{label}</span>
      {count !== undefined && <span className="count">{count}</span>}
    </button>
  );

  return (
    <div className="app">
      <header className="topbar glass">
        <button
          className="brand"
          onClick={() => {
            show({ kind: "all" });
            setSearch("");
          }}
          title="Go to inbox"
        >
          Mail<span>Assist</span>
        </button>
        <label className="search">
          <SearchIcon />
          <input
            placeholder="Search mail…"
            value={search}
            onChange={(e) => {
              // Searching escapes the current folder, otherwise typing a term
              // while "Needs you" is selected reports "no matches" for mail
              // that plainly exists.
              if (e.target.value && filter.kind !== "all") setFilter({ kind: "all" });
              setSearch(e.target.value);
            }}
          />
        </label>
        <div className="grow" />
        {account ? (
          // Always keep a way back to Google: the refresh token expires every
          // 7 days while the app is unverified, and without this the only cure
          // for a dead connection would be editing the database.
          <a className="account" href={api.googleLoginUrl()} title="Reconnect this mailbox">
            {account.email_address}
            <span className="reconnect">Reconnect</span>
          </a>
        ) : (
          <a href={api.googleLoginUrl()}>
            <button className="primary">Connect Gmail</button>
          </a>
        )}
        {syncedAgo && !syncing && <span className="synced-ago mono">{syncedAgo}</span>}
        <button onClick={() => sync()} disabled={syncing || !account}>
          {syncing ? `Syncing… ${syncLabel}` : "Sync inbox"}
        </button>
      </header>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="notice-banner">{notice}</div>}

      <div className="shell">
        <nav className={`sidebar glass ${collapsed ? "collapsed" : ""}`}>
          <div className="nav-label mailbox-head">
            <span>Mailbox</span>
            <button
              className="icon-btn sidebar-toggle"
              onClick={() => setCollapsed((v) => !v)}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              <SidebarIcon />
            </button>
          </div>
          {navItem("all", "All mail", InboxIcon, { kind: "all" }, "", counts.all ?? 0)}
          {navItem(
            "attention",
            "Needs you",
            StarIcon,
            { kind: "attention" },
            "cat-attention",
            counts.attention ?? 0,
          )}
          {navItem(
            "reply",
            "Needs reply",
            PenIcon,
            { kind: "reply" },
            "cat-reply",
            counts.reply ?? 0,
          )}
          {CATEGORIES.map(({ key, label, Icon, className }) =>
            navItem(key, label, Icon, { kind: "category", key }, className, counts[key] ?? 0),
          )}
          {navItem("archived", "Archived", ArchiveIcon, { kind: "archived" }, "subtle")}

          <div className="nav-label" style={{ paddingTop: 14 }}>
            Assistant
          </div>
          <button
            className={`nav-item ${view === "drafts" ? "active" : ""}`}
            onClick={() => setView("drafts")}
          >
            <PenIcon />
            <span className="nav-text">Draft queue</span>
          </button>
          <button
            className={`nav-item ${view === "insights" ? "active" : ""}`}
            onClick={() => setView("insights")}
          >
            <ChartIcon />
            <span className="nav-text">Insights</span>
          </button>

          <div className="spacer" />
          <button className={`nav-item ${view === "calendar" ? "active" : ""}`} onClick={() => setView("calendar")}>
            <CalendarIcon />
            <span className="nav-text">Calendar</span>
          </button>

          {hasDemo && (
            <button className="nav-item subtle" onClick={clearDemo}>
              <TrashIcon />
              <span className="nav-text">Clear demo data</span>
            </button>
          )}
          <button
            className={`nav-item ${view === "settings" ? "active" : ""}`}
            onClick={() => setView("settings")}
          >
            <SettingsIcon />
            <span className="nav-text">Settings</span>
          </button>

          <div className="account-card">
            <div className="avatar">{initials(user)}</div>
            <div className="account-info">
              <div className="account-name">{user.name || "Your account"}</div>
              <div className="account-email">{user.email}</div>
            </div>
            <button className="icon-btn" onClick={logout} title="Sign out">
              <LogoutIcon />
            </button>
          </div>
        </nav>

        <div style={{ display: view === "settings" ? "contents" : "none" }}>
          <SettingsPage onError={onError} onToneChange={setDefaultTone} active={view === "settings"} />
        </div>
        {view === "insights" && <InsightsPage onError={onError} />}
        <div style={{ display: view === "calendar" ? "contents" : "none" }}>
          <CalendarPage />
        </div>
        {view === "drafts" && (
          <DraftsQueue
            onError={onError}
            onOpenEmail={(emailId) => {
              const owner = session.current;
              api.email(emailId).then(email => {
                if (owner !== session.current) return;
                setSearch("");
                setEmails(rows => rows.some(row => row.id === email.id) ? rows : [email, ...rows]);
                setSelectedId(email.id);
                setFilter({ kind: email.is_archived ? "archived" : "all" });
                setView("inbox");
              }).catch(e => onError(e.message));
            }}
          />
        )}
        <div style={{ display: view === "inbox" ? "contents" : "none" }}>
            <div className="email-list glass">
              <div className="email-list-toolbar">
                <span className="email-list-count">{emails.length} {emails.length === 1 ? "email" : "emails"}</span>
                <MailSort value={sort} onChange={setSort} />
              </div>
              {(firstRun || (syncing && emails.length === 0)) && (
                <SyncProgress job={syncJob} />
              )}
              {!firstRun && !(syncing && emails.length === 0) && emails.length === 0 && (
                <div className="empty-state">
                  <p style={{ marginBottom: 14 }}>
                    {search
                      ? `Nothing matches “${search}”.`
                      : account
                        ? "No emails here. Hit “Sync inbox” to pull and classify your mail."
                        : "Connect your Gmail account to get started, or preview the app with sample data."}
                  </p>
                  {!account && !search && (
                    <button className="primary" onClick={loadDemo}>
                      Load demo inbox
                    </button>
                  )}
                </div>
              )}
              {sortedEmails.map((e) => (
                <div
                  key={e.id}
                  className={`email-row ${e.id === selectedId ? "selected" : ""} ${
                    e.is_read ? "" : "unread"
                  }`}
                  onClick={() => open(e)}
                >
                  <div className="row-top">
                    <span className="sender">
                      {e.sender.replace(/<.*>/, "").trim() || e.sender}
                    </span>
                    <span className="date mono">
                      {new Date(e.received_at).toLocaleDateString()}
                    </span>
                  </div>
                  <div className="subject">{e.subject || "(no subject)"}</div>
                  <div className="snippet">{e.snippet}</div>
                  <div className="row-chips">
                    {e.category && (
                      <span className={`chip mono ${e.category}`}>{e.category}</span>
                    )}
                    {e.needs_attention && (
                      <span className="chip mono attention">attention</span>
                    )}
                    <button
                      className="icon-btn row-archive"
                      title={e.is_archived ? "Move back to inbox" : "Archive"}
                      onClick={(event) => {
                        event.stopPropagation();
                        archive(e);
                      }}
                    >
                      <ArchiveIcon />
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div className="reading-pane glass">
              {selected ? (
                <EmailDetail
                  key={selected.id}
                  email={selected}
                  defaultTone={defaultTone}
                  assistantRevision={assistantRevision}
                  onError={onError}
                  onArchive={() => archive(selected)}
                />
              ) : (
                <div className="empty-state">
                  Select an email to read it and draft a reply.
                </div>
              )}
            </div>
        </div>
      </div>
      <AssistantChat key={user.id} email={view === "inbox" ? selected : null} onCalendar={() => setView("calendar")}
        onOpenEmail={emailId => {
          const owner = session.current;
          api.email(emailId).then(email => {
            if (owner !== session.current) return;
            setSearch(""); setFilter({ kind: email.is_archived ? "archived" : "all" });
            setEmails(rows => rows.some(row => row.id === email.id) ? rows : [email, ...rows]);
            open(email); setView("inbox");
          }).catch(error => onError(error.message));
        }}
        onUseRevision={(emailId, draftId, content) => {
          if (emailId === selectedId) setAssistantRevision({ draftId, content });
        }} />
    </div>
  );
}

export default App;
