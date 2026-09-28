import { useEffect, useRef, useState, type CSSProperties, type DragEvent, type PointerEvent as ReactPointerEvent } from "react";

export type SwipeDirection = "left" | "right";

type SwipeState = {
  dx: number;
  dragging: boolean;
  leaving: SwipeDirection | null;
};

type Gesture = {
  pointerId: number;
  element: HTMLElement;
  startX: number;
  startY: number;
  lastX: number;
  lastTime: number;
  velocity: number;
  armed: boolean;
};

/** Movement radius before the gesture picks a direction. Small jitter never decides anything. */
const slopDistance = 10;
/**
 * A swipe must be at least this many times more horizontal than vertical.
 * 1 means 45 degrees, the same split browsers use for `touch-action: pan-y`.
 * Anything steeper is a browser scroll; anything flatter must be a swipe or it becomes a dead zone.
 */
const horizontalRatio = 1;
const swipeThreshold = 120;
/** A fast flick counts before reaching the distance threshold. */
const flickVelocity = 0.6;
const flickMinDistance = 48;
const leaveDurationMs = 240;
const nudgeDurationMs = 160;

// Controls that own their own pointer interaction never start a swipe. Everything else on the card does,
// links and preview text included: a click or tap still works because a swipe only arms after movement.
// Known limit: a horizontal mouse drag over preview text swipes instead of selecting. Vertical selection still works.
const controlSelector = "button, textarea, input, select";

// Once a swipe is armed the page must not scroll under the card. React registers touch listeners as passive,
// so this native non-passive listener is the only way to cancel the browser pan.
function blockTouchScroll(event: TouchEvent) {
  if (event.cancelable) {
    event.preventDefault();
  }
}

/**
 * Tinder-style horizontal swipe on a card.
 * `onSwipe` returns true when the card leaves the list, false when it snaps back.
 */
export function useSwipe(onSwipe: (direction: SwipeDirection) => boolean, disabled = false) {
  const [state, setState] = useState<SwipeState>({ dx: 0, dragging: false, leaving: null });
  const gesture = useRef<Gesture | null>(null);
  const nudgeTimer = useRef<number | null>(null);

  useEffect(() => () => {
    endGesture();
    if (nudgeTimer.current !== null) window.clearTimeout(nudgeTimer.current);
  }, []);

  function endGesture() {
    const current = gesture.current;
    if (current) {
      current.element.removeEventListener("touchmove", blockTouchScroll);
      if (current.armed && current.element.hasPointerCapture(current.pointerId)) {
        current.element.releasePointerCapture(current.pointerId);
      }
    }
    gesture.current = null;
  }

  function finish(direction: SwipeDirection) {
    if (disabled) {
      setState({ dx: 0, dragging: false, leaving: null });
      return;
    }
    if (nudgeTimer.current !== null) window.clearTimeout(nudgeTimer.current);
    const leaves = onSwipe(direction);
    if (leaves) {
      setState({ dx: 0, dragging: false, leaving: direction });
      return;
    }
    // The action may already have removed the card optimistically.
    setState({ dx: direction === "left" ? -48 : 48, dragging: false, leaving: null });
    nudgeTimer.current = window.setTimeout(() => setState({ dx: 0, dragging: false, leaving: null }), nudgeDurationMs);
  }

  /** Run a swipe action from a button. Same animation as a real swipe. */
  function trigger(direction: SwipeDirection) {
    if (disabled || state.leaving) return;
    finish(direction);
  }

  function onPointerDown(event: ReactPointerEvent<HTMLElement>) {
    if (disabled || state.leaving || !event.isPrimary || gesture.current) {
      return;
    }
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest(controlSelector)) {
      return;
    }
    gesture.current = {
      pointerId: event.pointerId,
      element: event.currentTarget,
      startX: event.clientX,
      startY: event.clientY,
      lastX: event.clientX,
      lastTime: event.timeStamp,
      velocity: 0,
      armed: false,
    };
  }

  function onPointerMove(event: ReactPointerEvent<HTMLElement>) {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) {
      return;
    }
    const dx = event.clientX - current.startX;
    const dy = event.clientY - current.startY;
    if (!current.armed) {
      if (Math.hypot(dx, dy) < slopDistance) {
        return;
      }
      // One decision per gesture: mostly horizontal swipes, everything else scrolls.
      if (Math.abs(dx) < Math.abs(dy) * horizontalRatio) {
        endGesture();
        return;
      }
      current.armed = true;
      // A mouse drag has started a text selection by now. Drop it so the card, not the text, follows the pointer.
      window.getSelection()?.removeAllRanges();
      current.element.setPointerCapture(event.pointerId);
      current.element.addEventListener("touchmove", blockTouchScroll, { passive: false });
    }
    const elapsed = event.timeStamp - current.lastTime;
    if (elapsed > 0) {
      current.velocity = (event.clientX - current.lastX) / elapsed;
      current.lastX = event.clientX;
      current.lastTime = event.timeStamp;
    }
    setState({ dx, dragging: true, leaving: null });
  }

  function onPointerUp(event: ReactPointerEvent<HTMLElement>) {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) {
      return;
    }
    const armed = current.armed;
    const velocity = current.velocity;
    endGesture();
    if (!armed) {
      return;
    }
    const dx = event.clientX - current.startX;
    const flick = Math.abs(dx) >= flickMinDistance && Math.abs(velocity) >= flickVelocity && Math.sign(velocity) === Math.sign(dx);
    if (Math.abs(dx) >= swipeThreshold || flick) {
      finish(dx < 0 ? "left" : "right");
      return;
    }
    setState({ dx: 0, dragging: false, leaving: null });
  }

  // Links and images start a native drag, which cancels the pointer stream. Never let that happen inside a card.
  function onDragStart(event: DragEvent<HTMLElement>) {
    event.preventDefault();
  }

  function onPointerCancel(event: ReactPointerEvent<HTMLElement>) {
    if (gesture.current?.pointerId === event.pointerId) {
      endGesture();
      setState({ dx: 0, dragging: false, leaving: null });
    }
  }

  const leavingX = state.leaving === "left" ? "-120vw" : "120vw";
  const style: CSSProperties = {
    touchAction: "pan-y",
    userSelect: state.dragging ? "none" : undefined,
    transform: state.leaving ? `translateX(${leavingX}) rotate(${state.leaving === "left" ? -14 : 14}deg)` : `translateX(${state.dx}px) rotate(${state.dx / 24}deg)`,
    opacity: state.leaving ? 0 : 1,
    transition: state.dragging ? "none" : `transform ${state.leaving ? leaveDurationMs : nudgeDurationMs}ms ease-out, opacity ${leaveDurationMs}ms ease-out`,
  };

  // 0..1 progress towards the threshold, per side. Drives the action hint overlays.
  const leftProgress = Math.min(1, Math.max(0, -state.dx / swipeThreshold));
  const rightProgress = Math.min(1, Math.max(0, state.dx / swipeThreshold));

  return {
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel, onDragStart },
    style,
    leaving: state.leaving,
    leftProgress: state.leaving === "left" ? 1 : leftProgress,
    rightProgress: state.leaving === "right" ? 1 : rightProgress,
    trigger,
    reset: () => setState({ dx: 0, dragging: false, leaving: null }),
  };
}

export { leaveDurationMs };
