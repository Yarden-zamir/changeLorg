import { useEffect, useRef, useState } from "react";
import { api, errorMessage, type Plugin, type Profile, type Source, type SourceCreate, type TimeWindow } from "../lib/api";
import { formatDate, renderedHtml, safeUrl, type Change } from "../lib/changes";
import { discoveryUrl } from "../lib/sourceDiscovery";
import { draftSignature, fetchSignature, normalizedSource, stringConfig, toDraft, type Draft } from "../lib/sourceDraft";

type PreviewChange = Pick<Change, "title" | "url" | "summary" | "content" | "published_at" | "external_id" | "metadata">;
type Preview = { changes: PreviewChange[]; window: TimeWindow };
type Confirmation = {
  title: string;
  description: string;
  confirmLabel: "Discard changes" | "Disable source" | "Delete source" | "Delete profile";
  action: () => void;
  owner: string;
  returnFocus: HTMLElement | null;
};

export function SourceEditor({ owner, profiles: suppliedProfiles, onChanged, onClose, onViewSource }: { owner: string; profiles: Profile[]; onChanged: () => Promise<void>; onClose: () => void; onViewSource: (source: Source) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const confirmationDialog = useRef<HTMLDialogElement>(null);
  const cancelConfirmation = useRef<HTMLButtonElement>(null);
  const pendingConfirmation = useRef<Confirmation | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const newProfile = useRef<HTMLInputElement>(null);
  const candidate = useRef<SourceCreate | null>(null);
  const readRequest = useRef<AbortController | null>(null);
  const lock = useRef(false);
  const active = useRef(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [profiles, setProfiles] = useState(suppliedProfiles);
  const [catalog, setCatalog] = useState<SourceCreate[]>([]);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [query, setQuery] = useState("");
  const [discovery, setDiscovery] = useState<{ url: string; candidates: SourceCreate[] } | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [profilesOpen, setProfilesOpen] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState("");
  const [mutationPending, setMutationPending] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [syncError, setSyncError] = useState("");
  const [savedThisSession, setSavedThisSession] = useState(false);
  const [fetchState, setFetchState] = useState<"idle" | "complete" | "failed">("idle");
  const [selected, setSelected] = useState<Source | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [baseline, setBaseline] = useState<Draft | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [profileName, setProfileName] = useState("");
  const [managedProfile, setManagedProfile] = useState("");
  const [rename, setRename] = useState("");
  const dirty = Boolean(draft && baseline && draftSignature(draft) !== draftSignature(baseline));
  const needsSave = Boolean(draft && (!selected || dirty));
  const profileTextDirty = Boolean(profileName.trim() || (managedProfile && rename.trim() !== managedProfile));

  useEffect(() => { setProfiles(suppliedProfiles); }, [suppliedProfiles]);

  useEffect(() => {
    active.current = true;
    const element = dialog.current;
    element?.showModal();
    searchInput.current?.focus({ preventScroll: true });
    return () => { active.current = false; pendingConfirmation.current = null; readRequest.current?.abort(); confirmationDialog.current?.close(); element?.close(); };
  }, []);

  useEffect(() => {
    pendingConfirmation.current = null;
    setConfirmation(null);
  }, [owner]);

  useEffect(() => {
    const element = confirmationDialog.current;
    if (!confirmation || confirmation.owner !== owner) return;
    element?.showModal();
    cancelConfirmation.current?.focus({ preventScroll: true });
    return () => element?.close();
  }, [confirmation, owner]);

  useEffect(() => {
    if (!dirty && !profileTextDirty && !mutationPending) return;
    // Browsers own unload prompts. The custom dialog only covers actions inside the editor.
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, profileTextDirty, mutationPending]);

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
    if (discoveryUrl(query) || query.trim().length > 200) {
      setCatalogLoading(false);
      return () => controller.abort();
    }
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

  async function run(label: string, action: () => Promise<void>, mutates = false) {
    if (lock.current || pendingConfirmation.current || !active.current) return;
    lock.current = true;
    setBusy(label);
    setMutationPending(mutates);
    setError("");
    setMessage("");
    try { await action(); } catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { lock.current = false; if (active.current) { setBusy(""); setMutationPending(false); } }
  }

  function askConfirmation(value: Omit<Confirmation, "owner" | "returnFocus">) {
    if (lock.current || pendingConfirmation.current || !active.current) return;
    const next = { ...value, owner, returnFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null };
    pendingConfirmation.current = next;
    setConfirmation(next);
  }

  function finishConfirmation(confirmed: boolean) {
    const pending = pendingConfirmation.current;
    if (!pending || lock.current) return;
    pendingConfirmation.current = null;
    setConfirmation(null);
    confirmationDialog.current?.close();
    if (!active.current || pending.owner !== owner) return;
    if (confirmed) pending.action();
    else if (pending.returnFocus?.isConnected) pending.returnFocus.focus({ preventScroll: true });
  }

  function withDiscardGuard(action: () => void, leavesEditor = false) {
    if (lock.current || pendingConfirmation.current || !active.current) return;
    if (!dirty && !(leavesEditor && profileTextDirty)) { action(); return; }
    askConfirmation({
      title: "Discard unsaved changes?",
      description: (dirty ? `The source draft for "${draft?.source.name.trim() || "Untitled source"}" has unsaved changes. ` : "")
        + (leavesEditor && profileTextDirty ? "Unsubmitted profile names will also be discarded. " : "")
        + "Saved sources and profiles that you created stay on the server.",
      confirmLabel: "Discard changes",
      action,
    });
  }

  function close() {
    withDiscardGuard(onClose, true);
  }

  function selectDraft(source: SourceCreate, saved: Source | null = null) {
    const profile = stringConfig(source, "profile");
    const next = toDraft({ ...source, config: { ...source.config, profile: saved && profiles.some((item) => item.name === profile) ? profile : "" } });
    setDraft(next);
    setBaseline(next);
    setSelected(saved);
    candidate.current = saved ? null : source;
    setPreview(null);
    setAdvanced(source.plugin === "html-news");
    setFiltersOpen(Boolean(next.include.trim() || next.exclude.trim()));
    setSavedThisSession(false);
    setFetchState("idle");
    setCreateOpen(false);
    setError("");
    setMessage("");
    return next;
  }

  function choose(source: SourceCreate, saved: Source | null = null, autoPreview = false) {
    if (saved ? selected?.id === saved.id : !selected && candidate.current && draftSignature(toDraft(candidate.current)) === draftSignature(toDraft(source))) return;
    withDiscardGuard(() => {
      const next = selectDraft(source, saved);
      if (autoPreview) void run("Preview source", () => previewDraft(next));
    });
  }

  async function previewDraft(value: Draft) {
    setPreview(null);
    const body = payload(false, value);
    if (!body) return;
    readRequest.current?.abort();
    const controller = new AbortController();
    readRequest.current = controller;
    const result = await api<Preview>("/sources/preview", { owner, method: "POST", body, signal: controller.signal });
    if (active.current && !controller.signal.aborted) setPreview(result);
  }

  function discover() {
    const url = discoveryUrl(query);
    if (!url || !ready) return;
    withDiscardGuard(() => void run("Discover source", async () => {
      readRequest.current?.abort();
      const controller = new AbortController();
      readRequest.current = controller;
      setDiscovery(null);
      const candidates = await api<SourceCreate[]>("/sources/discover", { owner, method: "POST", body: { url }, signal: controller.signal });
      if (!active.current || controller.signal.aborted) return;
      setDiscovery({ url, candidates });
      setDraft(null);
      setSelected(null);
      setPreview(null);
      setBaseline(null);
      candidate.current = null;
      setSavedThisSession(false);
      setFetchState("idle");
      if (candidates.length === 1) await previewDraft(selectDraft(candidates[0]));
    }));
  }

  function editDraft(next: Draft) {
    if (!draft || lock.current || pendingConfirmation.current) return;
    if (fetchSignature(next) !== fetchSignature(draft)) setPreview(null);
    setDraft(next);
    setMessage("");
    setError("");
  }

  function edit(patch: Partial<SourceCreate>, config?: Record<string, unknown>) {
    if (!draft) return;
    editDraft({ ...draft, source: { ...draft.source, ...patch, config: config ? { ...draft.source.config, ...config } : draft.source.config } });
  }

  function payload(requireProfile = true, value = draft): SourceCreate | null {
    if (!value) return null;
    if (!value.source.name.trim() || value.source.name.trim().length > 200) {
      setError("Enter a source name with 1 to 200 characters.");
      return null;
    }
    if (requireProfile && !profiles.some((profile) => profile.name === stringConfig(value.source, "profile"))) {
      setError("Select an existing profile, or create one before you save.");
      return null;
    }
    if (!["rss-atom", "html-news", "x"].includes(value.source.plugin) || !plugins.some((plugin) => plugin.key === value.source.plugin)) {
      setError("Select a supported plugin to preview and save.");
      setAdvanced(true);
      return null;
    }
    if (!safeUrl(stringConfig(value.source, "url")) || stringConfig(value.source, "url").length > 2000) {
      setError("Enter an absolute HTTP or HTTPS source URL without credentials.");
      setAdvanced(true);
      return null;
    }
    const body = normalizedSource(value);
    const config = body.config;
    if (!requireProfile) delete config.profile;
    if (value.source.plugin === "html-news") {
      if (!config.article_path_prefix) {
        setError("Enter an article path prefix for the manual HTML news handler.");
        setAdvanced(true);
        return null;
      }
      if (config.limit !== undefined && (typeof config.limit !== "number" || !Number.isInteger(config.limit) || config.limit < 1 || config.limit > 100)) {
        setError("Enter an article limit from 1 to 100, or leave it empty for the plugin default.");
        setAdvanced(true);
        return null;
      }
    }
    return body;
  }

  async function sync() {
    if (!active.current) return;
    setMutationPending(false);
    setSyncError("");
    try {
      await load();
      if (active.current) await onChanged();
    } catch (cause) {
      if (active.current) setSyncError(`Could not sync the editor or feed. Completed saves remain saved. ${errorMessage(cause)}`);
    }
  }

  async function createProfile(select: boolean) {
    const name = profileName.trim();
    if (!name || name.length > 100) return;
    await api("/profiles", { owner, method: "POST", body: { name } });
    if (!active.current) return;
    setProfiles((values) => [...values.filter((value) => value.name !== name), { name, source_count: 0 }]);
    setProfileName("");
    setCreateOpen(false);
    if (select) setDraft((value) => value ? { ...value, source: { ...value.source, config: { ...value.source.config, profile: name } } } : value);
    setMessage(select ? "Profile created and selected." : "Profile created.");
    await sync();
  }

  async function fetchLatest(source: Source, justSaved = false) {
    if (!source.enabled || !active.current) return;
    setBusy("Fetch latest");
    setMessage(justSaved ? "Source saved. Fetch requested." : "");
    try {
      const result = await api<{ changes: Change[]; errors: { source_id: number; source_name: string; plugin: string; message: string }[]; window: TimeWindow }>(`/sources/${source.id}/refresh`, { owner, method: "POST" });
      if (!active.current) return;
      setFetchState(result.errors.length ? "failed" : "complete");
      if (result.errors.length) setError(`${justSaved ? "Saved; fetch failed" : "Fetch failed"}. ${result.changes.length} changes returned. Retry Fetch latest without another save.`);
      else setMessage(`${justSaved ? "Saved and fetched" : "Fetch completed"}. ${result.changes.length} changes returned.`);
    } catch (cause) {
      if (!active.current) return;
      setFetchState("failed");
      setError(`${justSaved ? "Saved; fetch failed" : "Fetch failed"}. ${errorMessage(cause)} Retry Fetch latest without another save.`);
    }
  }

  function save(fetch: boolean) {
    if (lock.current || !needsSave) return;
    const body = payload();
    if (!body || !preview || !supported) return;
    void run(fetch && body.enabled ? "Save and fetch" : "Save source", async () => {
      const saved = await api<Source>(selected ? `/sources/${selected.id}` : "/sources", { owner, method: selected ? "PATCH" : "POST", body });
      if (!active.current) return;
      setSelected(saved);
      setDraft(toDraft(saved));
      setBaseline(toDraft(saved));
      candidate.current = null;
      setSources((values) => [...values.filter((value) => value.id !== saved.id), saved]);
      setSavedThisSession(true);
      setFetchState("idle");
      setMessage(saved.enabled ? "Source saved. No fetch requested." : "Source saved as disabled. No fetch requested.");
      if (fetch && saved.enabled) await fetchLatest(saved, true);
      await sync();
    }, true);
  }

  function addAnother() {
    withDiscardGuard(() => {
      setSelected(null); setDraft(null); setPreview(null); setBaseline(null);
      candidate.current = null;
      setSavedThisSession(false); setFetchState("idle");
      setQuery(""); setDiscovery(null); setError(""); setMessage("");
      searchInput.current?.focus({ preventScroll: true });
      dialog.current?.scrollTo({ top: 0 });
    });
  }

  const needle = query.trim().toLowerCase();
  const link = discoveryUrl(query);
  const matches = sources.filter((source) => [source.name, source.plugin, stringConfig(source, "url"), stringConfig(source, "profile")].some((value) => value.toLowerCase().includes(needle)));
  const supported = draft && ["rss-atom", "html-news", "x"].includes(draft.source.plugin) && plugins.some((plugin) => plugin.key === draft.source.plugin);
  const enrichment = plugins.find((plugin) => plugin.key === draft?.source.plugin)?.config_schema.properties?.enrichment_profile?.enum
    ?.filter((name): name is string => typeof name === "string") ?? [];
  const saveBlocked = !supported ? "Select a supported plugin."
    : !draft?.source.name.trim() ? "Enter a source name."
    : draft.source.name.trim().length > 200 ? "Use a source name with at most 200 characters."
    : !needsSave ? (draft.source.enabled ? "No unsaved changes. Use Fetch latest to refresh this source." : "No unsaved changes. Enable this source before a fetch.")
    : !preview ? "Preview the current URL and fetch settings before you save."
    : !profiles.some((profile) => profile.name === stringConfig(draft.source, "profile")) ? "Select or create a profile to save."
    : "";
  const filterCounts = draft ? [draft.include, draft.exclude].map((value) => value.split("\n").filter((term) => term.trim()).length) : [0, 0];

  return (
    <dialog ref={dialog} className="source-editor" aria-labelledby="editor-title" onCancel={(event) => { event.preventDefault(); close(); }}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b-2 border-stone-950 pb-4">
        <div><p className="text-xs font-black uppercase tracking-[0.25em] text-stone-500">Edition workshop</p><h2 id="editor-title" className="font-serif text-3xl font-black">Sources &amp; profiles</h2></div>
        <button className="editor-button" disabled={Boolean(busy)} onClick={close}>Close</button>
      </div>
      <form id="source-discovery" className="my-5" onSubmit={(event) => { event.preventDefault(); discover(); }}>
        <label className="font-serif text-xl">Find a source<input ref={searchInput} type="search" maxLength={2000} value={query} readOnly={Boolean(busy)} onChange={(event) => { setQuery(event.target.value); setDiscovery(null); }} placeholder="Website, X, Bluesky, or GitHub link" aria-describedby="source-search-hint" /></label>
        <p id="source-search-hint" className="mt-2 text-sm leading-6 text-stone-600">Names search your saved sources and optional suggestions. Paste a link to discover feeds from any site, or use GitHub owner/repo.</p>
      </form>
      {busy ? <p role="status" className="editor-notice">{busy}...</p> : null}
      {error ? <p role="alert" className="editor-notice border-red-900/30 bg-red-50 text-red-950">{error}</p> : null}
      {message ? <p role="status" className="editor-notice">{message}</p> : null}
      {syncError ? <p role="alert" className="editor-notice">{syncError} <button type="button" className="underline" disabled={Boolean(busy)} onClick={() => void run("Sync editor", sync)}>Retry sync only</button></p> : null}
      {!ready && !busy ? <button className="editor-button" onClick={() => void run("Load editor", load)}>Retry editor load</button> : null}
      <div className="mt-5 grid min-w-0 gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <section className="min-w-0" aria-label="Source results">
          {link ? <button type="submit" form="source-discovery" className="editor-button editor-primary mb-3 w-full break-all text-left" disabled={Boolean(busy) || !ready}>Discover {query.trim()}</button> : null}
          {discovery ? <div className="editor-source-list mb-5">
            {discovery.candidates.length > 1 ? <p role="status" className="py-3 text-sm">This site offers several feeds. Select one to preview.</p> : null}
            {discovery.candidates.map((source, index) => <button type="button" key={`${stringConfig(source, "url")}:${index}`} className="editor-source" disabled={Boolean(busy)} aria-pressed={!selected && candidate.current === source} onClick={() => choose(source, null, true)}><strong>{source.name}</strong><span>New candidate / {source.plugin === "rss-atom" ? "RSS/Atom" : source.plugin} / {stringConfig(source, "url")}</span></button>)}
            {discovery.candidates.length === 0 ? <>
              <p role="status" className="py-3 text-sm">No advertised RSS/Atom feed found. Manual HTML news needs an article path prefix and does not work on every site.</p>
              <button type="button" className="editor-source" disabled={Boolean(busy)} onClick={() => choose({ name: new URL(discovery.url).hostname, plugin: "html-news", config: { url: discovery.url, article_path_prefix: "" }, enabled: true })}><strong>Use manual HTML news</strong><span>{discovery.url} / Advanced fallback</span></button>
            </> : null}
          </div> : null}
          <h3 className="editor-list-title">Your sources / {matches.length}</h3>
          <div className="editor-source-list">
            {matches.map((source) => <button type="button" key={source.id} className="editor-source" disabled={Boolean(busy)} aria-pressed={selected?.id === source.id} onClick={() => choose(source, source)}><strong>{source.name}</strong><span>{stringConfig(source, "profile")} / {source.enabled ? "Enabled" : "Disabled"}</span></button>)}
            {ready && matches.length === 0 ? <p className="py-3 text-sm text-stone-500">No matching saved sources.</p> : null}
          </div>
          {!link ? <>
            <h3 className="editor-list-title mt-5">Suggestions</h3>
            {catalogLoading ? <p role="status" className="py-3 text-sm">Search in progress...</p> : null}
            {catalogError ? <p role="alert" className="py-3 text-sm">Suggestions are unavailable. Link discovery still works. <button type="button" className="underline" onClick={() => setCatalogRevision((value) => value + 1)}>Retry suggestions</button></p> : null}
            <div className="editor-source-list">
              {catalog.map((source, index) => <button type="button" key={`${source.name}:${index}`} className="editor-source" disabled={Boolean(busy) || !ready} onClick={() => choose(source, null, source.plugin === "rss-atom")}><strong>{source.name}</strong><span>{source.plugin === "rss-atom" ? "RSS/Atom" : source.plugin}</span></button>)}
              {!catalogLoading && !catalogError && catalog.length === 0 ? <p className="py-3 text-sm text-stone-500">No suggestions. Paste a website link or GitHub owner/repo to discover a feed.</p> : null}
            </div>
          </> : null}
        </section>
        <section className="min-w-0" aria-label="Source draft">
          {draft ? <>
            <h3 className="mb-3 font-serif text-xl font-black">Preview &amp; save</h3>
            <form id="source-draft" noValidate onSubmit={(event) => { event.preventDefault(); save(true); }}>
              <fieldset disabled={Boolean(busy) || !ready} className="grid min-w-0 gap-4">
                <p className="text-xs font-black uppercase tracking-widest text-stone-500">{selected ? `Current subscription / ${selected.enabled ? "Enabled" : "Disabled"}` : "New candidate"}{needsSave ? " / Unsaved" : ""}</p>
                <p className="break-all text-sm text-stone-600">{draft.source.plugin === "rss-atom" ? "RSS/Atom" : draft.source.plugin === "html-news" ? "Manual HTML news" : draft.source.plugin === "x" ? "X posts" : draft.source.plugin} / {stringConfig(draft.source, "url")}</p>
                <label>Name<input required maxLength={200} value={draft.source.name} onChange={(event) => edit({ name: event.target.value })} /></label>
                <div className="flex flex-wrap items-end gap-2">
                  <label className="min-w-0 flex-1">Save in profile<select value={stringConfig(draft.source, "profile")} onChange={(event) => edit({}, { profile: event.target.value })}><option value="">Select a profile to save</option>{profiles.map((profile) => <option key={profile.name}>{profile.name}</option>)}</select></label>
                  <button type="button" className="editor-button" aria-expanded={createOpen} aria-controls="inline-profile" onClick={() => { setCreateOpen(!createOpen); if (!createOpen) window.requestAnimationFrame(() => newProfile.current?.focus()); }}>New profile</button>
                </div>
                {createOpen ? <div id="inline-profile" className="flex flex-wrap items-end gap-2">
                  <label className="min-w-0 flex-1">New profile name<input ref={newProfile} maxLength={100} value={profileName} onChange={(event) => setProfileName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void run("Create profile", () => createProfile(true), true); } }} placeholder="Research, work, games..." /></label>
                  <button type="button" className="editor-button" disabled={!profileName.trim()} onClick={() => void run("Create profile", () => createProfile(true), true)}>Create and select</button>
                </div> : null}
                {profiles.length === 0 ? <p className="text-sm text-stone-600">Preview needs no profile. Create a profile when you want to save.</p> : null}
                {!selected ? <label className="flex items-center gap-2"><input type="checkbox" checked={draft.source.enabled} onChange={(event) => edit({ enabled: event.target.checked })} />Enabled</label> : null}
                {!supported ? <p role="alert" className="text-sm text-red-900">Select RSS/Atom, HTML news, or X posts to preview and save.</p> : null}
                {draft.source.plugin === "x" ? <p className="text-sm text-stone-600">Keyless public timeline, one card per post. X can limit or block this feed; it may not include every recent post.</p> : null}
                {preview ? <section className="editor-preview border-t-2 border-stone-950 pt-4" aria-label="Source preview">
                  <h4 className="font-serif text-xl font-black">Preview / {Math.min(preview.changes.length, 10)} items</h4>
                  {preview.changes.some((change) => change.metadata?.fallback_reason === "no_releases") ? <p className="mt-2 text-sm text-stone-600">This repository has no releases. Each item is one commit.</p> : null}
                  <p className="my-2 text-xs text-stone-500">{formatDate(preview.window.start)} to {formatDate(preview.window.end)}. Preview writes nothing.</p>
                  {preview.changes.length === 0 ? <p className="editor-notice">No items in the last 30 days. Check the URL and filters. You can still save this source.</p> : null}
                  <div className="editor-preview-items grid gap-3">{preview.changes.slice(0, 10).map((change, index) => <article key={index} className="min-w-0 rounded-2xl border border-stone-950/20 bg-white/60 p-4">
                    <p className="text-xs text-stone-500">{formatDate(change.published_at)}</p>
                    <h5 className="mt-2 font-serif text-xl font-black">{safeUrl(change.url) ? <a href={safeUrl(change.url) ?? undefined} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">{change.title}</a> : change.title}</h5>
                    <div className="changelorg-rendered mt-3 max-h-64 overflow-auto text-sm leading-6" dangerouslySetInnerHTML={{ __html: renderedHtml(change.content || change.summary) }} />
                  </article>)}</div>
                </section> : <p className="editor-notice">No current preview. Preview this source before you save. No profile is required.</p>}
                {["rss-atom", "x"].includes(draft.source.plugin) ? <details open={filtersOpen} onToggle={(event) => setFiltersOpen(event.currentTarget.open)} className="border-t border-stone-950/20 pt-3">
                  <summary className="cursor-pointer text-sm font-bold">Filters / {filterCounts.some(Boolean) ? `${filterCounts[0]} include, ${filterCounts[1]} exclude` : "None"}</summary>
                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    <label>Include any term<textarea rows={3} value={draft.include} onChange={(event) => editDraft({ ...draft, include: event.target.value })} placeholder="One term per line" /></label>
                    <label>Exclude any term<textarea rows={3} value={draft.exclude} onChange={(event) => editDraft({ ...draft, exclude: event.target.value })} placeholder="One term per line" /></label>
                  </div>
                </details> : null}
                <details open={advanced} onToggle={(event) => setAdvanced(event.currentTarget.open)} className="min-w-0 border-y border-stone-950/20 py-3">
                  <summary className="cursor-pointer text-sm font-bold">Advanced / manual handler, URL &amp; enrichment</summary>
                  <div className="mt-4 grid min-w-0 gap-4">
                    <label>Plugin<select value={draft.source.plugin} onChange={(event) => edit({ plugin: event.target.value })}>{!supported ? <option value={draft.source.plugin}>{draft.source.plugin} (not editable)</option> : null}{plugins.filter((plugin) => ["rss-atom", "html-news", "x"].includes(plugin.key)).map((plugin) => <option key={plugin.key} value={plugin.key}>{plugin.name}</option>)}</select></label>
                    <label>Source URL<input type="url" required maxLength={2000} value={stringConfig(draft.source, "url")} onChange={(event) => edit({}, { url: event.target.value })} placeholder="https://example.com/feed.xml" /></label>
                    {draft.source.plugin === "html-news" ? <div className="grid gap-4 sm:grid-cols-2">
                      <label>Article path prefix<input required value={stringConfig(draft.source, "article_path_prefix")} onChange={(event) => edit({}, { article_path_prefix: event.target.value })} placeholder="/news/" /></label>
                      <label>Article limit<input type="number" min={1} max={100} step={1} value={typeof draft.source.config.limit === "number" || typeof draft.source.config.limit === "string" ? draft.source.config.limit : ""} onChange={(event) => edit({}, { limit: event.target.value })} placeholder="Plugin default" /></label>
                    </div> : null}
                    {draft.source.plugin !== "x" ? <label>Enrichment profile<select value={stringConfig(draft.source, "enrichment_profile")} onChange={(event) => edit({}, { enrichment_profile: event.target.value })}><option value="">Automatic / plugin default</option>{stringConfig(draft.source, "enrichment_profile") && !enrichment.includes(stringConfig(draft.source, "enrichment_profile")) ? <option value={stringConfig(draft.source, "enrichment_profile")}>{stringConfig(draft.source, "enrichment_profile")} (unavailable)</option> : null}{enrichment.map((name) => <option key={name}>{name}</option>)}</select></label> : null}
                  </div>
                </details>
                <p className="text-xs leading-5 text-stone-500">Preview shows up to 10 items. Only fetch settings require another preview; name, profile, and enabled state do not.</p>
              </fieldset>
            </form>
            {selected ? <div className="mt-5 flex flex-wrap gap-2 border-t border-stone-950/20 pt-4">
              <button className="editor-button" disabled={Boolean(busy) || dirty} onClick={() => {
                const toggle = () => void run(selected.enabled ? "Disable source" : "Enable source", async () => {
                  const saved = await api<Source>(`/sources/${selected.id}`, { owner, method: "PATCH", body: { enabled: !selected.enabled } });
                  if (!active.current) return;
                  if (fetchSignature(toDraft(saved)) !== fetchSignature(draft)) setPreview(null);
                  setSelected(saved); setDraft(toDraft(saved)); setBaseline(toDraft(saved));
                  setSources((values) => values.map((value) => value.id === saved.id ? saved : value));
                  setMessage(saved.enabled ? "Source enabled. Use Fetch latest to refresh it now." : "Source disabled. Cached data stays on the server.");
                  await sync();
                }, true);
                if (selected.enabled) askConfirmation({ title: "Disable source?", description: `"${selected.name}" will leave the feed and stop refreshes. Its cached changes, shelf items, and notes stay on the server.`, confirmLabel: "Disable source", action: toggle });
                else toggle();
              }}>{selected.enabled ? "Disable source" : "Enable source"}</button>
              <button type="button" className="editor-button" disabled={Boolean(busy)} onClick={() => { if (lock.current || pendingConfirmation.current) return; setSelected(null); candidate.current = null; setSavedThisSession(false); setFetchState("idle"); setMessage("Duplicate draft. Your edits stay in this draft. Save as a separate subscription; the original stays unchanged."); }}>Duplicate draft</button>
              <button className="editor-button text-red-900" disabled={Boolean(busy)} onClick={() => askConfirmation({
                title: "Delete source?",
                description: `Delete "${selected.name}" and all its cached changes, shelf items, and notes. This cannot be undone.${dirty ? " Unsaved edits to this source draft will also be discarded." : ""}`,
                confirmLabel: "Delete source",
                action: () => void run("Delete source", async () => {
                  await api(`/sources/${selected.id}`, { owner, method: "DELETE" });
                  if (!active.current) return;
                  setSources((values) => values.filter((value) => value.id !== selected.id));
                  setSelected(null); setDraft(null); setPreview(null); setBaseline(null); setSavedThisSession(false); setFetchState("idle");
                  setMessage("Source and its data deleted.");
                  await sync();
                }, true),
              })}>Delete source</button>
            </div> : null}
          </> : <div className="rounded-2xl border border-dashed border-stone-950/25 p-5 text-sm leading-6">
            <p className="font-serif text-xl font-black">Follow a link, not a preset.</p>
            <p className="mt-2">GitHub sources use releases, or one card per commit when no releases exist. Website links reveal advertised RSS/Atom feeds.</p>
            <p className="mt-2 text-stone-600">Discovery and preview save nothing. Select or create a profile when you are ready to save.</p>
          </div>}
        </section>
      </div>
      <details id="source-profiles" open={profilesOpen} onToggle={(event) => setProfilesOpen(event.currentTarget.open)} className="mt-5 border-t border-stone-950/20 pt-4">
        <summary className="cursor-pointer font-serif text-xl font-black">Manage profiles</summary>
        <fieldset disabled={Boolean(busy)} className="mt-4 min-w-0">
          {profiles.length === 0 ? <p className="mb-3 text-sm">No profiles yet. Discovery and preview work without one. Create a profile here when you want to save.</p> : null}
          <div className="grid gap-4 md:grid-cols-2">
            <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
              event.preventDefault();
              void run("Create profile", () => createProfile(false), true);
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
                  if (!active.current) return;
                  setProfiles((values) => values.map((value) => value.name === managedProfile ? { ...value, name } : value));
                  setSources((values) => values.map((value) => stringConfig(value, "profile") === managedProfile ? { ...value, config: { ...value.config, profile: name } } : value));
                  setDraft((value) => value && stringConfig(value.source, "profile") === managedProfile ? { ...value, source: { ...value.source, config: { ...value.source.config, profile: name } } } : value);
                  setBaseline((value) => value && stringConfig(value.source, "profile") === managedProfile ? { ...value, source: { ...value.source, config: { ...value.source.config, profile: name } } } : value);
                  setSelected((value) => value && stringConfig(value, "profile") === managedProfile ? { ...value, config: { ...value.config, profile: name } } : value);
                  setManagedProfile(name);
                  setMessage("Profile renamed.");
                  await sync();
                }, true)}>Rename</button>
                <button className="editor-button text-red-900" onClick={() => askConfirmation({
                  title: "Delete profile?",
                  description: `Delete profile "${managedProfile}" and all its sources, cached changes, shelf items, and notes. This cannot be undone. The current source draft stays in the editor.`,
                  confirmLabel: "Delete profile",
                  action: () => void run("Delete profile", async () => {
                    await api(`/profiles/${encodeURIComponent(managedProfile)}`, { owner, method: "DELETE" });
                    if (!active.current) return;
                    setProfiles((values) => values.filter((value) => value.name !== managedProfile));
                    setSources((values) => values.filter((value) => stringConfig(value, "profile") !== managedProfile));
                    if (selected && stringConfig(selected, "profile") === managedProfile) { setSelected(null); setSavedThisSession(false); setFetchState("idle"); }
                    setDraft((value) => value && stringConfig(value.source, "profile") === managedProfile ? { ...value, source: { ...value.source, config: { ...value.source.config, profile: "" } } } : value);
                    setBaseline((value) => value && stringConfig(value.source, "profile") === managedProfile ? { ...value, source: { ...value.source, config: { ...value.source.config, profile: "" } } } : value);
                    setManagedProfile("");
                    setMessage("Profile and its data deleted.");
                    await sync();
                  }, true),
                })}>Delete</button>
              </div> : null}
            </div> : null}
          </div>
        </fieldset>
      </details>
      {draft ? <footer className="editor-actions" aria-label="Source actions">
        <p className="mb-2 text-xs text-stone-600" role="status">{busy ? `${busy}...` : error || message || (draft.source.enabled ? "Save and fetch caches the last 30 days. Save only does not fetch." : "Disabled source: Save only. Enable it before a fetch.")}</p>
        {saveBlocked && !busy ? <p id="source-save-blocked" className="mb-2 text-xs text-stone-600">{saveBlocked}</p> : null}
        <div className="flex flex-wrap gap-2">
          <button type="submit" form="source-draft" className="editor-button editor-primary" aria-describedby={saveBlocked && !busy ? "source-save-blocked" : undefined} disabled={Boolean(busy) || !ready || Boolean(saveBlocked)}>{draft.source.enabled ? "Save and fetch" : "Save only (disabled)"}</button>
          {draft.source.enabled ? <button type="button" className="editor-button" aria-describedby={saveBlocked && !busy ? "source-save-blocked" : undefined} disabled={Boolean(busy) || !ready || Boolean(saveBlocked)} onClick={() => save(false)}>Save only</button> : null}
          <button type="button" className="editor-button" disabled={Boolean(busy) || !ready || !supported} onClick={() => void run("Preview source", () => previewDraft(draft))}>Preview last 30 days</button>
          {selected ? <button type="button" className="editor-button" disabled={Boolean(busy) || dirty || !selected.enabled} onClick={() => void run("Fetch latest", async () => { await fetchLatest(selected); await sync(); }, true)}>{fetchState === "failed" ? "Retry Fetch latest" : "Fetch latest"}</button> : null}
          {selected && fetchState === "complete" ? <button type="button" className="editor-button" disabled={Boolean(busy) || Boolean(syncError) || dirty || !selected.enabled} onClick={() => withDiscardGuard(() => onViewSource(selected), true)}>View source</button> : null}
          {savedThisSession ? <button type="button" className="editor-button" disabled={Boolean(busy)} onClick={addAnother}>Add another</button> : null}
        </div>
      </footer> : null}
      <dialog ref={confirmationDialog} className="editor-confirmation" aria-labelledby="editor-confirmation-title" aria-describedby="editor-confirmation-description"
        onCancel={(event) => { event.preventDefault(); event.stopPropagation(); finishConfirmation(false); }}
        onKeyDown={(event) => {
          if (event.key === "Escape") event.stopPropagation();
          if (event.key === "Enter" && event.repeat) event.preventDefault();
          if (event.key === "Tab") {
            const buttons = event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
            const first = buttons[0];
            const last = buttons[buttons.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
          }
        }}>
        {confirmation ? <>
          <p className="text-xs font-black uppercase tracking-[0.2em] text-stone-500">Edition workshop</p>
          <h3 id="editor-confirmation-title" className="mt-2 font-serif text-2xl font-black">{confirmation.title}</h3>
          <p id="editor-confirmation-description" className="my-4 text-sm leading-6">{confirmation.description}</p>
          <div className="flex flex-wrap justify-end gap-2">
            <button ref={cancelConfirmation} type="button" className="editor-button" disabled={Boolean(busy)} onClick={() => finishConfirmation(false)}>{confirmation.confirmLabel === "Discard changes" ? "Keep editing" : "Cancel"}</button>
            <button type="button" className="editor-button editor-danger" disabled={Boolean(busy)} onClick={() => finishConfirmation(true)}>{confirmation.confirmLabel}</button>
          </div>
        </> : null}
      </dialog>
    </dialog>
  );
}
