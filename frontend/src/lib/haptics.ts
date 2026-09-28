let lastFeedback = -Infinity;

/** Best-effort feedback; unsupported browsers keep the interaction unchanged. */
export function haptic() {
  if (typeof navigator === "undefined" || typeof navigator.vibrate !== "function") return;
  const now = performance.now();
  if (now - lastFeedback < 80) return;
  lastFeedback = now;
  try { navigator.vibrate(10); } catch { /* Browser policy may reject vibration. */ }
}

export function interactionHaptic(event: Event) {
  if (event.defaultPrevented || !(event.target instanceof Element)) return;
  const control = event.target.closest(event.type === "change" ? "select, input[type=checkbox], input[type=radio]" : "button, a[href], summary");
  if (!control || control.matches(":disabled, [aria-disabled=true]") || control.closest("[inert]")) return;
  haptic();
}
