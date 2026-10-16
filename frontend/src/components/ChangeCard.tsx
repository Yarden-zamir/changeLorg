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
  onSaveNote: () => Promise<boolean>;
  onSourceFilter: () => void;
  onToggleNote: () => void;
  onUpdateDraft: (value: string) => void;
};

function RenderedText({ value }: { value: string }) {
  if (!value.trim()) {
    return null;
  }
  return <div className="riffle-rendered text-[0.95rem] leading-7 text-ink" dangerouslySetInnerHTML={{ __html: renderedHtml(value) }} />;
}

export function ChangeCard({ change, location, focused, pending, noteDraft, noteOpen, onFocus, onClear, onToggleShelf, onSaveNote, onSourceFilter, onToggleNote, onUpdateDraft }: ChangeCardProps) {
  const preview = change.content || change.summary;
  const noteId = `note-${change.id}`;
  const onShelf = location === "shelf";
  const url = safeUrl(change.url);
  const slot = useRef<HTMLDivElement>(null);
  const collapse = useRef<Animation | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; collapse.current?.cancel(); };
  }, []);

  const leftAction: SwipeAction = onShelf
    ? { label: "Mark read", icon: Check, leaves: true, tone: "danger", run: onClear }
    : { label: "Clear", icon: X, leaves: true, tone: "danger", run: onClear };
  const rightAction: SwipeAction = onShelf
    ? { label: "Open source", icon: ExternalLink, leaves: false, tone: "accent", run: () => openChange(change) }
    : { label: "Shelf", icon: BookmarkPlus, leaves: true, tone: "accent", run: onToggleShelf };

  const swipe = useSwipe((direction: SwipeDirection) => {
    const action = direction === "left" ? leftAction : rightAction;
    if (action.leaves) {
      if (collapse.current) return true;
      const element = slot.current;
      if (!element) return false;
      const gap = element.parentElement ? parseFloat(getComputedStyle(element.parentElement).rowGap) || 0 : 0;
      const animation = element.animate([
        { height: `${element.offsetHeight}px`, marginBottom: "0px" },
        { height: "0px", marginBottom: `${-gap}px` },
      ], { duration: leaveDurationMs, easing: "ease-out", fill: "forwards" });
      collapse.current = animation;
      animation.onfinish = () => {
        void Promise.resolve(action.run()).finally(() => {
          if (mounted.current) { animation.cancel(); collapse.current = null; swipe.reset(); }
        });
      };
      return true;
    }
    action.run();
    return false;
  }, pending);

  return (
    <div ref={slot} className="min-w-0" style={{ overflowAnchor: "none" }}>
    <article
      {...swipe.handlers}
      className={`change-card group relative overflow-hidden rounded border-2 border-line bg-surface text-ink shadow-hard outline-none ${
        focused ? "ring-2 ring-accent ring-offset-2 ring-offset-canvas" : ""
      }`}
      data-change-key={change.change_key}
      aria-busy={pending}
      onFocus={onFocus}
      style={swipe.style}
      tabIndex={0}
    >
      <SwipeHint action={leftAction} progress={swipe.leftProgress} side="left" />
      <SwipeHint action={rightAction} progress={swipe.rightProgress} side="right" />

      <div className="grid gap-0 md:grid-cols-[minmax(0,1fr)_210px]">
        <div className="min-w-0 p-3 sm:p-5">
          <div className="flex flex-wrap items-center gap-2 text-xs font-extrabold uppercase tracking-[0.06em] text-muted">
            <button
              className="sticker cursor-pointer bg-info text-[0.7rem]"
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
            {change.metadata?.fallback_reason === "no_releases" && change.metadata.feed_kind === "commit" ? <span className="sticker" title="This repository has no releases. This card represents one commit.">Commit</span> : null}
          </div>

          <h3 className="mt-4 max-w-3xl text-2xl font-extrabold leading-[1.08] text-ink sm:text-[2rem]">
            {url ? (
              <a className="decoration-accent decoration-[3px] underline-offset-4 hover:underline" href={url} rel="noopener noreferrer" target="_blank">
                {change.title}
              </a>
            ) : (
              change.title
            )}
          </h3>

          {change.note.trim() && !noteOpen ? (
            <p className="mt-3 whitespace-pre-line rounded border-2 border-line bg-warning-surface px-2 py-2 text-sm leading-6 text-ink sm:mt-4 sm:px-4 sm:py-3">{change.note}</p>
          ) : null}

          {preview.trim() ? (
            <div className="scrollbar-none mt-3 max-h-72 touch-pan-y overflow-y-auto rounded border-2 border-line bg-raised px-2 py-2 sm:mt-4 sm:px-4 sm:py-3">
              <RenderedText value={preview} />
            </div>
          ) : (
            <div className="mt-4 rounded border-2 border-dashed border-line bg-raised px-4 py-3 text-sm font-medium text-muted">No preview text came back for this source.</div>
          )}
        </div>

        <div className={`flex flex-col justify-end border-t-2 border-line p-3 sm:p-4 md:justify-start md:gap-4 md:border-l-2 md:border-t-0 ${onShelf ? "bg-warning-surface" : "bg-raised"}`}>
          <Button
            className="hidden whitespace-nowrap px-3 md:inline-flex"
            disabled={pending || swipe.leaving !== null}
            onClick={() => swipe.trigger("left")}
            title={`${onShelf ? "Mark read" : "Clear from desk"} (x)`}
            variant="secondary"
          >
            {onShelf ? <Check className="mr-2 h-4 w-4" /> : <X className="mr-2 h-4 w-4" />}
            {onShelf ? "Mark read" : "Clear from desk"}
          </Button>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:mt-auto md:grid-cols-1">
            {url ? (
              <a
                className="editor-button inline-flex h-10 items-center justify-center px-3 py-0 text-sm no-underline"
                href={url}
                rel="noreferrer"
                target="_blank"
              >
                Open source
                <ExternalLink className="ml-2 h-3.5 w-3.5" />
              </a>
            ) : null}
            <div className={`grid grid-cols-[1fr_auto] gap-2 ${onShelf ? "md:grid-cols-1" : ""}`}>
              <Button className={`whitespace-nowrap px-3 ${onShelf ? "md:hidden" : ""}`} disabled={pending || swipe.leaving !== null} onClick={onShelf ? () => swipe.trigger("left") : () => swipe.trigger("right")} variant="secondary">
                {onShelf ? <Check className="mr-2 h-4 w-4" /> : <BookmarkPlus className="mr-2 h-4 w-4" />}
                {onShelf ? "Mark read" : "Shelf"}
              </Button>
              <Button
                aria-controls={noteId}
                aria-expanded={noteOpen}
                aria-label={noteOpen ? "Collapse note editor" : "Expand note editor"}
                className="px-3"
                onClick={onToggleNote}
                title={noteOpen ? "Close note" : change.note.trim() ? "Edit note" : "Add note"}
                variant="secondary"
              >
                <ChevronDown className={`h-4 w-4 transition-transform ${noteOpen ? "rotate-180" : ""}`} />
              </Button>
            </div>
            {onShelf ? (
              <Button className="whitespace-nowrap px-3" disabled={pending || swipe.leaving !== null} onClick={onToggleShelf} variant="secondary">
                <Undo2 className="mr-2 h-4 w-4" />
                Back to desk
              </Button>
            ) : (
              <Button className="whitespace-nowrap px-3 md:hidden" disabled={pending || swipe.leaving !== null} onClick={() => swipe.trigger("left")} variant="secondary">
                <X className="mr-2 h-4 w-4" />
                Clear from desk
              </Button>
            )}
          </div>
        </div>
      </div>

      {noteOpen ? (
        <div className="border-t-2 border-line bg-warning-surface p-3 sm:p-5" id={noteId}>
          <label className="flex items-center gap-2 text-sm font-extrabold uppercase tracking-[0.06em] text-muted" htmlFor={`${noteId}-textarea`}>
            {onShelf ? <BookmarkCheck className="h-4 w-4" /> : <BookmarkPlus className="h-4 w-4" />}
            Shelf note
          </label>
          <textarea
            autoFocus
            disabled={pending || swipe.leaving !== null}
            maxLength={10000}
            className="mt-3 min-h-28 w-full rounded border-2 border-line bg-surface p-4 text-sm leading-6 text-ink"
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
            <p className="text-xs font-medium text-muted">{onShelf ? "Cmd/Ctrl+Enter saves. Escape closes." : "Saving a note also moves this item to the shelf. Cmd/Ctrl+Enter saves."}</p>
            <Button disabled={pending || swipe.leaving !== null} onClick={onSaveNote}>
              {onShelf ? "Save note" : "Shelf with note"}
            </Button>
          </div>
        </div>
      ) : null}
    </article>
    </div>
  );
}

function SwipeHint({ action, progress, side }: { action: SwipeAction; progress: number; side: SwipeDirection }) {
  const Icon = action.icon;
  const tone = action.tone === "danger" ? "bg-error" : "bg-yellow";
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute top-4 z-10 inline-flex items-center gap-2 rounded border-2 border-shadow px-4 py-2 text-sm font-extrabold uppercase tracking-[0.06em] text-black shadow-sticker ${tone} ${
        side === "left" ? "right-4 -rotate-6" : "left-4 rotate-6"
      }`}
      style={{ opacity: progress, transform: `scale(${0.85 + progress * 0.25}) rotate(${side === "left" ? -6 : 6}deg)` }}
    >
      <Icon className="h-4 w-4" />
      {action.label}
    </div>
  );
}
