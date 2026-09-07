"use client";

import { useEffect, useRef, useState } from "react";
import { gsap } from "gsap";

import { DURATION, EASE, prefersReducedMotion } from "@/lib/motion";
import { useSmoothScroll } from "@/components/smooth-scroll-provider";

/**
 * First-load preloader: a 0-100 counter, then a wipe.
 *
 * Two deliberate choices.
 *
 * FIRST LOAD ONLY. It is held in sessionStorage, not localStorage: someone
 * returning tomorrow should see it again (it is the front door), but someone
 * navigating back to the tab five minutes later should not. A preloader that
 * replays on every route change stops being an entrance and becomes an
 * obstacle.
 *
 * MINIMUM DISPLAY TIME. The counter runs for at least MIN_DISPLAY_MS even if
 * the app is ready sooner. That is not padding for its own sake -- a wipe that
 * fires at 200ms reads as a flash of broken layout rather than an intro, and
 * on a fast connection that is exactly what would happen.
 */

const MIN_DISPLAY_MS = 1500;
const SESSION_KEY = "factpulse:preloaded";

export function Preloader() {
  const rootRef = useRef<HTMLDivElement>(null);
  const counterRef = useRef<HTMLSpanElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const { stop, start } = useSmoothScroll();

  // Assume it shows, then decide on mount. Rendering nothing first and
  // switching it on would flash the page underneath.
  const [show, setShow] = useState(true);

  useEffect(() => {
    // Read, but do not WRITE, the flag here. Writing it on start meant
    // StrictMode's second effect invocation in development read it back as
    // "already seen" and hid the preloader within a frame -- invisible in dev,
    // fine in production, which is the worst way for a bug to behave. It is
    // written in onComplete instead, so a reload part-way through still gets
    // the intro it never actually saw.
    const seen = window.sessionStorage.getItem(SESSION_KEY) === "1";

    if (seen || prefersReducedMotion()) {
      // sessionStorage and the reduced-motion query are client-only, so the
      // server must render the preloader and the client must take it down.
      // Deriving this during render would be a hydration mismatch.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShow(false);
      document.body.style.removeProperty("overflow");
      return;
    }

    // Hold scroll while it is up, so a scroll during the intro does not land
    // the viewer halfway down the explainer.
    stop();
    document.body.style.overflow = "hidden";

    const counter = { value: 0 };
    const timeline = gsap.timeline();

    timeline
      .to(counter, {
        value: 100,
        duration: MIN_DISPLAY_MS / 1000,
        ease: EASE,
        onUpdate: () => {
          const n = Math.round(counter.value);
          if (counterRef.current) {
            counterRef.current.textContent = String(n).padStart(3, "0");
          }
          if (barRef.current) {
            barRef.current.style.transform = `scaleX(${n / 100})`;
          }
        },
      })
      // A beat at 100 before the wipe. Without it the counter appears to jump
      // from 98 straight into the transition.
      .to({}, { duration: 0.15 })
      .to(rootRef.current, {
        yPercent: -100,
        duration: DURATION.base,
        ease: EASE,
        onComplete: () => {
          // Recorded on completion, not on start: a reload part-way through
          // should still get the intro it never actually saw.
          window.sessionStorage.setItem(SESSION_KEY, "1");
          setShow(false);
          document.body.style.removeProperty("overflow");
          start();
        },
      });

    return () => {
      timeline.kill();
      document.body.style.removeProperty("overflow");
      start();
    };
  }, [stop, start]);

  if (!show) return null;

  return (
    <div
      ref={rootRef}
      className="fixed inset-0 z-[100] flex flex-col justify-between bg-bg px-8 py-10 md:px-14 md:py-12"
      aria-hidden
    >
      <div className="flex items-baseline gap-3">
        <span className="size-2 rounded-full bg-accent" />
        <span className="font-mono text-xs uppercase tracking-[0.3em] text-text-dim">
          FactPulse
        </span>
      </div>

      <div className="flex flex-col gap-6">
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-text-dim">
          indexing knowledge layer<span className="animate-pulse">...</span>
        </p>

        <div className="flex items-end justify-between gap-8">
          <span
            ref={counterRef}
            className="font-mono text-[18vw] leading-[0.8] tracking-tighter text-text md:text-[12vw]"
          >
            000
          </span>
          <span className="mb-2 font-mono text-sm text-text-dim md:mb-4">%</span>
        </div>

        {/* The bar restates the counter spatially. On a wide screen the
            numerals alone give little sense of how far along things are. */}
        <div className="h-px w-full bg-border">
          <div
            ref={barRef}
            className="h-full origin-left bg-accent"
            style={{ transform: "scaleX(0)" }}
          />
        </div>
      </div>
    </div>
  );
}
