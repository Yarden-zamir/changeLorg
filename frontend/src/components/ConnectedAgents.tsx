import { useEffect, useRef, useState } from "react";

import { api, errorMessage, type AgentToken } from "../lib/api";

// Connected agents in the GitHub account menu: frontend/API.md "Agent Access Page" 7.
export function ConnectedAgents({ owner }: { owner: string }) {
  const [tokens, setTokens] = useState<AgentToken[] | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);

  async function load() {
    try {
      setTokens(await api<AgentToken[]>("/agent/tokens", { owner }));
    } catch (cause) {
      setMessage(errorMessage(cause));
    }
  }

  useEffect(() => { void load(); }, [owner]);

  async function revoke(token: AgentToken) {
    if (lock.current || !window.confirm(`Revoke access for ${token.name}? The agent must ask for access again.`)) return;
    lock.current = true;
    setBusy(true);
    setMessage("");
    try {
      await api(`/agent/tokens/${token.id}`, { owner, method: "DELETE" });
      setMessage(`Revoked access for ${token.name}.`);
      await load();
    } catch (cause) {
      setMessage(errorMessage(cause));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  return <section className="grid gap-2" aria-label="Connected agents">
    <span className="editor-list-title">Connected agents</span>
    {tokens === null ? (message ? null : <p role="status" className="text-xs">Load connected agents...</p>)
      : tokens.length === 0 ? <p className="text-xs text-muted">No agents have access. An agent asks through a link that you approve.</p>
        : tokens.map((token) => <div key={token.id} className="flex items-start justify-between gap-2 border-b-2 border-raised pb-2">
          <div className="min-w-0 [overflow-wrap:anywhere]">
            <p className="font-semibold">{token.name}</p>
            <p className="text-xs text-muted">{token.scope === "write" ? "Read and write" : "Read only"}. Last used {new Date(token.last_used_at).toLocaleDateString()}. Ends {new Date(token.expires_at).toLocaleDateString()} without use.</p>
          </div>
          <button className="editor-button shrink-0" disabled={busy} onClick={() => void revoke(token)}>Revoke</button>
        </div>)}
    {message ? <p role="status" className="text-xs">{message}</p> : null}
  </section>;
}
