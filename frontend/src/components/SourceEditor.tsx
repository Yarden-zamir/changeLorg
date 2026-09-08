import { useEffect, useRef, useState } from "react";
import { api, errorMessage, type Plugin, type Profile, type Source, type SourceCreate, type TimeWindow } from "../lib/api";
import { formatDate, renderedHtml, safeUrl, type Change } from "../lib/changes";

type PreviewChange = Pick<Change, "title" | "url" | "summary" | "content" | "published_at" | "external_id">;
type Preview = { changes: PreviewChange[]; window: TimeWindow };
type Draft = { source: SourceCreate; include: string; exclude: string };

function stringConfig(source: SourceCreate, key: string) {
  const value = source.config[key];
  return typeof value === "string" ? value : "";
}

function toDraft(source: SourceCreate): Draft {
  function terms(key: string) {
    const value = source.config[key];
    if (typeof value === "string") return value;
    return Array.isArray(value) ? value.filter((term): term is string => typeof term === "string").join("\n") : "";
  }
  return { source: { name: source.name, plugin: source.plugin, config: { ...source.config }, enabled: source.enabled }, include: terms("include_any"), exclude: terms("exclude_any") };
}

export function SourceEditor({ owner, profiles, onChanged, onClose }: { owner: string; profiles: Profile[]; onChanged: () => Promise<void>; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const lock = useRef(false);
  const active = useRef(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [catalog, setCatalog] = useState<SourceCreate[]>([]);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [query, setQuery] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<Source | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [profileName, setProfileName] = useState("");
  const [managedProfile, setManagedProfile] = useState("");
  const [rename, setRename] = useState("");

  useEffect(() => {
    active.current = true;
    const element = dialog.current;
    element?.showModal();
    return () => { active.current = false; element?.close(); };
  }, []);

  useEffect(() => {
    if (!dirty && !busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);

  async function load() {
    const [loadedSources, loadedPlugins] = await Promise.all([
      api<Source[]>("/sources", { owner }), api<Plugin[]>("/plugins", { owner }),
    ]);
    if (!active.current) return;
    setSources(loadedSources);
    setPlugins(loadedPlugins);
    setReady(true);
  }

  useEffect(() => { void run("Load editor", load); }, []);

  useEffect(() => {
    const controller = new AbortController();
    setCatalogLoading(true);
    setCatalogError("");
    setCatalog([]);
    const timer = window.setTimeout(async () => {
      try {
        const result = await api<SourceCreate[]>(`/catalog?q=${encodeURIComponent(query.trim())}`, { owner, signal: controller.signal });
        if (!controller.signal.aborted) setCatalog(result);
      } catch (cause) {
        if (!controller.signal.aborted) setCatalogError(errorMessage(cause));
      } finally {
        if (!controller.signal.aborted) setCatalogLoading(false);
      }
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [owner, query, catalogRevision]);

  async function run(label: string, action: () => Promise<void>) {
    if (lock.current || !active.current) return;
    lock.current = true;
    setBusy(label);
    setError("");
    setMessage("");
    try { await action(); } catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { lock.current = false; if (active.current) setBusy(""); }
  }

  function canDiscard() {
    return !lock.current && (!dirty || window.confirm("Discard the unsaved source draft?"));
  }

  function close() {
    if (canDiscard()) onClose();
  }

  function choose(source: SourceCreate, saved: Source | null = null) {
    if (!canDiscard()) return;
    const profile = stringConfig(source, "profile");
    setDraft(toDraft({ ...source, config: { ...source.config, profile: profiles.some((item) => item.name === profile) ? profile : profiles[0]?.name ?? "" } }));
    setSelected(saved);
    setPreview(null);
    setDirty(saved === null);
    setError("");
    setMessage("");
  }

  function edit(patch: Partial<SourceCreate>, config?: Record<string, unknown>) {
    if (!draft) return;
    setDraft({ ...draft, source: { ...draft.source, ...patch, config: config ? { ...draft.source.config, ...config } : draft.source.config } });
    setDirty(true);
    setPreview(null);
    setMessage("");
  }

  function payload(): SourceCreate | null {
    if (!draft || !form.current?.reportValidity()) return null;
    if (!draft.source.name.trim() || !profiles.some((profile) => profile.name === stringConfig(draft.source, "profile"))) {
      setError("Enter a source name and select an existing profile.");
      return null;
    }
    if (!safeUrl(stringConfig(draft.source, "url"))) {
      setError("Enter an absolute HTTP or HTTPS source URL without credentials.");
      return null;
    }
    const config: Record<string, unknown> = { ...draft.source.config, url: stringConfig(draft.source, "url").trim() };
    if (draft.source.plugin === "rss-atom") {
      config.include_any = draft.include.split("\n").map((term) => term.trim()).filter(Boolean);
      config.exclude_any = draft.exclude.split("\n").map((term) => term.trim()).filter(Boolean);
      delete config.article_path_prefix;
      delete config.exclude_path_prefixes;
      delete config.title_suffixes;
      delete config.limit;
    } else {
      delete config.include_any;
      delete config.exclude_any;
      if (!config.article_path_prefix) delete config.article_path_prefix;
      if (config.limit === "") delete config.limit;
      else if (typeof config.limit === "string") config.limit = Number(config.limit);
    }
    if (!config.enrichment_profile) delete config.enrichment_profile;
    return { ...draft.source, name: draft.source.name.trim(), config };
  }

  async function sync() {
    if (!active.current) return;
    await load();
    if (active.current) await onChanged();
  }

  const needle = query.trim().toLowerCase();
  const matches = sources.filter((source) => [source.name, source.plugin, stringConfig(source, "url"), stringConfig(source, "profile")].some((value) => value.toLowerCase().includes(needle)));
  const supported = draft && ["rss-atom", "html-news"].includes(draft.source.plugin) && plugins.some((plugin) => plugin.key === draft.source.plugin);
  const enrichment = plugins.find((plugin) => plugin.key === draft?.source.plugin)?.config_schema.properties?.enrichment_profile?.enum
    ?.filter((name): name is string => typeof name === "string") ?? [];

  return (
    <dialog ref={dialog} className="source-editor" aria-labelledby="editor-title" onCancel={(event) => { event.preventDefault(); close(); }}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b-2 border-stone-950 pb-4">
        <div><p className="text-xs font-black uppercase tracking-[0.25em] text-stone-500">Edition workshop</p><h2 id="editor-title" className="font-serif text-3xl font-black">Sources &amp; profiles</h2></div>
        <button autoFocus className="editor-button" disabled={Boolean(busy)} onClick={close}>Close</button>
      </div>
      <p className="my-4 text-sm leading-6 text-stone-600">Search your sources or the built-in catalog. For another site, enter a custom URL and preview it before you save.</p>
      {busy ? <p role="status" className="editor-notice">{busy} in progress. External sources can take a little time. Keep this editor open.</p> : null}
      {error ? <p role="alert" className="editor-notice border-red-900/30 bg-red-50 text-red-950">{error}</p> : null}
      {message ? <p role="status" className="editor-notice">{message}</p> : null}
      {!ready && !busy ? <button className="editor-button" onClick={() => void run("Load editor", load)}>Retry editor load</button> : null}

      <fieldset disabled={Boolean(busy)} className="min-w-0 border-b border-stone-950/20 pb-5">
        <legend className="mb-3 font-serif text-xl font-black">1. Organize your profiles</legend>
        {profiles.length === 0 ? <p className="mb-3 text-sm">No profiles yet. Create your first profile, then add a source.</p> : null}
        <div className="grid gap-4 md:grid-cols-2">
          <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
            event.preventDefault();
            if (!profileName.trim()) return;
            void run("Create profile", async () => {
              const name = profileName.trim();
              await api("/profiles", { owner, method: "POST", body: { name } });
              setProfileName("");
              if (draft && !stringConfig(draft.source, "profile")) edit({}, { profile: name });
              setMessage("Profile created.");
              await sync();
            });
          }}>
            <label className="min-w-0 flex-1">New profile<input required maxLength={100} value={profileName} onChange={(event) => setProfileName(event.target.value)} placeholder="Research, work, games..." /></label>
            <button className="editor-button editor-primary" type="submit">Create profile</button>
          </form>
          {profiles.length > 0 ? <div className="grid gap-2">
            <label>Existing profile<select value={managedProfile} onChange={(event) => { setManagedProfile(event.target.value); setRename(event.target.value); }}><option value="">Select a profile</option>{profiles.map((profile) => <option key={profile.name}>{profile.name}</option>)}</select></label>
            {managedProfile ? <div className="flex flex-wrap items-end gap-2">
              <label className="min-w-0 flex-1">New name<input maxLength={100} value={rename} onChange={(event) => setRename(event.target.value)} /></label>
              <button className="editor-button" disabled={!rename.trim() || rename.trim() === managedProfile} onClick={() => void run("Rename profile", async () => {
                const name = rename.trim();
                await api(`/profiles/${encodeURIComponent(managedProfile)}`, { owner, method: "PATCH", body: { name } });
                if (draft && stringConfig(draft.source, "profile") === managedProfile) edit({}, { profile: name });
                if (selected && stringConfig(selected, "profile") === managedProfile) setSelected({ ...selected, config: { ...selected.config, profile: name } });
                setManagedProfile(name);
                setMessage("Profile renamed.");
                await sync();
              })}>Rename</button>
              <button className="editor-button text-red-900" onClick={() => {
                if (!window.confirm(`Delete profile "${managedProfile}" and all its sources, cached changes, shelf items, and notes? This cannot be undone.`)) return;
                void run("Delete profile", async () => {
                  await api(`/profiles/${encodeURIComponent(managedProfile)}`, { owner, method: "DELETE" });
                  if (draft && stringConfig(draft.source, "profile") === managedProfile) { setDraft(null); setSelected(null); setDirty(false); setPreview(null); }
                  setManagedProfile("");
                  setMessage("Profile and its data deleted.");
                  await sync();
                });
              }}>Delete</button>
            </div> : null}
          </div> : null}
        </div>
      </fieldset>

      <div className="mt-5 grid min-w-0 gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
        <section className="min-w-0">
          <h3 className="mb-3 font-serif text-xl font-black">2. Choose a source</h3>
          <label>Search sources &amp; catalog<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, URL, topic..." /></label>
          <p className="mt-2 text-xs text-stone-500">Built-in catalog only, not a global web search.</p>
          <button className="editor-button editor-primary my-4 w-full" disabled={Boolean(busy) || !ready || profiles.length === 0} onClick={() => choose({ name: "", plugin: "rss-atom", config: { url: "", profile: profiles[0]?.name ?? "" }, enabled: true })}>Add custom URL</button>
          <h4 className="editor-list-title">Your sources / {matches.length}</h4>
          <div className="editor-source-list">
            {matches.map((source) => <button key={source.id} className="editor-source" disabled={Boolean(busy)} aria-pressed={selected?.id === source.id} onClick={() => choose(source, source)}><strong>{source.name}</strong><span>{stringConfig(source, "profile")} / {source.enabled ? "Enabled" : "Disabled"}</span></button>)}
            {ready && matches.length === 0 ? <p className="py-3 text-sm text-stone-500">No matching sources.</p> : null}
          </div>
          <h4 className="editor-list-title mt-5">Catalog</h4>
          {catalogLoading ? <p role="status" className="py-3 text-sm">Search in progress...</p> : null}
          {catalogError ? <p role="alert" className="py-3 text-sm text-red-900">{catalogError} <button className="underline" onClick={() => setCatalogRevision((value) => value + 1)}>Retry</button></p> : null}
          <div className="editor-source-list">
            {catalog.map((source, index) => <button key={`${source.name}:${index}`} className="editor-source" disabled={Boolean(busy) || !ready || profiles.length === 0} onClick={() => choose(source)}><strong>{source.name}</strong><span>{source.plugin}</span></button>)}
            {!catalogLoading && !catalogError && catalog.length === 0 ? <p className="py-3 text-sm text-stone-500">No catalog matches. Try a custom URL.</p> : null}
          </div>
        </section>

        <section className="min-w-0">
          <h3 className="mb-3 font-serif text-xl font-black">3. Preview &amp; save</h3>
          {!draft ? <div className="rounded-2xl border border-dashed border-stone-950/25 p-6 text-sm leading-6">{profiles.length === 0 ? "Create a profile above to start your edition." : "Select one of your sources to edit it, choose a catalog entry, or add a custom URL."}</div> : <>
            <form ref={form} onSubmit={(event) => {
              event.preventDefault();
              const body = payload();
              if (!body || !preview || !supported) return;
              if (selected?.enabled && !body.enabled && !window.confirm("Disable this source? It will leave the feed and stop refreshes. Cached data stays on the server.")) return;
              void run("Save source", async () => {
                const saved = await api<Source>(selected ? `/sources/${selected.id}` : "/sources", { owner, method: selected ? "PATCH" : "POST", body });
                setSelected(saved);
                setDraft(toDraft(saved));
                setDirty(false);
                setMessage("Source saved. Use Fetch latest to fetch and cache changes now. Save does not fetch changes.");
                await sync();
              });
            }}>
              <fieldset disabled={Boolean(busy) || !ready} className="grid min-w-0 gap-4">
                <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-black uppercase tracking-widest text-stone-500">{selected ? "Edit source" : "New source draft"}{dirty ? " / Unsaved" : ""}</span>{selected ? <button type="button" className="editor-button" onClick={() => { setSelected(null); setDirty(true); setPreview(null); setMessage("Duplicate draft. Preview it, then save as a separate source."); }}>Duplicate draft</button> : null}</div>
                <label>Name<input required maxLength={200} value={draft.source.name} onChange={(event) => edit({ name: event.target.value })} /></label>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label>Profile<select required value={stringConfig(draft.source, "profile")} onChange={(event) => edit({}, { profile: event.target.value })}><option value="">Select a profile</option>{profiles.map((profile) => <option key={profile.name}>{profile.name}</option>)}</select></label>
                  <label>Plugin<select value={draft.source.plugin} onChange={(event) => edit({ plugin: event.target.value })}>{!supported ? <option value={draft.source.plugin}>{draft.source.plugin} (not editable)</option> : null}{plugins.filter((plugin) => ["rss-atom", "html-news"].includes(plugin.key)).map((plugin) => <option key={plugin.key} value={plugin.key}>{plugin.name}</option>)}</select></label>
                </div>
                {!supported ? <p role="alert" className="text-sm text-red-900">This editor supports RSS/Atom and HTML news only. Select a supported plugin to preview and save.</p> : null}
                <label>Source URL<input type="url" required maxLength={2000} value={stringConfig(draft.source, "url")} onChange={(event) => edit({}, { url: event.target.value })} placeholder="https://example.com/feed.xml" /></label>
                {draft.source.plugin === "rss-atom" ? <div className="grid gap-4 sm:grid-cols-2">
                  <label>Include any term<textarea rows={3} value={draft.include} onChange={(event) => { setDraft({ ...draft, include: event.target.value }); setDirty(true); setPreview(null); }} placeholder="One term per line" /></label>
                  <label>Exclude any term<textarea rows={3} value={draft.exclude} onChange={(event) => { setDraft({ ...draft, exclude: event.target.value }); setDirty(true); setPreview(null); }} placeholder="One term per line" /></label>
                </div> : draft.source.plugin === "html-news" ? <div className="grid gap-4 sm:grid-cols-2">
                  <label>Article path prefix<input required value={stringConfig(draft.source, "article_path_prefix")} onChange={(event) => edit({}, { article_path_prefix: event.target.value })} placeholder="/news/" /></label>
                  <label>Article limit<input type="number" min={1} max={100} step={1} value={typeof draft.source.config.limit === "number" || typeof draft.source.config.limit === "string" ? draft.source.config.limit : ""} onChange={(event) => edit({}, { limit: event.target.value })} placeholder="Plugin default" /></label>
                </div> : null}
                <label>Enrichment profile<select value={stringConfig(draft.source, "enrichment_profile")} onChange={(event) => edit({}, { enrichment_profile: event.target.value })}><option value="">Automatic / plugin default</option>{stringConfig(draft.source, "enrichment_profile") && !enrichment.includes(stringConfig(draft.source, "enrichment_profile")) ? <option value={stringConfig(draft.source, "enrichment_profile")}>{stringConfig(draft.source, "enrichment_profile")} (unavailable)</option> : null}{enrichment.map((name) => <option key={name}>{name}</option>)}</select></label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={draft.source.enabled} onChange={(event) => edit({ enabled: event.target.checked })} />Enabled</label>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="editor-button" disabled={!supported} onClick={() => {
                    const body = payload();
                    if (body) void run("Preview source", async () => { setPreview(null); setPreview(await api<Preview>("/sources/preview", { owner, method: "POST", body })); });
                  }}>{busy === "Preview source" ? "Preview in progress..." : "Preview last 30 days"}</button>
                  <button type="submit" className="editor-button editor-primary" disabled={!preview || !supported}>{busy === "Save source" ? "Save in progress..." : "Save to profile"}</button>
                </div>
                <p className="text-xs leading-5 text-stone-500">Preview shows up to 10 items and writes nothing. After a draft change, preview again before you save.</p>
              </fieldset>
            </form>
            {selected ? <div className="mt-5 flex flex-wrap gap-2 border-t border-stone-950/20 pt-4">
              <button className="editor-button" disabled={Boolean(busy) || dirty || !selected.enabled} onClick={() => void run("Fetch latest", async () => {
                const result = await api<{ changes: Change[]; errors: { source_id: number; source_name: string; plugin: string; message: string }[]; window: TimeWindow }>(`/sources/${selected.id}/refresh`, { owner, method: "POST" });
                setMessage(result.errors.length ? `Fetch completed with ${result.errors.length} source errors. Check the source URL or try later. ${result.changes.length} changes returned.` : `Fetch completed. ${result.changes.length} changes returned. Check the desk window to view them.`);
                if (active.current) await onChanged();
              })}>{busy === "Fetch latest" ? "Fetch in progress..." : "Fetch latest"}</button>
              <button className="editor-button" disabled={Boolean(busy) || dirty} onClick={() => {
                if (selected.enabled && !window.confirm("Disable this source? It will leave the feed and stop refreshes. Cached data stays on the server.")) return;
                void run(selected.enabled ? "Disable source" : "Enable source", async () => {
                  const saved = await api<Source>(`/sources/${selected.id}`, { owner, method: "PATCH", body: { enabled: !selected.enabled } });
                  setSelected(saved); setDraft(toDraft(saved)); setPreview(null); setDirty(false);
                  setMessage(saved.enabled ? "Source enabled. Use Fetch latest to refresh it now." : "Source disabled. Cached data stays on the server.");
                  await sync();
                });
              }}>{selected.enabled ? "Disable source" : "Enable source"}</button>
              <button className="editor-button text-red-900" disabled={Boolean(busy)} onClick={() => {
                if (!window.confirm(`Delete "${selected.name}" and all its cached changes, shelf items, and notes? This cannot be undone.`)) return;
                void run("Delete source", async () => {
                  await api(`/sources/${selected.id}`, { owner, method: "DELETE" });
                  setSelected(null); setDraft(null); setPreview(null); setDirty(false);
                  setMessage("Source and its data deleted.");
                  await sync();
                });
              }}>Delete source</button>
            </div> : null}
            {preview ? <section className="mt-5 border-t-2 border-stone-950 pt-4" aria-label="Source preview">
              <h4 className="font-serif text-xl font-black">Preview / {Math.min(preview.changes.length, 10)} items</h4>
              <p className="my-2 text-xs text-stone-500">{formatDate(preview.window.start)} to {formatDate(preview.window.end)}. No data saved.</p>
              {preview.changes.length === 0 ? <p className="editor-notice">No items in the last 30 days. Check the URL and filters. You can still save this source.</p> : null}
              <div className="grid gap-3">{preview.changes.slice(0, 10).map((change, index) => <article key={index} className="min-w-0 rounded-2xl border border-stone-950/20 bg-white/60 p-4">
                <p className="text-xs text-stone-500">{formatDate(change.published_at)}</p>
                <h5 className="mt-2 font-serif text-xl font-black">{safeUrl(change.url) ? <a href={safeUrl(change.url) ?? undefined} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">{change.title}</a> : change.title}</h5>
                <div className="changelorg-rendered mt-3 max-h-64 overflow-auto text-sm leading-6" dangerouslySetInnerHTML={{ __html: renderedHtml(change.content || change.summary) }} />
              </article>)}</div>
            </section> : null}
          </>}
        </section>
      </div>
    </dialog>
  );
}
