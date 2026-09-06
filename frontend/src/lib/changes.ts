import DOMPurify from "dompurify";
import { marked } from "marked";

export type Change = {
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

export type ViewChange = Change & {
  change_key: string;
};

/** Where a card lives. Decides which actions the card offers. */
export type CardLocation = "desk" | "shelf";

export function changeKey(change: Change) {
  return `${change.source_id}:${change.external_id || change.url || change.title}`;
}

export function dateValue(value: string) {
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

export function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Undated";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function renderedHtml(value: string) {
  const parsed = marked.parse(value, { async: false }) as string;
  return DOMPurify.sanitize(parsed);
}

export function openChange(change: Change) {
  if (change.url) {
    window.open(change.url, "_blank", "noopener,noreferrer");
  }
}
