import DOMPurify from "dompurify";
import { marked } from "marked";
import {
  BookmarkCheck,
  BookmarkPlus,
  CalendarDays,
  ChevronDown,
  ExternalLink,
  FileText,
  Inbox,
  LibraryBig,
  Newspaper,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent } from "./components/ui/card";

type Change = {
  id: number;
  external_id: string | null;
  source_id: number;
  source_name: string;
  source_profile: string;
  plugin: string;
  title: string;
  url: string | null;
  summary: string;
  content: string;
  published_at: string;
  fetched_at: string;
  dismissed: boolean;
  saved: boolean;
  note: string;
  state_updated_at: string | null;
};

type UserChangeState = {
  dismissed?: boolean;
  saved?: boolean;
  note?: string;
};
type UserStateByChange = Record<string, UserChangeState>;
type SortKey = "newest" | "oldest" | "source" | "saved";
type FeedWindow = "24h" | "7d" | "30d" | "90d" | "365d";
type ProfileName = "dev" | "games" | string;
type SourceFilter = { id: number; name: string } | null;
type Profile = { name: ProfileName; source_count: number };
type ViewChange = Change & {
  change_key: string;
};

const apiUrl = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");
const userStateStorageKey = "changelorg:user-state:v1";
const selectedProfileStorageKey = "changelorg:selected-profile:v1";

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
  { value: "saved", label: "Saved first" },
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

function readSelectedProfile(): ProfileName {
  try {
    return localStorage.getItem(selectedProfileStorageKey) || "dev";
  } catch {
    return "dev";
  }
}

function changeKey(change: Change) {
  return `${change.source_id}:${change.external_id || change.url || change.title}`;
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

function dateValue(value: string) {
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Undated";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
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
  if (sort === "saved") {
    return copy.sort((left, right) => Number(right.saved) - Number(left.saved) || compareDate(right.published_at, left.published_at));
  }
  return copy.sort((left, right) => compareDate(right.published_at, left.published_at));
}

function renderedHtml(value: string) {
  const parsed = marked.parse(value, { async: false }) as string;
  return DOMPurify.sanitize(parsed);
}

function RenderedText({ value }: { value: string }) {
  if (!value.trim()) {
    return null;
  }
  return <div className="changelorg-rendered text-[0.95rem] leading-7 text-stone-700" dangerouslySetInnerHTML={{ __html: renderedHtml(value) }} />;
}

export default function App() {
  const [changes, setChanges] = useState<Change[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([
    { name: "dev", source_count: 0 },
    { name: "games", source_count: 0 },
  ]);
  const [selectedProfile, setSelectedProfile] = useState<ProfileName>(readSelectedProfile);
  const [sort, setSort] = useState<SortKey>("newest");
  const [feedWindow, setFeedWindow] = useState<FeedWindow>("7d");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>(null);
  const [openNotes, setOpenNotes] = useState<Record<number, boolean>>({});
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [userState, setUserState] = useState<UserStateByChange>(readUserState);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    try {
      localStorage.setItem(userStateStorageKey, JSON.stringify(userState));
    } catch {
      setError("Could not persist browser state in localStorage.");
    }
  }, [userState]);

  useEffect(() => {
    try {
      localStorage.setItem(selectedProfileStorageKey, selectedProfile);
    } catch {
      setError("Could not persist selected profile in localStorage.");
    }
  }, [selectedProfile]);

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

  const dismissedCount = viewChanges.filter((change) => change.dismissed).length;
  const savedChanges = sortedChanges(
    viewChanges.filter((change) => change.saved),
    "saved",
  );
  const feedChanges = sortedChanges(
    viewChanges.filter((change) => !change.dismissed && !change.saved && (sourceFilter === null || change.source_id === sourceFilter.id)),
    sort,
  );
  const sourceCount = new Set(viewChanges.map((change) => change.source_id)).size;

  function updateLocalChangeState(change: ViewChange, patch: UserChangeState) {
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
  }

  function onDismiss(change: ViewChange) {
    setError(null);
    updateLocalChangeState(change, { dismissed: true });
  }

  function onToggleSaved(change: ViewChange) {
    setError(null);
    const nextSaved = !change.saved;
    updateLocalChangeState(change, nextSaved ? { saved: true } : { saved: false, note: "" });
    if (!nextSaved) {
      setOpenNotes((current) => ({ ...current, [change.id]: false }));
      setNoteDrafts((current) => ({ ...current, [change.change_key]: "" }));
    }
  }

  function onSaveNote(change: ViewChange) {
    setError(null);
    updateLocalChangeState(change, { saved: true, note: noteDrafts[change.change_key] ?? "" });
    setOpenNotes((current) => ({ ...current, [change.id]: false }));
  }

  return (
    <main className="min-h-screen overflow-hidden bg-[#ece7db] px-3 py-4 text-stone-950 sm:px-5 lg:px-8">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_top_left,rgba(120,53,15,0.16),transparent_32rem),linear-gradient(90deg,rgba(28,25,23,0.045)_1px,transparent_1px),linear-gradient(rgba(28,25,23,0.045)_1px,transparent_1px)] bg-[length:auto,44px_44px,44px_44px]" />

      <section className="relative mx-auto grid max-w-7xl gap-5 lg:grid-cols-[330px_minmax(0,1fr)]">
        <ControlPanel
          queueCount={feedChanges.length}
          dismissedCount={dismissedCount}
          feedWindow={feedWindow}
          onClearSourceFilter={() => setSourceFilter(null)}
          onFeedWindowChange={setFeedWindow}
          onSortChange={setSort}
          onProfileChange={(profile) => {
            setSelectedProfile(profile);
            setSourceFilter(null);
          }}
          profiles={profiles}
          savedCount={savedChanges.length}
          selectedProfile={selectedProfile}
          sourceFilter={sourceFilter}
          sort={sort}
          sourceCount={sourceCount}
        />

        <div className="flex min-w-0 flex-col gap-5">
          {error ? <ErrorNote message={error} /> : null}

          <SavedSection
            noteDrafts={noteDrafts}
            onRemoveSaved={onToggleSaved}
            openNotes={openNotes}
            onSaveNote={onSaveNote}
            onSourceFilter={(change) => setSourceFilter({ id: change.source_id, name: change.source_name })}
            onToggleNote={(changeId) => setOpenNotes((current) => ({ ...current, [changeId]: !(current[changeId] ?? false) }))}
            onUpdateDraft={(changeKey, value) => setNoteDrafts((current) => ({ ...current, [changeKey]: value }))}
            savedChanges={savedChanges}
          />

          <section className="rounded-[2rem] border border-stone-950/15 bg-[#fffdf7]/90 p-4 shadow-[0_18px_50px_rgba(41,37,36,0.12)] backdrop-blur sm:p-5">
            <div className="flex flex-col gap-3 border-b border-stone-950/15 pb-4 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <div className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.28em] text-stone-500">
                  <Newspaper className="h-4 w-4" />
                  Main feed
                </div>
                <h2 className="font-serif text-3xl font-black leading-none tracking-tight text-stone-950 sm:text-4xl">The current edition</h2>
              </div>
              <p className="max-w-md text-sm leading-6 text-stone-600">
                {feedChanges.length} readable {profileLabel(selectedProfile)} item{feedChanges.length === 1 ? "" : "s"} from the last {windowLabel(feedWindow)}. Dismissals are saved in this browser.
              </p>
            </div>

            <FeedBody
              changes={feedChanges}
              isLoading={isLoading}
              noteDrafts={noteDrafts}
              openNotes={openNotes}
              onDismiss={onDismiss}
              onSaveNote={onSaveNote}
              onSourceFilter={(change) => setSourceFilter({ id: change.source_id, name: change.source_name })}
              onToggleNote={(changeId) => setOpenNotes((current) => ({ ...current, [changeId]: !(current[changeId] ?? false) }))}
              onToggleSaved={onToggleSaved}
              onUpdateDraft={(changeKey, value) => setNoteDrafts((current) => ({ ...current, [changeKey]: value }))}
            />
          </section>
        </div>
      </section>
    </main>
  );
}

function ControlPanel({
  queueCount,
  dismissedCount,
  feedWindow,
  onClearSourceFilter,
  onFeedWindowChange,
  onProfileChange,
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
  onSortChange: (value: SortKey) => void;
  profiles: Profile[];
  savedCount: number;
  selectedProfile: ProfileName;
  sourceFilter: SourceFilter;
  sort: SortKey;
  sourceCount: number;
}) {
  return (
    <aside className="lg:sticky lg:top-5 lg:self-start">
      <div className="overflow-hidden rounded-[2rem] border border-stone-950 bg-[#1d1a16] text-[#fff8e8] shadow-[10px_10px_0_rgba(28,25,23,0.22)]">
        <div className="border-b border-[#fff8e8]/15 p-5 sm:p-6">
          <Badge className="border-[#d7b56d]/40 bg-[#d7b56d]/15 text-[#f8df9d]">Local changelog desk</Badge>
          <h1 className="mt-5 font-serif text-5xl font-black leading-[0.9] tracking-tight sm:text-6xl lg:text-5xl">change<br />lorg</h1>
          <p className="mt-4 text-sm leading-6 text-[#d7d0c0]">
            A compact research inbox for release notes, product updates, and the bits worth saving before they disappear into tabs.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-px border-b border-[#fff8e8]/15 bg-[#fff8e8]/15 text-center">
          <DeskStat label="queue" value={queueCount} />
          <DeskStat label="sources" value={sourceCount} />
          <DeskStat label="saved" value={savedCount} />
          <DeskStat label="dismissed" value={dismissedCount} />
        </div>

        <div className="space-y-4 p-5 sm:p-6">
          <label className="block">
            <span className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.24em] text-[#b8af9d]">
              <Inbox className="h-4 w-4" />
              Profile
            </span>
            <select
              className="h-11 w-full rounded-xl border border-[#fff8e8]/15 bg-[#292520] px-3 text-sm font-semibold text-[#fff8e8] outline-none transition focus:border-[#d7b56d]"
              value={selectedProfile}
              onChange={(event) => onProfileChange(event.target.value)}
            >
              {profiles.map((profile) => (
                <option key={profile.name} value={profile.name}>
                  {profileLabel(profile.name)} - {profile.source_count} source{profile.source_count === 1 ? "" : "s"}
                </option>
              ))}
            </select>
          </label>

          {sourceFilter ? (
            <div className="rounded-2xl border border-[#f8df9d]/25 bg-[#292520] p-3">
              <div className="text-[0.65rem] font-bold uppercase tracking-[0.22em] text-[#b8af9d]">Source filter</div>
              <div className="mt-1 truncate text-sm font-black text-[#fff8e8]">{sourceFilter.name}</div>
              <button className="mt-2 text-xs font-black text-[#f8df9d] underline underline-offset-4" onClick={onClearSourceFilter} type="button">
                Show all sources
              </button>
            </div>
          ) : null}

          <label className="block">
            <span className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.24em] text-[#b8af9d]">
              <CalendarDays className="h-4 w-4" />
              Window
            </span>
            <select
              className="h-11 w-full rounded-xl border border-[#fff8e8]/15 bg-[#292520] px-3 text-sm font-semibold text-[#fff8e8] outline-none transition focus:border-[#d7b56d]"
              value={feedWindow}
              onChange={(event) => onFeedWindowChange(event.target.value as FeedWindow)}
            >
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
              Filing order
            </span>
            <select
              className="h-11 w-full rounded-xl border border-[#fff8e8]/15 bg-[#292520] px-3 text-sm font-semibold text-[#fff8e8] outline-none transition focus:border-[#d7b56d]"
              value={sort}
              onChange={(event) => onSortChange(event.target.value as SortKey)}
            >
              {sortOptions.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          <div className="rounded-2xl border border-[#f8df9d]/30 bg-[#f8df9d] px-4 py-4 text-stone-950">
            <span className="block font-black">Auto-refreshed hourly</span>
            <span className="mt-1 block text-xs font-semibold text-stone-700">Use filters here. The backend fetches and normalizes sources every hour.</span>
          </div>
        </div>
      </div>
    </aside>
  );
}

function DeskStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-[#1d1a16] px-3 py-4">
      <div className="font-serif text-3xl font-black leading-none text-[#f8df9d]">{value}</div>
      <div className="mt-1 text-[0.65rem] font-bold uppercase tracking-[0.22em] text-[#b8af9d]">{label}</div>
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

function SavedSection({
  noteDrafts,
  onRemoveSaved,
  openNotes,
  onSaveNote,
  onSourceFilter,
  onToggleNote,
  onUpdateDraft,
  savedChanges,
}: {
  noteDrafts: Record<string, string>;
  openNotes: Record<number, boolean>;
  onRemoveSaved: (change: ViewChange) => void;
  onSaveNote: (change: ViewChange) => void;
  onSourceFilter: (change: ViewChange) => void;
  onToggleNote: (changeId: number) => void;
  onUpdateDraft: (changeKey: string, value: string) => void;
  savedChanges: ViewChange[];
}) {
  return (
    <section className="rounded-[2rem] border border-amber-900/30 bg-[#f9d978] p-4 text-stone-950 shadow-[8px_8px_0_rgba(120,53,15,0.18)]">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2 text-xs font-black uppercase tracking-[0.28em] text-amber-950/65">
            <BookmarkCheck className="h-4 w-4" />
            Saved notes
          </div>
          <h2 className="font-serif text-2xl font-black leading-none tracking-tight sm:text-3xl">Pinned for later</h2>
        </div>
        <Badge className="w-fit border-amber-950/20 bg-amber-950 text-amber-50">{savedChanges.length} saved</Badge>
      </div>

      {savedChanges.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-amber-950/35 bg-amber-50/35 p-4 text-sm font-medium leading-6 text-amber-950/75">
          Save a change or add a note and it will stay parked here, above the noise, until you remove it.
        </div>
      ) : (
        <div className="mt-4 grid gap-3 xl:grid-cols-2">
          {savedChanges.map((change) => (
            <SavedCard
              change={change}
              key={change.id}
              noteDraft={noteDrafts[change.change_key] ?? ""}
              noteOpen={openNotes[change.id] ?? false}
              onRemoveSaved={onRemoveSaved}
              onSaveNote={onSaveNote}
              onSourceFilter={onSourceFilter}
              onToggleNote={() => onToggleNote(change.id)}
              onUpdateDraft={(value) => onUpdateDraft(change.change_key, value)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function SavedCard({
  change,
  noteDraft,
  noteOpen,
  onRemoveSaved,
  onSaveNote,
  onSourceFilter,
  onToggleNote,
  onUpdateDraft,
}: {
  change: ViewChange;
  noteDraft: string;
  noteOpen: boolean;
  onRemoveSaved: (change: ViewChange) => void;
  onSaveNote: (change: ViewChange) => void;
  onSourceFilter: (change: ViewChange) => void;
  onToggleNote: () => void;
  onUpdateDraft: (value: string) => void;
}) {
  return (
    <Card className="rounded-2xl border-amber-950/20 bg-[#fff8e8]/90 shadow-none">
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-amber-950/60">
          <button className="transition-colors hover:text-stone-950 hover:underline" onClick={() => onSourceFilter(change)} title={`Filter to ${change.source_name}`} type="button">
            {change.source_name}
          </button>
          <span aria-hidden="true">/</span>
          <span>{formatDate(change.published_at)}</span>
        </div>
        <h3 className="line-clamp-2 font-serif text-lg font-black leading-tight text-stone-950 sm:text-xl">{change.title}</h3>
        {change.note.trim() ? <p className="scrollbar-none max-h-24 overflow-y-auto rounded-xl bg-amber-100/80 p-3 text-sm leading-6 text-stone-700">{change.note}</p> : null}
        {noteOpen ? (
          <div className="rounded-xl border border-amber-950/20 bg-amber-50/70 p-3">
            <textarea
              className="min-h-20 w-full rounded-xl border border-amber-950/20 bg-white p-3 text-sm leading-6 text-stone-950 outline-none focus:border-amber-950"
              onChange={(event) => onUpdateDraft(event.target.value)}
              placeholder="Add why this is worth revisiting."
              value={noteDraft}
            />
            <div className="mt-2 flex justify-end">
              <Button className="rounded-xl bg-amber-950 text-amber-50 hover:bg-amber-900" onClick={() => onSaveNote(change)}>
                Save note
              </Button>
            </div>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-3">
          {change.url ? (
            <a className="inline-flex items-center gap-1 text-sm font-black text-stone-950 underline decoration-amber-800/40 underline-offset-4" href={change.url} rel="noreferrer" target="_blank">
              Revisit source
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          ) : null}
          <button className="inline-flex items-center gap-1 text-sm font-black text-stone-950 underline decoration-amber-800/40 underline-offset-4" onClick={onToggleNote} type="button">
            {noteOpen ? "Close note" : change.note.trim() ? "Edit note" : "Add note"}
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${noteOpen ? "rotate-180" : ""}`} />
          </button>
          <button className="inline-flex items-center gap-1 text-sm font-black text-amber-950 underline decoration-amber-800/40 underline-offset-4" onClick={() => onRemoveSaved(change)} type="button">
            Remove
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </CardContent>
    </Card>
  );
}

function FeedBody({
  changes,
  isLoading,
  noteDrafts,
  openNotes,
  onDismiss,
  onSaveNote,
  onSourceFilter,
  onToggleNote,
  onToggleSaved,
  onUpdateDraft,
}: {
  changes: ViewChange[];
  isLoading: boolean;
  noteDrafts: Record<string, string>;
  openNotes: Record<number, boolean>;
  onDismiss: (change: ViewChange) => void;
  onSaveNote: (change: ViewChange) => void;
  onSourceFilter: (change: ViewChange) => void;
  onToggleNote: (changeId: number) => void;
  onToggleSaved: (change: ViewChange) => void;
  onUpdateDraft: (changeKey: string, value: string) => void;
}) {
  if (isLoading) {
    return <LoadingState />;
  }

  if (changes.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="mt-5 grid gap-4">
      {changes.map((change) => (
        <ChangeCard
          change={change}
          key={change.id}
          noteDraft={noteDrafts[change.change_key] ?? ""}
          noteOpen={openNotes[change.id] ?? false}
          onDismiss={onDismiss}
          onSaveNote={onSaveNote}
          onSourceFilter={onSourceFilter}
          onToggleNote={() => onToggleNote(change.id)}
          onToggleSaved={onToggleSaved}
          onUpdateDraft={(value) => onUpdateDraft(change.change_key, value)}
        />
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
      <h3 className="mt-4 font-serif text-2xl font-black">No cached changes on the desk</h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-stone-600">The backend refreshes sources hourly. If this stays empty, add sources with the CLI or check the backend refresh logs.</p>
    </div>
  );
}

function ChangeCard({
  change,
  noteDraft,
  noteOpen,
  onDismiss,
  onSaveNote,
  onSourceFilter,
  onToggleNote,
  onToggleSaved,
  onUpdateDraft,
}: {
  change: ViewChange;
  noteDraft: string;
  noteOpen: boolean;
  onDismiss: (change: ViewChange) => void;
  onSaveNote: (change: ViewChange) => void;
  onSourceFilter: (change: ViewChange) => void;
  onToggleNote: () => void;
  onToggleSaved: (change: ViewChange) => void;
  onUpdateDraft: (value: string) => void;
}) {
  const preview = change.content || change.summary;
  const noteId = `note-${change.id}`;

  return (
    <article className="group overflow-hidden rounded-[1.75rem] border border-stone-950 bg-[#fffaf0] shadow-[5px_5px_0_rgba(28,25,23,0.14)] transition-transform duration-200 hover:-translate-y-0.5">
      <div className="grid gap-0 md:grid-cols-[minmax(0,1fr)_190px]">
        <div className="min-w-0 p-4 sm:p-5">
          <div className="flex flex-wrap items-center gap-2 text-xs font-black uppercase tracking-[0.18em] text-stone-500">
            <button
              className="inline-flex items-center rounded-full border border-stone-950 bg-stone-950 px-2.5 py-1 text-[#fff8e8] transition-colors hover:bg-stone-800 focus:outline-none focus:ring-2 focus:ring-amber-400 focus:ring-offset-2 focus:ring-offset-[#fffaf0]"
              onClick={() => onSourceFilter(change)}
              title={`Filter to ${change.source_name}`}
              type="button"
            >
              {change.source_name}
            </button>
            {change.saved ? <Badge className="border-amber-900/25 bg-amber-200 text-amber-950">Saved</Badge> : null}
            <span className="inline-flex items-center gap-1">
              <CalendarDays className="h-3.5 w-3.5" />
              {formatDate(change.published_at)}
            </span>
          </div>

          <h3 className="mt-4 max-w-3xl font-serif text-2xl font-black leading-[1.08] tracking-tight text-stone-950 sm:text-[2rem]">{change.title}</h3>

          {preview.trim() ? (
            <div className="scrollbar-none mt-4 max-h-72 overflow-y-auto rounded-2xl border border-stone-950/10 bg-white/65 px-4 py-3">
              <RenderedText value={preview} />
            </div>
          ) : (
            <div className="mt-4 rounded-2xl border border-dashed border-stone-950/20 bg-white/45 px-4 py-3 text-sm font-medium text-stone-500">No preview text came back for this source.</div>
          )}
        </div>

        <div className="flex flex-col justify-between border-t border-stone-950 bg-[#eee6d6] p-4 md:border-l md:border-t-0">
          <div className="space-y-1 text-sm text-stone-700">
            <div className="flex items-center gap-2 font-black text-stone-950">
              <FileText className="h-4 w-4" />
              Actions
            </div>
            <p className="leading-5">Open, save, annotate, or dismiss.</p>
          </div>

          <div className="mt-4 grid gap-2">
            {change.url ? (
              <a className="inline-flex h-10 items-center justify-center rounded-xl border border-stone-950 bg-white px-3 text-sm font-black text-stone-950 transition-colors hover:bg-amber-100" href={change.url} rel="noreferrer" target="_blank">
                Open source
                <ExternalLink className="ml-2 h-3.5 w-3.5" />
              </a>
            ) : null}
            <div className="grid grid-cols-[1fr_auto] gap-2">
              <Button className="rounded-xl bg-white text-stone-950 hover:bg-amber-100" onClick={() => onToggleSaved(change)} variant="secondary">
                {change.saved ? <BookmarkCheck className="mr-2 h-4 w-4" /> : <BookmarkPlus className="mr-2 h-4 w-4" />}
                {change.saved ? "Saved" : "Save"}
              </Button>
              <Button
                aria-controls={noteId}
                aria-expanded={noteOpen}
                aria-label={noteOpen ? "Collapse note editor" : "Expand note editor"}
                className="rounded-xl bg-white px-3 text-stone-950 hover:bg-amber-100"
                onClick={onToggleNote}
                variant="secondary"
              >
                <ChevronDown className={`h-4 w-4 transition-transform ${noteOpen ? "rotate-180" : ""}`} />
              </Button>
            </div>
            <Button className="rounded-xl bg-transparent text-stone-700 hover:bg-stone-950 hover:text-[#fff8e8]" onClick={() => onDismiss(change)} variant="secondary">
              <X className="mr-2 h-4 w-4" />
              Dismiss
            </Button>
          </div>
        </div>
      </div>

      {noteOpen ? (
        <div className="border-t border-stone-950 bg-[#fff8e8] p-4 sm:p-5" id={noteId}>
          <label className="flex items-center gap-2 text-sm font-black uppercase tracking-[0.18em] text-stone-600" htmlFor={`${noteId}-textarea`}>
            <BookmarkPlus className="h-4 w-4" />
            Note for later
          </label>
          <textarea
            className="mt-3 min-h-28 w-full rounded-2xl border border-stone-950/20 bg-white p-4 text-sm leading-6 text-stone-950 shadow-inner outline-none transition focus:border-stone-950"
            id={`${noteId}-textarea`}
            onChange={(event) => onUpdateDraft(event.target.value)}
            placeholder="Why is this worth revisiting? Add the decision, risk, or follow-up here."
            value={noteDraft}
          />
          <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs font-medium text-stone-500">Saving a note also pins this item to the top section.</p>
            <Button className="rounded-xl bg-stone-950 text-[#fff8e8] hover:bg-stone-800" onClick={() => onSaveNote(change)}>
              Save note
            </Button>
          </div>
        </div>
      ) : null}
    </article>
  );
}
