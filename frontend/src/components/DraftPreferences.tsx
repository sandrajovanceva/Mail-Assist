import type { Draft } from "../api";

export function DraftPreferences({ draft }: { draft: Draft }) {
  const preferences = draft.preferences_used;
  return <details className="draft-preferences">
    <summary>Preferences used <span>{preferences ? preferences.length : ""}</span></summary>
    <div className="draft-preferences-body">
      {preferences ? <>
        <p>These preferences were supplied to the AI when this draft was generated. They describe its inputs, not proof that it followed every instruction. Later changes to your settings do not change this record.</p>
        {preferences.map((item, index) => <div className="draft-preference" key={index}>
          <strong>{item.title}</strong>
          <span className="draft-preference-kind">{item.kind.replaceAll("_", " ")}</span>
          <p>{item.content}</p>
        </div>)}
      </> : <p>Preferences were not recorded for this older draft. Generate a new draft to capture them.</p>}
    </div>
  </details>;
}
