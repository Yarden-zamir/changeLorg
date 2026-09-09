import { BookmarkCheck, CalendarDays, Inbox, LibraryBig, Newspaper, Undo2 } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { ChangeCard } from "./components/ChangeCard";
import { AccountArea } from "./components/AccountArea";
import { SourceEditor } from "./components/SourceEditor";
import { Badge } from "./components/ui/badge";
import { anonymousTokenKey, api, ApiError, errorMessage, getIdentity, identityInvalidatedEvent, invalidateIdentity, resetSession, type Identity, type Profile } from "./lib/api";
import { changeKey, dateValue, openChange, type CardLocation, type Change, type ViewChange } from "./lib/changes";

type UserChangeState = {
  dismissed?: boolean;
  saved?: boolean;
  note?: string;
};
type SortKey = "newest" | "oldest" | "source";
type FeedWindow = "24h" | "7d" | "30d" | "90d" | "365d";
type ProfileName = string;
type SourceFilter = { id: number; name: string } | null;
type UrlState = {
  profile: ProfileName;
  feedWindow: FeedWindow;
  sort: SortKey;
  sourceId: number | null;
  sourceName: string | null;
};
/** The last state change, kept so one tap can undo a mis-swipe. */
type LastAction = {
  changeKey: string;
  id: number;
  label: string;
  previous: UserChangeState;
};
/** Where the viewport must land after a card leaves a queue. */
type ScrollAnchor = {
  nextKey: string | null;
  top: number;
};

const undoTimeoutMs = 8000;
const anchorTopMin = 16;
const defaultUrlState: UrlState = {
  profile: "",
  feedWindow: "7d",
  sort: "newest",
  sourceId: null,
  sourceName: null,
};

const feedWindows: Array<{ value: FeedWindow; label: string; caption: string }> = [
  { value: "24h", label: "24 hours", caption: "Today" },
  { value: "7d", label: "7 days", caption: "Weekly desk" },
  { value: "30d", label: "30 days", caption: "Monthly sweep" },
  { value: "90d", label: "90 days", caption: "Quarter file" },
  { value: "365d", label: "1 year", caption: "Archive" },
];

const sortOptions: Array<{ value: SortKey; label: string }> = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "source", label: "Group by source" },
];

async function fetchChanges(owner: string, feedWindow: FeedWindow, profile: ProfileName, sourceId: number | null, signal: AbortSignal): Promise<Change[]> {
  const query = new URLSearchParams({ since: feedWindow, limit: "200", include_dismissed: "true", profile });
  if (sourceId !== null) query.set("source_id", String(sourceId));
  return api<Change[]>(`/changes?${query}`, { owner, signal });
}

function isFeedWindow(value: string | null): value is FeedWindow {
  return feedWindows.some((item) => item.value === value);
}

function isSortKey(value: string | null): value is SortKey {
  return sortOptions.some((item) => item.value === value);
}

function parseSourceId(value: string | null): number | null {
  if (!value) {
    return null;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function readUrlState(): UrlState {
  const params = new URLSearchParams(window.location.search);
  const sinceParam = params.get("since");
  const sortParam = params.get("sort");
  const profile = params.get("profile")?.trim() || defaultUrlState.profile;
  const feedWindow = isFeedWindow(sinceParam) ? sinceParam : defaultUrlState.feedWindow;
  const sort = isSortKey(sortParam) ? sortParam : defaultUrlState.sort;
  const sourceId = parseSourceId(params.get("source"));
  const sourceName = params.get("sourceName")?.trim() || null;

  return {
    profile,
    feedWindow,
    sort,
    sourceId,
    sourceName: sourceId ? sourceName : null,
  };
}

function urlForState(state: UrlState) {
  const params = new URLSearchParams();
  if (state.profile) params.set("profile", state.profile);
  params.set("since", state.feedWindow);
  params.set("sort", state.sort);
  if (state.sourceId !== null) {
    params.set("source", String(state.sourceId));
    if (state.sourceName) {
      params.set("sourceName", state.sourceName);
    }
  }

  return `${window.location.pathname}?${params.toString()}${window.location.hash}`;
}

function profileLabel(profile: ProfileName) {
  if (profile === "dev") {
    return "Dev";
  }
  if (profile === "games") {
    return "Games";
  }
  return profile;
}

function compareDate(left: string, right: string) {
  return dateValue(left) - dateValue(right);
}

function windowLabel(value: FeedWindow) {
  return feedWindows.find((item) => item.value === value)?.label ?? value;
}

function sortedChanges<T extends Change>(changes: T[], sort: SortKey) {
  const copy = [...changes];
  if (sort === "oldest") {
    return copy.sort((left, right) => compareDate(left.published_at, right.published_at));
  }
  if (sort === "source") {
    return copy.sort((left, right) => {
      const bySource = left.source_name.localeCompare(right.source_name);
      return bySource || compareDate(right.published_at, left.published_at);
    });
  }
  return copy.sort((left, right) => compareDate(right.published_at, left.published_at));
}

function cardElement(key: string | null): HTMLElement | null {
  if (key === null) {
    return null;
  }
  const escaped = typeof CSS !== "undefined" && "escape" in CSS ? CSS.escape(key) : key;
  return document.querySelector<HTMLElement>(`[data-change-key="${escaped}"]`);
}

/** Viewport top of an element as laid out, ignoring its own transform. A swiped card is mid-flight when measured. */
function layoutTop(element: HTMLElement | null) {
  if (!element) {
    return anchorTopMin;
  }
  const parent = element.offsetParent;
  return parent instanceof HTMLElement ? parent.getBoundingClientRect().top + element.offsetTop : element.getBoundingClientRect().top;
}

function isTypingTarget(target: EventTarget | null) {
  return target instanceof HTMLElement && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
}

export default function App() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const currentIdentity = useRef<Identity | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState("");
  const [resetPending, setResetPending] = useState(false);
  const [resetError, setResetError] = useState("");
  const resetLock = useRef(false);
  const [revision, setRevision] = useState(0);
  const [editorOpen, setEditorOpen] = useState(false);
  const request = useRef(0);
  const profileRequest = useRef(0);
  const refreshProfiles = useRef(true);

  async function reload(force = true) {
    if (resetLock.current) return;
    if (force) refreshProfiles.current = true;
    const ticket = ++request.current;
    let profileTicket: number | null = null;
    try {
      const next = await getIdentity();
      if (ticket !== request.current) return;
      const changed = currentIdentity.current?.id !== next.id || currentIdentity.current?.authenticated !== next.authenticated;
      if (changed) {
        currentIdentity.current = null;
        setIdentity(null);
        setProfiles([]);
        setEditorOpen(false);
      }
      if (changed || refreshProfiles.current) {
        profileTicket = ++profileRequest.current;
        const loaded = await api<Profile[]>("/profiles", { owner: next.id });
        if (ticket !== request.current) return;
        if (profileTicket === profileRequest.current) {
          refreshProfiles.current = false;
          setProfiles(loaded);
          setRevision((value) => value + 1);
        }
      }
      currentIdentity.current = next;
      setIdentity(next);
      setError("");
    } catch (cause) {
      if (ticket !== request.current) return;
      if (profileTicket !== null && profileTicket !== profileRequest.current) return;
      profileRequest.current++;
      invalidateIdentity();
      currentIdentity.current = null;
      setIdentity(null);
      setProfiles([]);
      setEditorOpen(false);
      setError(errorMessage(cause));
    }
  }

  async function continueAnonymously() {
    if (resetLock.current) return;
    resetLock.current = true;
    request.current++;
    profileRequest.current++;
    setResetPending(true);
    setResetError("");
    try {
      await resetSession();
      invalidateIdentity();
      resetLock.current = false;
      await reload();
    } catch (cause) {
      setResetError(errorMessage(cause));
    } finally {
      resetLock.current = false;
      setResetPending(false);
    }
  }

  useEffect(() => {
    void reload();
    const check = () => { if (document.visibilityState === "visible") void reload(false); };
    const reset = () => {
      profileRequest.current++;
      invalidateIdentity();
      currentIdentity.current = null;
      setIdentity(null);
      setProfiles([]);
      setEditorOpen(false);
      void reload();
    };
    const storage = (event: StorageEvent) => {
      if (event.key === anonymousTokenKey || event.key === null) {
        reset();
      }
    };
    window.addEventListener("focus", check);
    window.addEventListener("pageshow", check);
    document.addEventListener("visibilitychange", check);
    window.addEventListener("storage", storage);
    window.addEventListener(identityInvalidatedEvent, reset);
    return () => {
      request.current++;
      profileRequest.current++;
      window.removeEventListener("focus", check);
      window.removeEventListener("pageshow", check);
      document.removeEventListener("visibilitychange", check);
      window.removeEventListener("storage", storage);
      window.removeEventListener(identityInvalidatedEvent, reset);
      invalidateIdentity();
    };
  }, []);

  return <div className="min-h-screen bg-[#ece7db] px-3 py-4 text-stone-950 sm:px-5 lg:px-8">
    {identity ? <>
      <AccountArea key={`${identity.id}:${identity.authenticated}`} identity={identity} onReload={() => reload()} onEdit={() => setEditorOpen(true)} />
      <Feed key={`${identity.id}:${identity.authenticated}`} owner={identity.id} profiles={profiles} revision={revision} onEdit={() => setEditorOpen(true)} />
      {editorOpen ? <SourceEditor key={identity.id} owner={identity.id} profiles={profiles} onChanged={async () => {
        const ticket = ++profileRequest.current;
        const loaded = await api<Profile[]>("/profiles", { owner: identity.id });
        if (ticket !== profileRequest.current) throw new ApiError("A newer profile refresh started. Retry sync to confirm the current list.");
        refreshProfiles.current = false;
        setProfiles(loaded);
        setRevision((value) => value + 1);
      }} onClose={() => setEditorOpen(false)} onViewSource={(source) => {
        if (currentIdentity.current?.id !== source.owner_id) return;
        window.history.pushState(null, "", urlForState({ ...readUrlState(), profile: source.config.profile, sourceId: source.id, sourceName: source.name }));
        window.dispatchEvent(new PopStateEvent("popstate"));
        setEditorOpen(false);
        window.scrollTo({ top: 0 });
      }} /> : null}
    </> : <section className="mx-auto max-w-3xl py-10">
      <h1 className="mb-5 font-serif text-4xl font-black">changelorg.</h1>
      {error ? <>
        <ErrorNote message={error} />
        <p className="mt-4 text-sm leading-6">Continue anonymously to clear an expired sign-in session. This works even when GitHub sign-in is unavailable. Your anonymous identifier and data stay intact.</p>
        <div className="my-4 flex flex-wrap items-center gap-3">
          <button className="editor-button editor-primary" disabled={resetPending} onClick={() => void continueAnonymously()}>Continue anonymously</button>
          <button className="editor-button" disabled={resetPending} onClick={() => void reload()}>Retry account access</button>
          <a className="text-sm underline underline-offset-4" href="/auth/start?rd=/" aria-disabled={resetPending} onClick={(event) => { if (resetPending) event.preventDefault(); }}>Sign in with GitHub</a>
        </div>
        <p className="mb-4 text-xs text-stone-600">GitHub sign-in requires authentication to be enabled on this server. Session recovery does not.</p>
        {resetPending ? <p role="status" className="mb-4 text-sm">Session reset in progress. Keep this page open.</p> : null}
        {resetError ? <ErrorNote message={resetError} /> : null}
      </> : <p role="status">Load your account and profiles...</p>}
    </section>}
  </div>;
}

function Feed({ owner, profiles, revision, onEdit }: { owner: string; profiles: Profile[]; revision: number; onEdit: () => void }) {
  const [changes, setChanges] = useState<Change[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [urlState, setUrlState] = useState<UrlState>(readUrlState);
  const [openNotes, setOpenNotes] = useState<Record<string, boolean>>({});
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<LastAction | null>(null);
  const scrollAnchor = useRef<ScrollAnchor | null>(null);
  const scrollOnFocus = useRef(false);
  const shortcutDialog = useRef<HTMLDialogElement>(null);
  const mutationLock = useRef(false);
  const needsReload = useRef(false);
  const [pending, setPending] = useState(false);
  const loadEpoch = useRef(0);
  const [retry, setRetry] = useState(0);

  const selectedProfile = profiles.some((profile) => profile.name === urlState.profile) ? urlState.profile : profiles[0]?.name ?? "";
  const feedWindow = urlState.feedWindow;
  const sort = urlState.sort;

  function updateUrlState(patch: Partial<UrlState>, mode: "push" | "replace" = "push") {
    const next = { ...readUrlState(), ...patch };
    window.history[mode === "push" ? "pushState" : "replaceState"](null, "", urlForState(next));
    setUrlState(next);
  }

  useEffect(() => {
    window.history.replaceState(null, "", urlForState(urlState));
    const onPopState = () => setUrlState(readUrlState());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (urlState.profile !== selectedProfile) updateUrlState({ profile: selectedProfile, sourceId: null, sourceName: null }, "replace");
  }, [selectedProfile, urlState.profile]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    loadEpoch.current++;

    setIsLoading(Boolean(selectedProfile));
    setError(null);
    setChanges([]);
    setLastAction(null);
    setFocusedKey(null);
    scrollAnchor.current = null;
    (selectedProfile ? fetchChanges(owner, feedWindow, selectedProfile, urlState.sourceId, controller.signal) : Promise.resolve([]))
      .then((loaded) => {
        if (cancelled) {
          return;
        }
        setChanges(loaded);
        needsReload.current = false;
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(errorMessage(cause));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });

    return () => {
      cancelled = true;
      controller.abort();
      loadEpoch.current++;
    };
  }, [owner, feedWindow, selectedProfile, urlState.sourceId, revision, retry]);

  useEffect(() => {
    if (!lastAction) {
      return;
    }
    const timer = window.setTimeout(() => setLastAction(null), undoTimeoutMs);
    return () => window.clearTimeout(timer);
  }, [lastAction]);

  const viewChanges: ViewChange[] = changes.map((change) => ({ ...change, change_key: changeKey(change) }));

  const inSourceFilter = (change: ViewChange) => urlState.sourceId === null || change.source_id === urlState.sourceId;
  const dismissedCount = viewChanges.filter((change) => change.dismissed).length;
  const shelfChanges = sortedChanges(
    viewChanges.filter((change) => change.saved && !change.dismissed && inSourceFilter(change)),
    sort,
  );
  const deskChanges = sortedChanges(
    viewChanges.filter((change) => !change.dismissed && !change.saved && inSourceFilter(change)),
    sort,
  );
  const sourceFilter: SourceFilter = urlState.sourceId
    ? {
        id: urlState.sourceId,
        name: urlState.sourceName || viewChanges.find((change) => change.source_id === urlState.sourceId)?.source_name || `Source ${urlState.sourceId}`,
      }
    : null;
  const sourceCount = profiles.find((profile) => profile.name === selectedProfile)?.source_count ?? 0;
  // Shelf first, then desk: the order cards appear on screen. Drives keyboard focus and scroll anchoring.
  const visibleChanges = [...shelfChanges, ...deskChanges];
  const visibleKeys = visibleChanges.map((change) => change.change_key);

  // After a card leaves, put the card that followed it where the removed card was.
  useLayoutEffect(() => {
    const anchor = scrollAnchor.current;
    if (!anchor) {
      return;
    }
    scrollAnchor.current = null;
    const nextElement = cardElement(anchor.nextKey);
    if (!nextElement) {
      return;
    }
    const targetTop = Math.max(anchor.top, anchorTopMin);
    window.scrollBy({ top: nextElement.getBoundingClientRect().top - targetTop });
  }, [changes]);

  // Move DOM focus to the focused card. Only keyboard navigation scrolls; a click or a removal keeps the viewport still.
  useEffect(() => {
    const element = cardElement(focusedKey);
    const scroll = scrollOnFocus.current;
    scrollOnFocus.current = false;
    if (!element || element.contains(document.activeElement) || isTypingTarget(document.activeElement)) {
      return;
    }
    element.focus({ preventScroll: true });
    if (scroll) {
      element.scrollIntoView({ block: "nearest" });
    }
  }, [focusedKey, visibleKeys.join("\n")]);

  // Serialize desk mutations. Revisit per-item locks only if concurrent card writes become necessary.
  async function updateChangeState(change: ViewChange, patch: UserChangeState, label: string | null, removes = false) {
    if (mutationLock.current || needsReload.current || isLoading) return false;
    mutationLock.current = true;
    setPending(true);
    setError(null);
    const epoch = loadEpoch.current;
    try {
      const updated = await api<Change>(`/changes/${change.id}`, { owner, method: "PATCH", body: patch });
      if (patch.note !== undefined) setNoteDrafts((current) => {
        const next = { ...current };
        delete next[change.change_key];
        return next;
      });
      if (epoch !== loadEpoch.current) {
        setRetry((value) => value + 1);
        return false;
      }
      if (removes) latestAnchor.current(change);
      setChanges((current) => current.map((item) => item.id === updated.id ? updated : item));
      setLastAction(label ? { id: change.id, changeKey: change.change_key, label, previous: { dismissed: change.dismissed, saved: change.saved, note: change.note } } : null);
      return true;
    } catch (cause) {
      if (epoch === loadEpoch.current) {
        needsReload.current = true;
        setError(errorMessage(cause));
      } else setRetry((value) => value + 1);
      return false;
    } finally {
      mutationLock.current = false;
      setPending(false);
    }
  }

  /** Record where `change` sits so the following card can take its place, and move focus to it. */
  function anchorAfterRemoval(change: ViewChange) {
    const index = visibleKeys.indexOf(change.change_key);
    if (index < 0) return;
    const nextKey = visibleKeys[index + 1] ?? visibleKeys[index - 1] ?? null;
    scrollAnchor.current = { nextKey, top: layoutTop(cardElement(change.change_key)) };
    if (focusedKey === change.change_key) {
      setFocusedKey(nextKey);
    }
  }
  const latestAnchor = useRef(anchorAfterRemoval);
  latestAnchor.current = anchorAfterRemoval;

  function onClear(change: ViewChange) {
    return updateChangeState(change, { dismissed: true }, change.saved ? "Marked read" : "Cleared from desk", true);
  }

  async function onToggleShelf(change: ViewChange) {
    const nextSaved = !change.saved;
    const success = await updateChangeState(change, nextSaved ? { saved: true } : { saved: false, note: "" }, nextSaved ? "Shelved" : "Back on the desk", true);
    if (success && !nextSaved) {
      setOpenNotes((current) => ({ ...current, [change.change_key]: false }));
    }
    return success;
  }

  async function onSaveNote(change: ViewChange) {
    const success = await updateChangeState(change, { saved: true, note: noteDrafts[change.change_key] ?? change.note }, change.saved ? null : "Shelved with note", !change.saved);
    if (success) {
      setOpenNotes((current) => ({ ...current, [change.change_key]: false }));
    }
  }

  function onToggleNote(change: ViewChange) {
    setOpenNotes((current) => ({ ...current, [change.change_key]: !(current[change.change_key] ?? false) }));
  }

  async function onUndo() {
    if (!lastAction) {
      return;
    }
    const { changeKey: key, previous, id } = lastAction;
    const change = viewChanges.find((item) => item.id === id);
    if (change && await updateChangeState(change, previous, null, !change.dismissed)) {
      scrollOnFocus.current = true;
      setFocusedKey(key);
    }
  }

  async function onRestoreCleared() {
    if (mutationLock.current || needsReload.current || isLoading || !dismissedCount) return;
    mutationLock.current = true;
    setPending(true);
    setError(null);
    const epoch = loadEpoch.current;
    try {
      const restored = await api<Change[]>("/changes/restore", { owner, method: "POST", body: { ids: changes.filter((change) => change.dismissed).map((change) => change.id) } });
      if (epoch !== loadEpoch.current) {
        setRetry((value) => value + 1);
        return;
      }
      const byId = new Map(restored.map((change) => [change.id, change]));
      setChanges((current) => current.map((change) => byId.get(change.id) ?? change));
      setLastAction(null);
    } catch (cause) {
      if (epoch === loadEpoch.current) {
        needsReload.current = true;
        setError(errorMessage(cause));
      } else setRetry((value) => value + 1);
    } finally {
      mutationLock.current = false;
      setPending(false);
    }
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) {
        return;
      }
      if (document.querySelector("dialog[open]") && !shortcutDialog.current?.open) return;
      if (event.key === "?") {
        event.preventDefault();
        if (!event.repeat) {
          if (shortcutDialog.current?.open) shortcutDialog.current.close();
          else shortcutDialog.current?.showModal();
        }
        return;
      }
      if (shortcutDialog.current?.open) return;
      if (event.key === "e") {
        event.preventDefault();
        if (!event.repeat) onEdit();
        return;
      }
      if (event.key === "Enter" && event.target instanceof HTMLElement && event.target.closest("button, a, summary")) return;
      if (event.repeat && !["j", "k", "ArrowDown", "ArrowUp"].includes(event.key)) return;
      const controlIds: Record<string, string> = { p: "profile-select", w: "window-select", t: "sort-select" };
      const controlId = controlIds[event.key];
      if (controlId) {
        event.preventDefault();
        document.getElementById(controlId)?.focus();
        return;
      }
      if (event.key === "r") {
        event.preventDefault();
        onRestoreCleared();
        return;
      }
      if (event.key === "a") {
        event.preventDefault();
        updateUrlState({ sourceId: null, sourceName: null });
        return;
      }
      if (event.key === "z" || event.key === "u") {
        onUndo();
        return;
      }
      const index = focusedKey ? visibleKeys.indexOf(focusedKey) : -1;
      if (event.key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        scrollOnFocus.current = true;
        setFocusedKey(visibleKeys[Math.min(index + 1, visibleKeys.length - 1)] ?? null);
        return;
      }
      if (event.key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        scrollOnFocus.current = true;
        setFocusedKey(visibleKeys[Math.max(index - 1, 0)] ?? null);
        return;
      }
      const change = index >= 0 ? visibleChanges[index] : undefined;
      if (!change) {
        return;
      }
      if (event.key === "x") {
        onClear(change);
      } else if (event.key === "s") {
        onToggleShelf(change);
      } else if (event.key === "o" || event.key === "Enter") {
        openChange(change);
      } else if (event.key === "n") {
        event.preventDefault();
        onToggleNote(change);
      } else if (event.key === "f") {
        event.preventDefault();
        updateUrlState({ sourceId: change.source_id, sourceName: change.source_name });
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const cardProps = (change: ViewChange, location: CardLocation) => ({
    change,
    pending: pending || needsReload.current,
    location,
    focused: focusedKey === change.change_key,
    noteDraft: noteDrafts[change.change_key] ?? change.note,
    noteOpen: openNotes[change.change_key] ?? false,
    onFocus: () => setFocusedKey(change.change_key),
    onClear: () => onClear(change),
    onToggleShelf: () => onToggleShelf(change),
    onSaveNote: () => onSaveNote(change),
    onSourceFilter: () => updateUrlState({ sourceId: change.source_id, sourceName: change.source_name }),
    onToggleNote: () => onToggleNote(change),
    onUpdateDraft: (value: string) => setNoteDrafts((current) => {
      const next = { ...current };
      if (value === change.note) delete next[change.change_key];
      else next[change.change_key] = value;
      return next;
    }),
  });

  return (
    <main className="overflow-x-hidden text-stone-950 [overflow-anchor:none]">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_top_left,rgba(120,53,15,0.16),transparent_32rem),linear-gradient(90deg,rgba(28,25,23,0.045)_1px,transparent_1px),linear-gradient(rgba(28,25,23,0.045)_1px,transparent_1px)] bg-[length:auto,44px_44px,44px_44px]" />

      <section className="relative mx-auto grid max-w-7xl gap-5 lg:grid-cols-[330px_minmax(0,1fr)]">
        <ControlPanel
          queueCount={deskChanges.length}
          dismissedCount={dismissedCount}
          pending={pending || isLoading || needsReload.current}
          feedWindow={feedWindow}
          onClearSourceFilter={() => updateUrlState({ sourceId: null, sourceName: null })}
          onFeedWindowChange={(nextFeedWindow) => updateUrlState({ feedWindow: nextFeedWindow })}
          onRestoreCleared={onRestoreCleared}
          onSortChange={(nextSort) => updateUrlState({ sort: nextSort })}
          onProfileChange={(profile) => {
            updateUrlState({ profile, sourceId: null, sourceName: null });
          }}
          profiles={profiles}
          savedCount={shelfChanges.length}
          selectedProfile={selectedProfile}
          sourceFilter={sourceFilter}
          sort={sort}
          sourceCount={sourceCount}
        />

        <div className="flex min-w-0 flex-col gap-5">
          {error ? <div><ErrorNote message={error} /><button className="editor-button mt-2" disabled={pending} onClick={() => setRetry((value) => value + 1)}>Reload desk from server</button></div> : null}
          {pending ? <p role="status" className="fixed bottom-4 right-4 z-20 rounded-xl border border-stone-950 bg-[#fffaf0] px-4 py-2 text-sm font-semibold shadow-lg">Save in progress...</p> : null}
          {!isLoading && sourceCount === 0 ? <section className="rounded-[2rem] border border-stone-950 bg-[#fffaf0] p-6">
            <h2 className="font-serif text-3xl font-black">Build your edition</h2>
            <p className="my-3 text-sm leading-6">{profiles.length === 0 ? "Paste a website or GitHub link to discover and preview a source. Create a profile when you want to save." : "This profile has no active sources. Add or enable a source, then fetch its latest changes."}</p>
            <button className="editor-button editor-primary" onClick={onEdit}>{profiles.length === 0 ? "Find your first source" : "Add or manage sources"}</button>
          </section> : null}

          <section data-queue="shelf" className="rounded-[2rem] border border-amber-900/30 bg-[#f9d978] p-4 text-stone-950 shadow-[8px_8px_0_rgba(120,53,15,0.18)] sm:p-5">
            <QueueHeader
              count={shelfChanges.length}
              icon={<BookmarkCheck className="h-4 w-4" />}
              swipeHint="Swipe left to mark read, right to open the source."
              title="The shelf"
              tone="shelf"
            >
              {shelfChanges.length === 0
                ? "Nothing shelved yet. Shelf a desk item and it waits here until you mark it read."
                : `${shelfChanges.length} item${shelfChanges.length === 1 ? "" : "s"} shelved for later. Mark read when done, or put one back on the desk.`}
            </QueueHeader>

            {shelfChanges.length === 0 ? null : (
              <div className="mt-4 grid gap-4">
                {shelfChanges.map((change) => (
                  <ChangeCard key={change.change_key} {...cardProps(change, "shelf")} />
                ))}
              </div>
            )}
          </section>

          <section data-queue="desk" className="rounded-[2rem] border border-stone-950/15 bg-[#fffdf7]/90 p-4 shadow-[0_18px_50px_rgba(41,37,36,0.12)] backdrop-blur sm:p-5">
            <QueueHeader count={deskChanges.length} icon={<Newspaper className="h-4 w-4" />} swipeHint="Swipe left to clear, right to shelf." title="The desk" tone="desk">
              {isLoading ? "Load your feed..." : error ? "The feed could not load. Retry above." : sourceCount === 0 ? "Add or enable a source to fill your desk."
                : deskChanges.length ? `${deskChanges.length} readable ${profileLabel(selectedProfile)} item${deskChanges.length === 1 ? "" : "s"} from the last ${windowLabel(feedWindow)}.`
                  : shelfChanges.length ? "Your current items are on the shelf."
                    : dismissedCount ? "Desk is clear. Restore cleared items from the panel or choose a wider window."
                      : "No unread items in this window. Try a wider window or another source."}
            </QueueHeader>

            {isLoading ? (
              <LoadingState />
            ) : deskChanges.length === 0 ? null : (
              <div className="mt-5 grid gap-4">
                {deskChanges.map((change) => (
                  <ChangeCard key={change.change_key} {...cardProps(change, "desk")} />
                ))}
              </div>
            )}
          </section>

          <button className="self-end rounded-xl border border-stone-950/20 bg-[#fffaf0] px-4 py-2 text-sm font-semibold" onClick={() => shortcutDialog.current?.showModal()} type="button">
            Keyboard shortcuts <kbd>?</kbd>
          </button>
        </div>
      </section>

      {lastAction ? <UndoToast label={lastAction.label} pending={pending || isLoading || needsReload.current} onUndo={onUndo} /> : null}
      <dialog ref={shortcutDialog} aria-labelledby="shortcut-title" className="fixed inset-0 m-auto max-h-[85dvh] w-[calc(100%_-_2rem)] max-w-lg overflow-y-auto rounded-[1.75rem] border border-stone-950 bg-[#fffaf0] p-6 text-stone-950 shadow-xl backdrop:bg-stone-950/50">
        <div className="mb-4 flex items-center justify-between gap-4">
          <h2 id="shortcut-title" className="font-serif text-2xl font-black">Keyboard shortcuts</h2>
          <button autoFocus className="rounded-xl border border-stone-950 px-3 py-2 text-sm font-bold" onClick={() => shortcutDialog.current?.close()} type="button">Close</button>
        </div>
        <p className="mb-4 text-sm text-stone-600">Card actions use the focused card. Press j to select the first card. Shortcuts stay inactive in form fields.</p>
        <KeyboardLegend />
      </dialog>
    </main>
  );
}

function QueueHeader({ children, count, icon, swipeHint, title, tone }: { children: ReactNode; count: number; icon: ReactNode; swipeHint: string; title: string; tone: "shelf" | "desk" }) {
  const muted = tone === "shelf" ? "text-amber-950/65" : "text-stone-500";
  const body = tone === "shelf" ? "text-amber-950/80" : "text-stone-600";
  return (
    <div className={`flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-end sm:justify-between ${tone === "shelf" ? "border-amber-950/20" : "border-stone-950/15"}`}>
      <div>
        <div className={`flex items-center gap-2 text-xs font-black uppercase tracking-[0.28em] ${muted}`}>
          {icon}
          {title}
          <Badge className={tone === "shelf" ? "border-amber-950/20 bg-amber-950 text-amber-50" : "border-stone-950/20 bg-stone-950 text-[#fff8e8]"}>{count}</Badge>
        </div>
        <p className={`mt-2 hidden text-xs font-semibold pointer-coarse:block ${body}`}>{swipeHint}</p>
      </div>
      <p className={`max-w-md text-sm leading-6 ${body}`}>{children}</p>
    </div>
  );
}

function ControlPanel({
  queueCount,
  dismissedCount,
  pending,
  feedWindow,
  onClearSourceFilter,
  onFeedWindowChange,
  onProfileChange,
  onRestoreCleared,
  onSortChange,
  profiles,
  savedCount,
  selectedProfile,
  sourceFilter,
  sort,
  sourceCount,
}: {
  queueCount: number;
  dismissedCount: number;
  pending: boolean;
  feedWindow: FeedWindow;
  onClearSourceFilter: () => void;
  onFeedWindowChange: (value: FeedWindow) => void;
  onProfileChange: (value: ProfileName) => void;
  onRestoreCleared: () => void;
  onSortChange: (value: SortKey) => void;
  profiles: Profile[];
  savedCount: number;
  selectedProfile: ProfileName;
  sourceFilter: SourceFilter;
  sort: SortKey;
  sourceCount: number;
}) {
  const selectClassName = "h-11 w-full rounded-xl border border-[#fff8e8]/15 bg-[#292520] px-3 text-sm font-semibold text-[#fff8e8] outline-none transition focus:border-[#d7b56d]";
  return (
    <aside className="lg:sticky lg:top-5 lg:self-start">
      <div className="overflow-hidden rounded-[2rem] border border-stone-950 bg-[#1d1a16] text-[#fff8e8] shadow-[10px_10px_0_rgba(28,25,23,0.22)]">
        <div className="hidden border-b border-[#fff8e8]/15 p-5 sm:p-6 lg:block">
          <Badge className="border-[#d7b56d]/40 bg-[#d7b56d]/15 text-[#f8df9d]">Personal changelog desk</Badge>
        </div>

        <div className="grid grid-cols-4 gap-px border-b border-[#fff8e8]/15 bg-[#fff8e8]/15 text-center lg:grid-cols-2">
          <DeskStat label="desk" value={queueCount} />
          <DeskStat label="shelf" value={savedCount} />
          <DeskStat label="sources" value={sourceCount} />
          <DeskStat label="cleared" value={dismissedCount}>
            {dismissedCount > 0 ? (
              <button className="mt-2 text-[0.65rem] font-black uppercase tracking-[0.18em] text-[#f8df9d] underline underline-offset-4" disabled={pending} onClick={onRestoreCleared} type="button">
                Restore
              </button>
            ) : null}
          </DeskStat>
        </div>

        <div className="grid grid-cols-3 gap-3 p-4 sm:gap-4 sm:p-6 lg:grid-cols-1">
          <label className="block">
            <span className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.24em] text-[#b8af9d]">
              <Inbox className="h-4 w-4" />
              Profile
            </span>
            <select id="profile-select" className={selectClassName} value={selectedProfile} disabled={profiles.length === 0} onChange={(event) => onProfileChange(event.target.value)}>
              {profiles.length === 0 ? <option value="">No profiles</option> : null}
              {profiles.map((profile) => (
                <option key={profile.name} value={profile.name}>
                  {profileLabel(profile.name)} - {profile.source_count} source{profile.source_count === 1 ? "" : "s"}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.24em] text-[#b8af9d]">
              <CalendarDays className="h-4 w-4" />
              Window
            </span>
            <select id="window-select" className={selectClassName} value={feedWindow} onChange={(event) => onFeedWindowChange(event.target.value as FeedWindow)}>
              {feedWindows.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label} - {item.caption}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.24em] text-[#b8af9d]">
              <LibraryBig className="h-4 w-4" />
              Order
            </span>
            <select id="sort-select" className={selectClassName} value={sort} onChange={(event) => onSortChange(event.target.value as SortKey)}>
              {sortOptions.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          {sourceFilter ? (
            <div className="col-span-3 rounded-2xl border border-[#f8df9d]/25 bg-[#292520] p-3 lg:col-span-1">
              <div className="text-[0.65rem] font-bold uppercase tracking-[0.22em] text-[#b8af9d]">Source filter</div>
              <div className="mt-1 truncate text-sm font-black text-[#fff8e8]">{sourceFilter.name}</div>
              <button className="mt-2 text-xs font-black text-[#f8df9d] underline underline-offset-4" onClick={onClearSourceFilter} type="button">
                Show all sources
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

function DeskStat({ children, label, value }: { children?: ReactNode; label: string; value: number }) {
  return (
    <div className="bg-[#1d1a16] px-3 py-4">
      <div className="font-serif text-3xl font-black leading-none text-[#f8df9d]">{value}</div>
      <div className="mt-1 text-[0.65rem] font-bold uppercase tracking-[0.22em] text-[#b8af9d]">{label}</div>
      {children}
    </div>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <div role="alert" className="whitespace-pre-line rounded-2xl border border-red-900/30 bg-red-100 p-4 text-sm font-medium leading-6 text-red-950 shadow-sm">
      {message}
    </div>
  );
}

function UndoToast({ label, pending, onUndo }: { label: string; pending: boolean; onUndo: () => void }) {
  return (
    <div className="fixed inset-x-0 bottom-4 z-20 flex justify-center px-3" role="status">
      <div className="flex items-center gap-3 rounded-full border border-stone-950 bg-[#1d1a16] py-2 pl-5 pr-2 text-sm font-semibold text-[#fff8e8] shadow-[6px_6px_0_rgba(28,25,23,0.22)]">
        {label}
        <button className="inline-flex h-9 items-center gap-1 rounded-full bg-[#f8df9d] px-4 font-black text-stone-950 transition-colors hover:bg-amber-300" disabled={pending} onClick={onUndo} type="button">
          <Undo2 className="h-4 w-4" />
          Undo
        </button>
      </div>
    </div>
  );
}

function KeyboardLegend() {
  const keys: Array<[string, string]> = [
    ["j / Down", "next card"],
    ["k / Up", "previous card"],
    ["x", "clear or mark read"],
    ["s", "shelf / back to desk"],
    ["o / Enter", "open source"],
    ["n", "note"],
    ["Cmd/Ctrl + Enter", "save note (in editor)"],
    ["Escape", "close note or shortcut help"],
    ["z / u", "undo"],
    ["r", "restore cleared items"],
    ["f", "filter to card source"],
    ["a", "show all sources"],
    ["p / w / t", "focus profile / window / order"],
    ["e", "edit sources and profiles"],
    ["?", "toggle shortcut help"],
  ];
  return (
    <div className="grid gap-3 text-sm font-semibold text-stone-600">
      {keys.map(([key, label]) => (
        <span className="inline-flex items-center gap-2" key={key}>
          <kbd className="rounded-md border border-stone-950/20 bg-white px-1.5 py-0.5 font-mono text-[0.7rem] text-stone-800">{key}</kbd>
          {label}
        </span>
      ))}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="mt-5 grid gap-4">
      {[0, 1, 2].map((item) => (
        <div className="animate-pulse rounded-[1.75rem] border border-stone-950/10 bg-stone-100 p-5" key={item}>
          <div className="h-4 w-36 rounded bg-stone-300" />
          <div className="mt-4 h-8 w-3/4 rounded bg-stone-300" />
          <div className="mt-5 space-y-2">
            <div className="h-3 rounded bg-stone-200" />
            <div className="h-3 w-11/12 rounded bg-stone-200" />
            <div className="h-3 w-2/3 rounded bg-stone-200" />
          </div>
        </div>
      ))}
    </div>
  );
}
