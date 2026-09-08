import { useRef, useState } from "react";
import { api, apiUrl, errorMessage, type Identity } from "../lib/api";

const legacyKey = "changelorg:user-state:v1";

export function AccountArea({ identity, onReload, onEdit }: { identity: Identity; onReload: () => Promise<void>; onEdit: () => void }) {
  const marker = `changelorg:import-dismissed:v1:${identity.id}`;
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(marker) === "1"; } catch { return false; }
  });
  const [hasLegacy, setHasLegacy] = useState(() => {
    try { return localStorage.getItem(legacyKey) !== null; } catch { return false; }
  });
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [message, setMessage] = useState("");

  async function importData(legacy: boolean) {
    if (lock.current || !window.confirm(legacy
      ? "Import old browser reading state for sources that belong to this account? Existing server state takes precedence."
      : "Import this browser's anonymous profiles, sources, and changes into your GitHub account? Anonymous data and the browser identifier stay intact.")) return;
    lock.current = true;
    setBusy(true);
    setMessage("");
    try {
      if (legacy) {
        const parsed: unknown = JSON.parse(localStorage.getItem(legacyKey) ?? "null");
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setMessage("Old browser state is invalid. Nothing was imported.");
          return;
        }
        const state: Record<string, { dismissed?: boolean; saved?: boolean; note?: string }> = Object.create(null);
        for (const [key, value] of Object.entries(parsed)) {
          if (!value || typeof value !== "object" || Array.isArray(value)) continue;
          state[key] = {
            ...("dismissed" in value && typeof value.dismissed === "boolean" ? { dismissed: value.dismissed } : {}),
            ...("saved" in value && typeof value.saved === "boolean" ? { saved: value.saved } : {}),
            ...("note" in value && typeof value.note === "string" ? { note: value.note } : {}),
          };
        }
        await api("/me/import-state", { owner: identity.id, method: "POST", body: { state } });
        setHasLegacy(false);
        setMessage("Browser state import completed. The server remains the source of truth.");
      } else {
        const counts = await api<{ profiles: number; sources: number; changes: number }>("/me/import", { owner: identity.id, method: "POST" });
        setMessage(`Imported ${counts.profiles} profiles, ${counts.sources} sources, and ${counts.changes} changes.`);
        setDismissed(true);
        try { localStorage.setItem(marker, "1"); } catch { /* Import succeeded; a browser preference must not block the server reload. */ }
      }
      await onReload();
    } catch (cause) {
      setMessage(errorMessage(cause));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  return (
    <header className="account-area relative mx-auto mb-5 max-w-7xl border-y-2 border-stone-950 py-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-[0.65rem] font-black uppercase tracking-[0.3em]">Your personal change newspaper</p>
          <h1 className="font-serif text-4xl font-black tracking-tight">changelorg<span className="text-amber-800">.</span></h1>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="font-semibold">{identity.authenticated ? `GitHub / ${identity.login ?? "Account"}` : "Anonymous edition"}</span>
          {identity.authenticated ? <a className="underline underline-offset-4" href={`${apiUrl}/auth/sign_out?rd=/`}>Sign out</a>
            : identity.auth_enabled ? <a className="underline underline-offset-4" href={`${apiUrl}/auth/start?rd=/`}>Sign in with GitHub</a>
              : <span className="text-stone-600">GitHub sign-in unavailable</span>}
          {identity.authenticated && identity.anonymous_has_data && dismissed ? <button className="text-xs underline underline-offset-4" disabled={busy} onClick={() => void importData(false)}>Import anonymous data</button> : null}
          <button className="editor-button editor-primary" onClick={onEdit} type="button">Edit sources &amp; profiles</button>
        </div>
      </div>
      <p className="mt-3 max-w-3xl text-xs leading-5 text-stone-600">{identity.authenticated
        ? "Your profiles, sources, shelf, and notes live on the server under your GitHub account."
        : "Your data lives on the server. This browser holds a secret access identifier. If you clear browser data, you lose anonymous access. Keep the identifier private."}</p>
      {identity.authenticated && identity.anonymous_has_data && !dismissed ? (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-amber-900/25 bg-amber-100 p-3 text-sm">
          <p>This browser also has anonymous data. Import it into this GitHub account?</p>
          <button className="editor-button" disabled={busy} onClick={() => void importData(false)}>Import anonymous data</button>
          <button className="underline" disabled={busy} onClick={() => {
            try { localStorage.setItem(marker, "1"); setDismissed(true); } catch { setMessage("Cannot store this preference in the browser."); }
          }}>Not now</button>
        </div>
      ) : null}
      {hasLegacy ? <button className="mt-3 text-xs font-semibold underline underline-offset-4" disabled={busy} onClick={() => void importData(true)}>Import old browser reading state</button> : null}
      {busy ? <p role="status" className="mt-2 text-sm">Import in progress. Keep this page open.</p> : null}
      {message ? <p role="status" className="mt-2 text-sm">{message}</p> : null}
    </header>
  );
}
