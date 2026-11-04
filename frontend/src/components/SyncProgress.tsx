import type { SyncJob } from "../api";

const STAGE_COPY: Record<string, string> = {
  starting: "Connecting to your mailbox…",
  fetching: "Fetching your recent mail…",
  classifying: "Reading and sorting your inbox…",
  drafting: "Writing replies for mail that needs one…",
  "auto-sending": "Handling routine replies…",
  done: "Almost there…",
};

/**
 * First-run state shown straight after connecting a mailbox.
 *
 * Classifying an inbox is a dozen-plus model calls, so it takes long enough
 * that an empty list would read as "nothing happened". The backend reports
 * stage and per-email progress, so show both rather than a generic spinner.
 */
export function SyncProgress({ job }: { job: SyncJob | null }) {
  const stage = job?.stage ?? "starting";
  const total = job?.total ?? 0;
  const processed = job?.processed ?? 0;
  const pct = total > 0 ? Math.round((processed / total) * 100) : null;

  return (
    <div className="sync-progress">
      <div className="sync-orb">
        <span className="sync-orb-ring" />
        <span className="sync-orb-ring delay" />
        <span className="sync-orb-core" />
      </div>

      <h3>Preparing your inbox</h3>
      <p className="sync-stage">{STAGE_COPY[stage] ?? "Working…"}</p>

      <div className="sync-bar">
        <div
          className={`sync-bar-fill ${pct === null ? "indeterminate" : ""}`}
          style={pct === null ? undefined : { width: `${pct}%` }}
        />
      </div>
      <p className="sync-count mono">
        {pct === null ? "Looking for new mail" : `${processed} of ${total} emails`}
      </p>

      <ul className="sync-steps">
        {["fetching", "classifying", "drafting"].map((key) => {
          const order = ["starting", "fetching", "classifying", "drafting", "auto-sending", "done"];
          const state =
            order.indexOf(stage) > order.indexOf(key)
              ? "done"
              : stage === key
                ? "active"
                : "todo";
          return (
            <li key={key} className={state}>
              <span className="dot" />
              {key === "fetching" && "Downloading messages"}
              {key === "classifying" && "Sorting by importance"}
              {key === "drafting" && "Writing replies"}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
