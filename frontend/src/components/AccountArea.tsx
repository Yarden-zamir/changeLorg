import { useEffect, useRef, useState } from "react";
import { api, apiUrl, errorMessage, type Identity } from "../lib/api";

const legacyKey = "changelorg:user-state:v1";

export function AccountArea({ identity, onReload, onEdit }: { identity: Identity; onReload: () => Promise<void>; onEdit: () => void }) {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (menu.current?.open && event.target instanceof Node && !menu.current.contains(event.target)) menu.current.open = false;
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, []);
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
    <header className="account-area relative z-30 mx-auto mb-4 max-w-7xl border-b border-stone-950/25 py-3">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="font-serif text-4xl font-black tracking-tight">changelorg<span className="text-amber-800">.</span></h1>
        </div>
        <details ref={menu} className="relative" onKeyDown={(event) => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}>
          <summary aria-label="GitHub account menu" className="flex h-11 w-11 cursor-pointer list-none items-center justify-center rounded-full border border-stone-950/20 bg-[#fffaf0] [&::-webkit-details-marker]:hidden"><svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor"><path d="M12 .5a12 12 0 0 0-3.79 23.39c.6.11.82-.26.82-.58v-2.23c-3.34.73-4.04-1.42-4.04-1.42-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.73.08-.73 1.21.09 1.84 1.24 1.84 1.24 1.07 1.83 2.81 1.3 3.49.99.11-.78.42-1.3.76-1.6-2.67-.3-5.47-1.34-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.13-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.25 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.6-2.8 5.63-5.48 5.93.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.58A12 12 0 0 0 12 .5Z" /></svg></summary>
          <div className="absolute right-0 top-13 grid w-[min(19rem,calc(100vw-2rem))] gap-3 rounded-2xl border border-stone-950/20 bg-[#fffaf0] p-4 text-sm shadow-xl">
          <span className="font-semibold">{identity.authenticated ? `GitHub / ${identity.login ?? "Account"}` : "Anonymous edition"}</span>
          {identity.authenticated ? <a className="underline underline-offset-4" href={`${apiUrl}/auth/sign_out?rd=/`}>Sign out</a>
            : identity.auth_enabled ? <a className="underline underline-offset-4" href={`${apiUrl}/auth/start?rd=/`}>Sign in with GitHub</a>
              : <span className="text-stone-600">GitHub sign-in unavailable</span>}
          {identity.authenticated && identity.anonymous_has_data ? <button className="min-h-11 text-left underline underline-offset-4" disabled={busy} onClick={() => void importData(false)}>Import anonymous data</button> : null}
          <button className="editor-button editor-primary" onClick={onEdit} type="button">Edit sources &amp; profiles</button>
      <p className="text-xs leading-5 text-stone-600">{identity.authenticated
        ? "Your profiles, sources, shelf, and notes live on the server under your GitHub account."
        : "Your data lives on the server. This browser holds a secret access identifier. If you clear browser data, you lose anonymous access. Keep the identifier private."}</p>
      {hasLegacy ? <button className="mt-3 text-xs font-semibold underline underline-offset-4" disabled={busy} onClick={() => void importData(true)}>Import old browser reading state</button> : null}
      {busy ? <p role="status" className="mt-2 text-sm">Import in progress. Keep this page open.</p> : null}
      {message ? <p role="status" className="mt-2 text-sm">{message}</p> : null}
          </div>
        </details>
      </div>
    </header>
  );
}
