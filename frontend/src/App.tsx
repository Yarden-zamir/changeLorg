import { BookmarkCheck, CalendarDays, Inbox, LibraryBig, Newspaper, Undo2 } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { ChangeCard } from "./components/ChangeCard";
import { Badge } from "./components/ui/badge";
import { changeKey, dateValue, openChange, type CardLocation, type Change, type ViewChange } from "./lib/changes";

type UserChangeState = {
  dismissed?: boolean;
  saved?: boolean;
  note?: string;
};
type UserStateByChange = Record<string, UserChangeState>;
type SortKey = "newest" | "oldest" | "source";
type FeedWindow = "24h" | "7d" | "30d" | "90d" | "365d";
type ProfileName = "dev" | "games" | string;
type SourceFilter = { id: number; name: string } | null;
type Profile = { name: ProfileName; source_count: number };
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
  label: string;
  previous: UserChangeState | undefined;
};
/** Where the viewport must land after a card leaves a queue. */
type ScrollAnchor = {
  nextKey: string | null;
  top: number;
};

const apiUrl = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");
const userStateStorageKey = "changelorg:user-state:v1";
const undoTimeoutMs = 8000;
const anchorTopMin = 16;
const defaultUrlState: UrlState = {
  profile: "dev",
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

async function fetchChanges(feedWindow: FeedWindow, profile: ProfileName): Promise<Change[]> {
  const response = await fetch(`${apiUrl}/changes?since=${feedWindow}&limit=200&include_dismissed=true&profile=${encodeURIComponent(profile)}`);
  if (!response.ok) {
    throw new Error(`Could not load changes: ${response.status}`);
  }
  return response.json();
}

async function fetchProfiles(): Promise<Profile[]> {
  const response = await fetch(`${apiUrl}/profiles`);
  if (!response.ok) {
    throw new Error(`Could not load profiles: ${response.status}`);
  }
  return response.json();
}

function readUserState(): UserStateByChange {
  try {
    const raw = localStorage.getItem(userStateStorageKey);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as UserStateByChange) : {};
  } catch {
    return {};
  }
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
  const parsed = Number.parseInt(value, 10);
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
  params.set("profile", state.profile);
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

function normalizeState(state: UserChangeState): UserChangeState | null {
  const note = state.note?.trim() ? state.note : undefined;
  const normalized = {
    dismissed: state.dismissed || undefined,
    saved: state.saved || undefined,
    note,
  } satisfies UserChangeState;
  return normalized.dismissed || normalized.saved || normalized.note ? normalized : null;
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
  const [changes, setChanges] = useState<Change[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [urlState, setUrlState] = useState<UrlState>(readUrlState);
  const [profiles, setProfiles] = useState<Profile[]>([
    { name: "dev", source_count: 0 },
    { name: "games", source_count: 0 },
  ]);
  const [openNotes, setOpenNotes] = useState<Record<string, boolean>>({});
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [userState, setUserState] = useState<UserStateByChange>(readUserState);
  const [isLoading, setIsLoading] = useState(true);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<LastAction | null>(null);
  const scrollAnchor = useRef<ScrollAnchor | null>(null);
  const scrollOnFocus = useRef(false);

  const selectedProfile = urlState.profile;
  const feedWindow = urlState.feedWindow;
  const sort = urlState.sort;

  function updateUrlState(patch: Partial<UrlState>, mode: "push" | "replace" = "push") {
    setUrlState((current) => {
      const next = { ...current, ...patch };
      window.history[mode === "push" ? "pushState" : "replaceState"](null, "", urlForState(next));
      return next;
    });
  }

  useEffect(() => {
    window.history.replaceState(null, "", urlForState(urlState));
    const onPopState = () => setUrlState(readUrlState());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(userStateStorageKey, JSON.stringify(userState));
    } catch {
      setError("Could not persist browser state in localStorage.");
    }
  }, [userState]);

  useEffect(() => {
    fetchProfiles()
      .then((loaded) => {
        const byName = new Map<ProfileName, Profile>();
        for (const profile of loaded) {
          byName.set(profile.name, profile);
        }
        byName.set("dev", byName.get("dev") ?? { name: "dev", source_count: 0 });
        byName.set("games", byName.get("games") ?? { name: "games", source_count: 0 });
        const ordered = ["dev", "games", ...loaded.map((profile) => profile.name).filter((name) => name !== "dev" && name !== "games")];
        setProfiles(ordered.map((name) => byName.get(name)).filter((profile): profile is Profile => Boolean(profile)));
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Unknown error"));
  }, []);

  useEffect(() => {
    let cancelled = false;

    setIsLoading(true);
    setError(null);
    fetchChanges(feedWindow, selectedProfile)
      .then((loaded) => {
        if (cancelled) {
          return;
        }
        setChanges(loaded);
        setNoteDrafts(Object.fromEntries(loaded.map((change) => [changeKey(change), userState[changeKey(change)]?.note ?? ""])));
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Unknown error");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [feedWindow, selectedProfile]);

  useEffect(() => {
    if (!lastAction) {
      return;
    }
    const timer = window.setTimeout(() => setLastAction(null), undoTimeoutMs);
    return () => window.clearTimeout(timer);
  }, [lastAction]);

  const viewChanges: ViewChange[] = changes.map((change) => {
    const key = changeKey(change);
    const state = userState[key] ?? {};
    return {
      ...change,
      change_key: key,
      dismissed: Boolean(state.dismissed),
      saved: Boolean(state.saved),
      note: state.note ?? "",
    };
  });

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
  const sourceCount = new Set(viewChanges.map((change) => change.source_id)).size;
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
  }, [userState]);

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

  function updateLocalChangeState(change: ViewChange, patch: UserChangeState, label: string | null) {
    setUserState((current) => {
      const currentState = current[change.change_key] ?? {};
      const nextState = normalizeState({ ...currentState, ...patch });
      const next = { ...current };
      if (nextState) {
        next[change.change_key] = nextState;
      } else {
        delete next[change.change_key];
      }
      return next;
    });
    if (label) {
      setLastAction({ changeKey: change.change_key, label, previous: userState[change.change_key] });
    }
  }

  /** Record where `change` sits so the following card can take its place, and move focus to it. */
  function anchorAfterRemoval(change: ViewChange) {
    const index = visibleKeys.indexOf(change.change_key);
    const nextKey = visibleKeys[index + 1] ?? visibleKeys[index - 1] ?? null;
    scrollAnchor.current = { nextKey, top: layoutTop(cardElement(change.change_key)) };
    if (focusedKey === change.change_key) {
      setFocusedKey(nextKey);
    }
  }

  function onClear(change: ViewChange) {
    setError(null);
    anchorAfterRemoval(change);
    updateLocalChangeState(change, { dismissed: true }, change.saved ? "Marked read" : "Cleared from desk");
  }

  function onToggleShelf(change: ViewChange) {
    setError(null);
    anchorAfterRemoval(change);
    const nextSaved = !change.saved;
    updateLocalChangeState(change, nextSaved ? { saved: true } : { saved: false, note: "" }, nextSaved ? "Shelved" : "Back on the desk");
    if (!nextSaved) {
      setOpenNotes((current) => ({ ...current, [change.change_key]: false }));
      setNoteDrafts((current) => ({ ...current, [change.change_key]: "" }));
    }
  }

  function onSaveNote(change: ViewChange) {
    setError(null);
    if (!change.saved) {
      anchorAfterRemoval(change);
    }
    updateLocalChangeState(change, { saved: true, note: noteDrafts[change.change_key] ?? "" }, change.saved ? null : "Shelved with note");
    setOpenNotes((current) => ({ ...current, [change.change_key]: false }));
  }

  function onToggleNote(change: ViewChange) {
    setOpenNotes((current) => ({ ...current, [change.change_key]: !(current[change.change_key] ?? false) }));
  }

  function onUndo() {
    if (!lastAction) {
      return;
    }
    const { changeKey: key, previous } = lastAction;
    setUserState((current) => {
      const next = { ...current };
      if (previous) {
        next[key] = previous;
      } else {
        delete next[key];
      }
      return next;
    });
    setLastAction(null);
    scrollOnFocus.current = true;
    setFocusedKey(key);
  }

  function onRestoreCleared() {
    setUserState((current) => {
      const next = { ...current };
      for (const change of viewChanges) {
        if (!change.dismissed) {
          continue;
        }
        const restored = normalizeState({ ...next[change.change_key], dismissed: false });
        if (restored) {
          next[change.change_key] = restored;
        } else {
          delete next[change.change_key];
        }
      }
      return next;
    });
    setLastAction(null);
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) {
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
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const cardProps = (change: ViewChange, location: CardLocation) => ({
    change,
    location,
    focused: focusedKey === change.change_key,
    noteDraft: noteDrafts[change.change_key] ?? "",
    noteOpen: openNotes[change.change_key] ?? false,
    onFocus: () => setFocusedKey(change.change_key),
    onClear: () => onClear(change),
    onToggleShelf: () => onToggleShelf(change),
    onSaveNote: () => onSaveNote(change),
    onSourceFilter: () => updateUrlState({ sourceId: change.source_id, sourceName: change.source_name }),
    onToggleNote: () => onToggleNote(change),
    onUpdateDraft: (value: string) => setNoteDrafts((current) => ({ ...current, [change.change_key]: value })),
  });

  return (
    <main className="min-h-screen overflow-x-hidden bg-[#ece7db] px-3 py-4 text-stone-950 [overflow-anchor:none] sm:px-5 lg:px-8">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_top_left,rgba(120,53,15,0.16),transparent_32rem),linear-gradient(90deg,rgba(28,25,23,0.045)_1px,transparent_1px),linear-gradient(rgba(28,25,23,0.045)_1px,transparent_1px)] bg-[length:auto,44px_44px,44px_44px]" />

      <section className="relative mx-auto grid max-w-7xl gap-5 lg:grid-cols-[330px_minmax(0,1fr)]">
        <ControlPanel
          queueCount={deskChanges.length}
          dismissedCount={dismissedCount}
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
          {error ? <ErrorNote message={error} /> : null}

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
              {deskChanges.length} readable {profileLabel(selectedProfile)} item{deskChanges.length === 1 ? "" : "s"} from the last {windowLabel(feedWindow)}. Desk state stays in this browser.
            </QueueHeader>

            {isLoading ? (
              <LoadingState />
            ) : deskChanges.length === 0 ? (
              <EmptyState />
            ) : (
              <div className="mt-5 grid gap-4">
                {deskChanges.map((change) => (
                  <ChangeCard key={change.change_key} {...cardProps(change, "desk")} />
                ))}
              </div>
            )}
          </section>

          <KeyboardLegend />
        </div>
      </section>

      {lastAction ? <UndoToast label={lastAction.label} onUndo={onUndo} /> : null}
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
          <Badge className="border-[#d7b56d]/40 bg-[#d7b56d]/15 text-[#f8df9d]">Local changelog desk</Badge>
        </div>

        <div className="grid grid-cols-4 gap-px border-b border-[#fff8e8]/15 bg-[#fff8e8]/15 text-center lg:grid-cols-2">
          <DeskStat label="desk" value={queueCount} />
          <DeskStat label="shelf" value={savedCount} />
          <DeskStat label="sources" value={sourceCount} />
          <DeskStat label="cleared" value={dismissedCount}>
            {dismissedCount > 0 ? (
              <button className="mt-2 text-[0.65rem] font-black uppercase tracking-[0.18em] text-[#f8df9d] underline underline-offset-4" onClick={onRestoreCleared} type="button">
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
            <select className={selectClassName} value={selectedProfile} onChange={(event) => onProfileChange(event.target.value)}>
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
            <select className={selectClassName} value={feedWindow} onChange={(event) => onFeedWindowChange(event.target.value as FeedWindow)}>
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
            <select className={selectClassName} value={sort} onChange={(event) => onSortChange(event.target.value as SortKey)}>
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
    <div className="whitespace-pre-line rounded-2xl border border-red-900/30 bg-red-100 p-4 text-sm font-medium leading-6 text-red-950 shadow-sm">
      {message}
    </div>
  );
}

function UndoToast({ label, onUndo }: { label: string; onUndo: () => void }) {
  return (
    <div className="fixed inset-x-0 bottom-4 z-20 flex justify-center px-3" role="status">
      <div className="flex items-center gap-3 rounded-full border border-stone-950 bg-[#1d1a16] py-2 pl-5 pr-2 text-sm font-semibold text-[#fff8e8] shadow-[6px_6px_0_rgba(28,25,23,0.22)]">
        {label}
        <button className="inline-flex h-9 items-center gap-1 rounded-full bg-[#f8df9d] px-4 font-black text-stone-950 transition-colors hover:bg-amber-300" onClick={onUndo} type="button">
          <Undo2 className="h-4 w-4" />
          Undo
        </button>
      </div>
    </div>
  );
}

function KeyboardLegend() {
  const keys: Array<[string, string]> = [
    ["j / k", "next / previous"],
    ["x", "clear or mark read"],
    ["s", "shelf / back to desk"],
    ["o", "open source"],
    ["n", "note"],
    ["z", "undo"],
  ];
  return (
    <div className="hidden flex-wrap items-center gap-x-5 gap-y-2 px-2 text-xs font-semibold text-stone-500 pointer-fine:flex">
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

function EmptyState() {
  return (
    <div className="mt-5 rounded-[1.75rem] border border-dashed border-stone-950/25 bg-stone-100/80 p-6 text-center">
      <Inbox className="mx-auto h-9 w-9 text-stone-600" />
      <h3 className="mt-4 font-serif text-2xl font-black">Desk is clear</h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-stone-600">Try a wider window, another profile, a different source filter, or restore cleared items from the panel.</p>
    </div>
  );
}
