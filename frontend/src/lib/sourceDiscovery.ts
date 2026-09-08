export function discoveryUrl(input: string): string | null {
  const value = input.trim();
  if (!value || [...value].some((char) => char.trim() === "" || char <= " ") || value.includes("\\")) return null;

  if (value.includes("://")) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch {
      return null;
    }
  }

  const host = value.split("/")[0].split("?")[0].split("#")[0];
  if (host.includes(".")) {
    try {
      const url = new URL(`https://${value}`);
      const labels = url.hostname.split(".");
      return !url.username && !url.password && labels.length > 1 && labels.every(Boolean) ? url.href : null;
    } catch {
      return null;
    }
  }

  const parts = (value.endsWith("/") ? value.slice(0, -1) : value).split("/");
  if (parts.length !== 2) return null;
  const [owner, repo] = parts;
  const alphanumeric = "abcdefghijklmnopqrstuvwxyz0123456789";
  if (!owner || owner.length > 39 || owner.startsWith("-") || owner.endsWith("-") ||
    ![...owner.toLowerCase()].every((char) => (alphanumeric + "-").includes(char)) ||
    !repo || repo.length > 100 || repo === "." || repo === ".." ||
    ![...repo.toLowerCase()].every((char) => (alphanumeric + "-_.").includes(char))) return null;
  return `https://github.com/${owner}/${repo}`;
}
