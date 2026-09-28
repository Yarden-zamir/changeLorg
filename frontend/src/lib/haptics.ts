let lastFeedback = -Infinity;
let switchLabel: HTMLLabelElement | null = null;

function switchFeedback() {
  if (typeof document === "undefined") return;
  const input = document.createElement("input");
  // Recent WebKit exposes native switch controls with their own haptic response.
  if (!("switch" in input)) return;
  if (!switchLabel) {
    switchLabel = document.createElement("label");
    switchLabel.hidden = true;
    switchLabel.setAttribute("aria-hidden", "true");
    input.type = "checkbox";
    input.setAttribute("switch", "");
    input.tabIndex = -1;
    switchLabel.appendChild(input);
    // These internal events must never become app interactions or repeat feedback.
    switchLabel.addEventListener("click", (event) => event.stopPropagation());
    switchLabel.addEventListener("change", (event) => event.stopPropagation());
  }
  const dialogs = document.querySelectorAll("dialog[open]");
  const container = dialogs[dialogs.length - 1] ?? document.body;
  if (switchLabel.parentElement !== container) container.appendChild(switchLabel);
  switchLabel.click();
}

/** Best-effort feedback; unsupported browsers keep the interaction unchanged. */
export function haptic() {
  if (typeof navigator === "undefined") return;
  const now = performance.now();
  if (now - lastFeedback < 80) return;
  lastFeedback = now;
  try {
    if (typeof navigator.vibrate === "function" && navigator.vibrate(25)) return;
    switchFeedback();
  } catch { /* Device or browser settings may reject all haptic feedback. */ }
}

export function interactionHaptic(event: Event) {
  if (event.defaultPrevented || !(event.target instanceof Element)) return;
  const control = event.target.closest(event.type === "change" ? "select, input[type=checkbox], input[type=radio]" : "button, a[href], summary");
  if (!control || control.matches(":disabled, [aria-disabled=true]") || control.closest("[inert]")) return;
  haptic();
}
