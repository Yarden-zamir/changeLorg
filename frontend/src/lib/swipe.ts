import { useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";

export type SwipeDirection = "left" | "right";

type SwipeState = {
  dx: number;
  dragging: boolean;
  leaving: SwipeDirection | null;
};

type Gesture = {
  pointerId: number;
  startX: number;
  startY: number;
  armed: boolean;
};

const armDistance = 12;
const swipeThreshold = 120;
const leaveDurationMs = 240;
const nudgeDurationMs = 160;

// Elements that own their own pointer interaction never start a swipe.
const interactiveSelector = "button, a, textarea, input, select";

/**
 * Tinder-style horizontal swipe on a card.
 * `onSwipe` returns true when the card leaves the list, false when it snaps back.
 */
export function useSwipe(onSwipe: (direction: SwipeDirection) => boolean) {
  const [state, setState] = useState<SwipeState>({ dx: 0, dragging: false, leaving: null });
  const gesture = useRef<Gesture | null>(null);

  function finish(direction: SwipeDirection) {
    const leaves = onSwipe(direction);
    if (leaves) {
      setState({ dx: 0, dragging: false, leaving: direction });
      return;
    }
    // Nudge in the swipe direction, then snap back.
    setState({ dx: direction === "left" ? -48 : 48, dragging: false, leaving: null });
    window.setTimeout(() => setState({ dx: 0, dragging: false, leaving: null }), nudgeDurationMs);
  }

  /** Run a swipe action from a button. Same animation as a real swipe. */
  function trigger(direction: SwipeDirection) {
    finish(direction);
  }

  function onPointerDown(event: ReactPointerEvent<HTMLElement>) {
    if (state.leaving || !event.isPrimary) {
      return;
    }
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest(interactiveSelector)) {
      return;
    }
    // Mouse users select text in the preview; only touch and pen swipe from there.
    if (event.pointerType === "mouse" && target?.closest("[data-swipe-ignore]")) {
      return;
    }
    gesture.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, armed: false };
  }

  function onPointerMove(event: ReactPointerEvent<HTMLElement>) {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) {
      return;
    }
    const dx = event.clientX - current.startX;
    const dy = event.clientY - current.startY;
    if (!current.armed) {
      if (Math.abs(dy) > armDistance && Math.abs(dy) > Math.abs(dx)) {
        gesture.current = null;
        return;
      }
      if (Math.abs(dx) < armDistance) {
        return;
      }
      current.armed = true;
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    setState({ dx, dragging: true, leaving: null });
  }

  function onPointerUp(event: ReactPointerEvent<HTMLElement>) {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) {
      return;
    }
    gesture.current = null;
    if (!current.armed) {
      return;
    }
    const dx = event.clientX - current.startX;
    if (Math.abs(dx) >= swipeThreshold) {
      finish(dx < 0 ? "left" : "right");
      return;
    }
    setState({ dx: 0, dragging: false, leaving: null });
  }

  function onPointerCancel(event: ReactPointerEvent<HTMLElement>) {
    if (gesture.current?.pointerId === event.pointerId) {
      gesture.current = null;
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
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel },
    style,
    leaving: state.leaving,
    leftProgress: state.leaving === "left" ? 1 : leftProgress,
    rightProgress: state.leaving === "right" ? 1 : rightProgress,
    trigger,
  };
}

export { leaveDurationMs };
