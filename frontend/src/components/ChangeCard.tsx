import { BookmarkCheck, BookmarkPlus, CalendarDays, Check, ChevronDown, ExternalLink, Undo2, X, type LucideIcon } from "lucide-react";
import { useEffect, useRef } from "react";

import { formatDate, openChange, renderedHtml, safeUrl, type CardLocation, type ViewChange } from "../lib/changes";
import { leaveDurationMs, useSwipe, type SwipeDirection } from "../lib/swipe";
import { Button } from "./ui/button";

type SwipeAction = {
  label: string;
  icon: LucideIcon;
  /** True when the card leaves its queue after the action. */
  leaves: boolean;
  tone: "danger" | "accent";
  run: () => void | Promise<boolean>;
};

export type ChangeCardProps = {
  change: ViewChange;
  location: CardLocation;
  focused: boolean;
  pending: boolean;
  noteDraft: string;
  noteOpen: boolean;
  onFocus: () => void;
  /** Desk: clear from desk. Shelf: mark read, which also clears it. */
  onClear: () => Promise<boolean>;
  /** Desk: move to shelf. Shelf: put back on the desk. */
  onToggleShelf: () => Promise<boolean>;
  onSaveNote: () => void;
  onSourceFilter: () => void;
  onToggleNote: () => void;
  onUpdateDraft: (value: string) => void;
};

function RenderedText({ value }: { value: string }) {
  if (!value.trim()) {
    return null;
  }
  return <div className="changelorg-rendered text-[0.95rem] leading-7 text-stone-700" dangerouslySetInnerHTML={{ __html: renderedHtml(value) }} />;
}

export function ChangeCard({ change, location, focused, pending, noteDraft, noteOpen, onFocus, onClear, onToggleShelf, onSaveNote, onSourceFilter, onToggleNote, onUpdateDraft }: ChangeCardProps) {
  const preview = change.content || change.summary;
  const noteId = `note-${change.id}`;
  const onShelf = location === "shelf";
  const url = safeUrl(change.url);
  const leaveTimer = useRef<number | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (leaveTimer.current !== null) window.clearTimeout(leaveTimer.current);
    };
  }, []);

  const leftAction: SwipeAction = onShelf
    ? { label: "Mark read", icon: Check, leaves: true, tone: "danger", run: onClear }
    : { label: "Clear", icon: X, leaves: true, tone: "danger", run: onClear };
  const rightAction: SwipeAction = onShelf
    ? { label: "Open source", icon: ExternalLink, leaves: false, tone: "accent", run: () => openChange(change) }
    : { label: "Shelf", icon: BookmarkPlus, leaves: true, tone: "accent", run: onToggleShelf };
  const actions = useRef({ left: leftAction, right: rightAction });
  actions.current = { left: leftAction, right: rightAction };

  const swipe = useSwipe((direction: SwipeDirection) => {
    const action = direction === "left" ? leftAction : rightAction;
    if (action.leaves) {
      if (leaveTimer.current !== null) return true;
      leaveTimer.current = window.setTimeout(async () => {
        try { await actions.current[direction].run(); }
        finally {
          leaveTimer.current = null;
          if (mounted.current) swipe.reset();
        }
      }, leaveDurationMs);
      return true;
    }
    action.run();
    return false;
  }, pending);

  return (
    <article
      {...swipe.handlers}
      className={`group relative overflow-hidden rounded-[1.75rem] border bg-[#fffaf0] shadow-[5px_5px_0_rgba(28,25,23,0.14)] outline-none ${
        onShelf ? "border-amber-950/40" : "border-stone-950"
      } ${focused ? "ring-2 ring-amber-500 ring-offset-2 ring-offset-[#ece7db]" : ""}`}
      data-change-key={change.change_key}
      aria-busy={pending}
      onFocus={onFocus}
      style={swipe.style}
      tabIndex={0}
    >
      <SwipeHint action={leftAction} progress={swipe.leftProgress} side="left" />
      <SwipeHint action={rightAction} progress={swipe.rightProgress} side="right" />

      <div className="grid gap-0 md:grid-cols-[minmax(0,1fr)_210px]">
        <div className="min-w-0 p-4 sm:p-5">
          <div className="flex flex-wrap items-center gap-2 text-xs font-black uppercase tracking-[0.18em] text-stone-500">
            <button
              className="inline-flex items-center rounded-full border border-stone-950 bg-stone-950 px-2.5 py-1 text-[#fff8e8] transition-colors hover:bg-stone-800 focus:outline-none focus:ring-2 focus:ring-amber-400 focus:ring-offset-2 focus:ring-offset-[#fffaf0]"
              onClick={onSourceFilter}
              title={`Filter to ${change.source_name}`}
              type="button"
            >
              {change.source_name}
            </button>
            <span className="inline-flex items-center gap-1">
              <CalendarDays className="h-3.5 w-3.5" />
              {formatDate(change.published_at)}
            </span>
          </div>

          <h3 className="mt-4 max-w-3xl font-serif text-2xl font-black leading-[1.08] tracking-tight text-stone-950 sm:text-[2rem]">
            {url ? (
              <a className="decoration-amber-800/40 underline-offset-4 hover:underline" href={url} rel="noopener noreferrer" target="_blank">
                {change.title}
              </a>
            ) : (
              change.title
            )}
          </h3>

          {change.note.trim() && !noteOpen ? (
            <p className="mt-4 whitespace-pre-line rounded-2xl border border-amber-900/20 bg-amber-100/80 px-4 py-3 text-sm leading-6 text-stone-800">{change.note}</p>
          ) : null}

          {preview.trim() ? (
            <div className="scrollbar-none mt-4 max-h-72 touch-pan-y overflow-y-auto rounded-2xl border border-stone-950/10 bg-white/65 px-4 py-3">
              <RenderedText value={preview} />
            </div>
          ) : (
            <div className="mt-4 rounded-2xl border border-dashed border-stone-950/20 bg-white/45 px-4 py-3 text-sm font-medium text-stone-500">No preview text came back for this source.</div>
          )}
        </div>

        <div className={`flex flex-col justify-end border-t border-stone-950 p-4 md:justify-start md:gap-4 md:border-l md:border-t-0 ${onShelf ? "bg-[#f3e2a8]" : "bg-[#eee6d6]"}`}>
          <Button
            className="hidden whitespace-nowrap rounded-xl bg-transparent px-3 text-stone-700 hover:bg-stone-950 hover:text-[#fff8e8] md:inline-flex"
            disabled={pending || swipe.leaving !== null}
            onClick={() => swipe.trigger("left")}
            title={`${onShelf ? "Mark read" : "Clear from desk"} (x)`}
            variant="secondary"
          >
            {onShelf ? <Check className="mr-2 h-4 w-4" /> : <X className="mr-2 h-4 w-4" />}
            {onShelf ? "Mark read" : "Clear from desk"}
          </Button>
          <div className="grid grid-cols-2 gap-2 md:mt-auto md:grid-cols-1">
            {url ? (
              <a
                className="inline-flex h-10 items-center justify-center rounded-xl border border-stone-950 bg-white px-3 text-sm font-black text-stone-950 transition-colors hover:bg-amber-100"
                href={url}
                rel="noreferrer"
                target="_blank"
              >
                Open source
                <ExternalLink className="ml-2 h-3.5 w-3.5" />
              </a>
            ) : null}
            <div className={`grid grid-cols-[1fr_auto] gap-2 ${onShelf ? "md:grid-cols-1" : ""}`}>
              <Button className={`whitespace-nowrap rounded-xl bg-white px-3 text-stone-950 hover:bg-amber-100 ${onShelf ? "md:hidden" : ""}`} disabled={pending || swipe.leaving !== null} onClick={onShelf ? () => swipe.trigger("left") : () => swipe.trigger("right")} variant="secondary">
                {onShelf ? <Check className="mr-2 h-4 w-4" /> : <BookmarkPlus className="mr-2 h-4 w-4" />}
                {onShelf ? "Mark read" : "Shelf"}
              </Button>
              <Button
                aria-controls={noteId}
                aria-expanded={noteOpen}
                aria-label={noteOpen ? "Collapse note editor" : "Expand note editor"}
                className="rounded-xl bg-white px-3 text-stone-950 hover:bg-amber-100"
                onClick={onToggleNote}
                title={noteOpen ? "Close note" : change.note.trim() ? "Edit note" : "Add note"}
                variant="secondary"
              >
                <ChevronDown className={`h-4 w-4 transition-transform ${noteOpen ? "rotate-180" : ""}`} />
              </Button>
            </div>
            {onShelf ? (
              <Button className="whitespace-nowrap rounded-xl bg-transparent px-3 text-stone-700 hover:bg-stone-950 hover:text-[#fff8e8]" disabled={pending || swipe.leaving !== null} onClick={onToggleShelf} variant="secondary">
                <Undo2 className="mr-2 h-4 w-4" />
                Back to desk
              </Button>
            ) : (
              <Button className="whitespace-nowrap rounded-xl bg-transparent px-3 text-stone-700 hover:bg-stone-950 hover:text-[#fff8e8] md:hidden" disabled={pending || swipe.leaving !== null} onClick={() => swipe.trigger("left")} variant="secondary">
                <X className="mr-2 h-4 w-4" />
                Clear from desk
              </Button>
            )}
          </div>
        </div>
      </div>

      {noteOpen ? (
        <div className="border-t border-stone-950 bg-[#fff8e8] p-4 sm:p-5" id={noteId}>
          <label className="flex items-center gap-2 text-sm font-black uppercase tracking-[0.18em] text-stone-600" htmlFor={`${noteId}-textarea`}>
            {onShelf ? <BookmarkCheck className="h-4 w-4" /> : <BookmarkPlus className="h-4 w-4" />}
            Shelf note
          </label>
          <textarea
            autoFocus
            disabled={pending || swipe.leaving !== null}
            maxLength={10000}
            className="mt-3 min-h-28 w-full rounded-2xl border border-stone-950/20 bg-white p-4 text-sm leading-6 text-stone-950 shadow-inner outline-none transition focus:border-stone-950"
            id={`${noteId}-textarea`}
            onChange={(event) => onUpdateDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing || event.repeat) return;
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                onSaveNote();
              }
              if (event.key === "Escape") {
                onToggleNote();
              }
            }}
            placeholder="Why is this worth revisiting? Add the decision, risk, or follow-up here."
            value={noteDraft}
          />
          <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs font-medium text-stone-500">{onShelf ? "Cmd/Ctrl+Enter saves. Escape closes." : "Saving a note also moves this item to the shelf. Cmd/Ctrl+Enter saves."}</p>
            <Button className="rounded-xl bg-stone-950 text-[#fff8e8] hover:bg-stone-800" disabled={pending || swipe.leaving !== null} onClick={onSaveNote}>
              {onShelf ? "Save note" : "Shelf with note"}
            </Button>
          </div>
        </div>
      ) : null}
    </article>
  );
}

function SwipeHint({ action, progress, side }: { action: SwipeAction; progress: number; side: SwipeDirection }) {
  const Icon = action.icon;
  const tone = action.tone === "danger" ? "border-red-800 bg-red-700 text-red-50" : "border-amber-950 bg-amber-300 text-amber-950";
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute top-4 z-10 inline-flex items-center gap-2 rounded-full border-2 px-4 py-2 text-sm font-black uppercase tracking-[0.18em] ${tone} ${
        side === "left" ? "right-4 -rotate-6" : "left-4 rotate-6"
      }`}
      style={{ opacity: progress, transform: `scale(${0.85 + progress * 0.25}) rotate(${side === "left" ? -6 : 6}deg)` }}
    >
      <Icon className="h-4 w-4" />
      {action.label}
    </div>
  );
}
