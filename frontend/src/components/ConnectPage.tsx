import { useEffect, useRef, useState } from "react";

import { ErrorNote } from "./ErrorNote";
import { api, ApiError, apiUrl, errorMessage, getIdentity, type AgentRequest, type Identity } from "../lib/api";

// Agent approval page: frontend/API.md "Agent Access Page".
function requestError(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 404) return "This code is unknown or expired. Start again from your agent.";
  if (cause instanceof ApiError && cause.status === 409) return "This request already has a decision. Reload to see it.";
  return errorMessage(cause);
}

export function ConnectPage() {
  const [code, setCode] = useState(() => new URLSearchParams(window.location.search).get("code")?.trim() ?? "");
  const [draft, setDraft] = useState("");
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [request, setRequest] = useState<AgentRequest | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const lock = useRef(false);

  useEffect(() => {
    let active = true;
    getIdentity().then((value) => { if (active) setIdentity(value); }, (cause: unknown) => { if (active) setError(errorMessage(cause)); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!identity?.authenticated || !code) return;
    let active = true;
    setRequest(null);
    setError("");
    api<AgentRequest>(`/agent/device/${encodeURIComponent(code)}`, { owner: identity.id }).then(
      (value) => { if (active) setRequest(value); },
      (cause: unknown) => { if (active) setError(requestError(cause)); },
    );
    return () => { active = false; };
  }, [identity, code]);

  async function decide(decision: "approve" | "deny") {
    if (lock.current || !identity || !request) return;
    lock.current = true;
    setPending(true);
    setError("");
    try {
      setRequest(await api<AgentRequest>(`/agent/device/${encodeURIComponent(code)}/${decision}`, { owner: identity.id, method: "POST" }));
    } catch (cause) {
      setError(requestError(cause));
    } finally {
      lock.current = false;
      setPending(false);
    }
  }

  function submitCode() {
    const value = draft.trim();
    if (!value) return;
    window.history.replaceState(null, "", `/connect?code=${encodeURIComponent(value)}`);
    setCode(value);
  }

  const signIn = `${apiUrl}/auth/start?rd=${encodeURIComponent(window.location.pathname + window.location.search)}`;

  return <div className="min-h-screen bg-[#ece7db] px-1 py-2 text-stone-950 sm:px-5 sm:py-4 lg:px-8">
    <section className="mx-auto grid max-w-2xl gap-4 py-10">
      <h1 className="font-serif text-4xl font-black">changelorg<span className="text-amber-800">.</span></h1>
      <h2 className="font-serif text-2xl font-bold">Agent access</h2>
      {!identity ? (error ? <>
        <ErrorNote message={error} />
        <a className="editor-button editor-primary w-fit" href={signIn}>Sign in with GitHub again</a>
      </> : <p role="status">Load your account...</p>)
        : !identity.authenticated ? <>
          <p className="leading-7">An agent asks for read access to your changelorg account. Sign in with GitHub to review the request.</p>
          {identity.auth_enabled
            ? <a className="editor-button editor-primary w-fit" href={signIn}>Sign in with GitHub</a>
            : <p className="text-stone-600">GitHub sign-in is not available on this server, so agents cannot get access here.</p>}
        </>
          : !code ? <form className="grid gap-3" onSubmit={(event) => { event.preventDefault(); submitCode(); }}>
            <label className="grid gap-2 font-semibold">Enter the code that your agent shows
              <input className="rounded-xl border border-stone-950/25 bg-[#fffaf0] px-3 py-2 font-mono text-lg uppercase tracking-widest" autoComplete="off" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="XXXX-XXXX" />
            </label>
            <button className="editor-button editor-primary w-fit" type="submit">Continue</button>
          </form>
            : <>
              {request ? <div className="grid gap-3 rounded-2xl border border-stone-950/20 bg-[#fffaf0] p-5 shadow-sm">
                <p className="text-lg leading-7"><strong>{request.client_name}</strong> asks to read your changelorg account.</p>
                <p className="font-mono text-3xl font-bold tracking-widest">{request.user_code}</p>
                <p className="text-sm leading-6">Compare this code with the code in your agent's output. Approve only a request that you started yourself. Another person can send you a link like this.</p>
                <ul className="list-disc pl-5 text-sm leading-6">
                  <li>It can read your profiles, sources, changes, shelf flags, and notes.</li>
                  <li>It cannot change or delete anything.</li>
                  <li>Access ends 90 days after the agent last uses it. You can revoke it earlier in the GitHub account menu.</li>
                </ul>
                <p className="text-xs text-stone-600">Signed in as {identity.login}. This request expires at {new Date(request.expires_at).toLocaleTimeString()}.</p>
                {request.status === "pending" ? <div className="flex flex-wrap gap-3">
                  <button className="editor-button editor-primary" disabled={pending} onClick={() => void decide("approve")}>Approve</button>
                  <button className="editor-button" disabled={pending} onClick={() => void decide("deny")}>Deny</button>
                </div> : <p role="status" className="font-semibold">{request.status === "approved"
                  ? "Approved. Return to your agent. It continues on its own."
                  : "Denied. The agent gets no access."}</p>}
                {pending ? <p role="status" className="text-sm">Save the decision...</p> : null}
              </div> : error ? null : <p role="status">Load the request...</p>}
              {error ? <ErrorNote message={error} /> : null}
            </>}
      <a className="w-fit text-sm underline underline-offset-4" href="/">Open your desk</a>
    </section>
  </div>;
}
