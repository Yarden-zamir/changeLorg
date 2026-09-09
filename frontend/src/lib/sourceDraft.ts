import type { SourceCreate } from "./api";

export type Draft = { source: SourceCreate; include: string; exclude: string };

export function stringConfig(source: SourceCreate, key: string) {
  const value = source.config[key];
  return typeof value === "string" ? value : "";
}

export function toDraft(source: SourceCreate): Draft {
  function terms(key: string) {
    const value = source.config[key];
    if (typeof value === "string") return value;
    return Array.isArray(value) ? value.filter((term): term is string => typeof term === "string").join("\n") : "";
  }
  return { source: { name: source.name, plugin: source.plugin, config: { ...source.config }, enabled: source.enabled }, include: terms("include_any"), exclude: terms("exclude_any") };
}

export function normalizedSource(value: Draft): SourceCreate {
  const config: Record<string, unknown> = { ...value.source.config, url: stringConfig(value.source, "url").trim() };
  if (value.source.plugin === "rss-atom") {
    config.include_any = value.include.split("\n").map((term) => term.trim()).filter(Boolean);
    config.exclude_any = value.exclude.split("\n").map((term) => term.trim()).filter(Boolean);
    delete config.article_path_prefix;
    delete config.exclude_path_prefixes;
    delete config.title_suffixes;
    delete config.limit;
  } else if (value.source.plugin === "html-news") {
    delete config.include_any;
    delete config.exclude_any;
    config.article_path_prefix = stringConfig(value.source, "article_path_prefix").trim();
    if (config.limit === "") delete config.limit;
    else if (typeof config.limit === "string") config.limit = Number(config.limit);
  }
  if (!config.enrichment_profile) delete config.enrichment_profile;
  return { ...value.source, name: value.source.name.trim(), config };
}

export function fetchSignature(value: Draft): string {
  const { plugin, config } = normalizedSource(value);
  delete config.profile;
  return JSON.stringify({ plugin, config }, (_key, item: unknown) => {
    if (item && typeof item === "object" && !Array.isArray(item)) {
      return Object.fromEntries(Object.entries(item).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0));
    }
    return item;
  });
}

export function draftSignature(value: Draft): string {
  return JSON.stringify([value.source.name.trim(), stringConfig(value.source, "profile"), value.source.enabled, fetchSignature(value)]);
}
