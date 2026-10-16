import { useEffect, useRef, useState } from "react";

import { ErrorNote } from "./ErrorNote";
import { api, ApiError, apiUrl, errorMessage, getIdentity, type Identity, type Profile, type SharedProfile, type ShareImportResult } from "../lib/api";

// Shared profile page: frontend/API.md "Shared Profile Page".
function shareError(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 404) return "This share link is unknown, or its owner stopped sharing the profile.";
  if (cause instanceof ApiError && cause.status === 409) return "The import would pass the limit of 100 sources or 30 profiles. Remove sources or profiles first.";
  return errorMessage(cause);
}

function host(config: Record<string, unknown>) {
  if (typeof config.url !== "string") return "";
  try { return new URL(config.url).host; } catch { return ""; }
}

export function SharePage({ token }: { token: string }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [shared, setShared] = useState<SharedProfile | null>(null);
  const [name, setName] = useState("");
  const [taken, setTaken] = useState<string[]>([]);
  const [result, setResult] = useState<ShareImportResult | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const lock = useRef(false);

  useEffect(() => {
    let active = true;
    (async () => {
      const me = await getIdentity();
      const [profile, mine] = await Promise.all([
        api<SharedProfile>(`/shares/${encodeURIComponent(token)}`, { owner: me.id }),
        api<Profile[]>("/profiles", { owner: me.id }),
      ]);
      if (!active) return;
      const names = mine.map((value) => value.name);
      setIdentity(me);
      setShared(profile);
      setTaken(names);
      setName(names.includes(profile.name) ? `${profile.name} (shared)` : profile.name);
    })().catch((cause: unknown) => { if (active) setError(shareError(cause)); });
    return () => { active = false; };
  }, [token]);

  async function importProfile() {
    const target = name.trim();
    if (lock.current || !identity || !target) return;
    lock.current = true;
    setPending(true);
    setError("");
    try {
      setResult(await api<ShareImportResult>(`/shares/${encodeURIComponent(token)}/import`, { owner: identity.id, method: "POST", body: { name: target } }));
    } catch (cause) {
      setError(shareError(cause));
    } finally {
      lock.current = false;
      setPending(false);
    }
  }

  const signIn = `${apiUrl}/auth/start?rd=${encodeURIComponent(window.location.pathname)}`;
  const owner = shared?.shared_by ? `${shared.shared_by}'s` : "A";

  return <div className="min-h-screen px-1 py-2 text-ink sm:px-5 sm:py-4 lg:px-8">
    <section className="mx-auto grid max-w-2xl gap-4 py-10">
      <h1 className="text-4xl font-extrabold">riffle<span className="text-accent">.</span></h1>
      <h2 className="text-2xl font-extrabold">Shared profile</h2>
      {!shared ? (error ? <ErrorNote message={error} /> : <p role="status">Load the shared profile...</p>) : <>
        <div className="grid gap-3 rounded border-2 border-line bg-surface p-5 shadow-hard">
          <p className="text-lg leading-7">{owner} <strong>{shared.name}</strong> profile follows {shared.sources.length} {shared.sources.length === 1 ? "source" : "sources"}. Import them to start your own feed with the same news.</p>
          <ul className="grid max-h-80 gap-1 overflow-y-auto border-y-2 border-raised py-2 text-sm">
            {shared.sources.map((source) => <li key={`${source.name}:${host(source.config)}`} className="flex flex-wrap justify-between gap-x-3">
              <span className="font-semibold">{source.name}</span><span className="font-mono text-xs text-muted">{host(source.config)}</span>
            </li>)}
          </ul>
          <p className="text-sm leading-6">The import copies these sources and their items from the last 30 days. It never copies the read marks, shelf, or notes of the person who shared.</p>
          {result ? <div role="status" className="grid gap-2">
            <p className="font-semibold">Imported into "{result.profile}": {result.sources} new {result.sources === 1 ? "source" : "sources"} and {result.changes} recent items.{result.skipped ? ` ${result.skipped} that you already follow there stayed as they were.` : ""}</p>
            <a className="editor-button w-fit" href={`/?profile=${encodeURIComponent(result.profile)}`}>Open your feed</a>
          </div> : <form className="grid gap-3" onSubmit={(event) => { event.preventDefault(); void importProfile(); }}>
            <label className="grid gap-2 font-semibold">Import into the profile
              <input className="rounded border-2 border-line bg-surface px-3 py-2 text-ink" maxLength={100} required value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            {taken.includes(name.trim()) ? <p className="text-sm text-muted">You already have this profile. The sources join it, and sources that it already has stay as they were.</p> : null}
            <button className="editor-button editor-send w-fit" type="submit" disabled={pending || !name.trim()}>{pending ? "Import..." : `Import ${shared.sources.length} ${shared.sources.length === 1 ? "source" : "sources"}`}</button>
          </form>}
          {identity && !identity.authenticated ? <p className="text-xs leading-5 text-muted">
            You are not signed in, so the import goes into this browser only.{" "}
            {identity.auth_enabled ? <>To keep the sources in an account, <a className="underline underline-offset-4" href={signIn}>sign in with GitHub</a> first.</> : null}
          </p> : null}
        </div>
        {error ? <ErrorNote message={error} /> : null}
      </>}
      <a className="w-fit text-sm underline underline-offset-4" href="/">Open your desk</a>
    </section>
  </div>;
}
