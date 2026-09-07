"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import Lenis from "lenis";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

import { prefersReducedMotion } from "@/lib/motion";

/**
 * Lenis smooth scroll, wired to ScrollTrigger.
 *
 * The wiring matters and is easy to get subtly wrong. Lenis takes over
 * scrolling with its own RAF loop and transforms; ScrollTrigger, left alone,
 * still reads native scroll position and ends up a frame behind — pinned
 * sections jitter and scrubbed timelines lag the cursor. Three things keep
 * them in step:
 *
 *   1. lenis.on('scroll', ScrollTrigger.update) — ScrollTrigger recalculates
 *      on Lenis's schedule rather than the browser's.
 *   2. GSAP's ticker drives Lenis's raf, so both run on one loop instead of
 *      two competing ones.
 *   3. lagSmoothing(0) — GSAP's lag correction skips ahead after a slow frame,
 *      which desynchronises a scrubbed timeline from actual scroll position.
 */

type SmoothScrollValue = {
  /**
   * Read the live instance. A function, not a field.
   *
   * Exposing `lenis: lenisRef.current` on the context object meant reading a
   * ref during render: consumers captured `null` on the first render and were
   * never re-rendered when the instance appeared, so anyone who held onto it
   * held onto null forever. A getter defers the read to call time, which is
   * when the answer is actually known.
   */
  getLenis: () => Lenis | null;
  /** True once Lenis and ScrollTrigger are live and safe to build tweens against. */
  ready: boolean;
  stop: () => void;
  start: () => void;
};

const SmoothScrollContext = createContext<SmoothScrollValue>({
  getLenis: () => null,
  ready: false,
  stop: () => {},
  start: () => {},
});

export function useSmoothScroll() {
  return useContext(SmoothScrollContext);
}

export function SmoothScrollProvider({ children }: { children: ReactNode }) {
  const lenisRef = useRef<Lenis | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    gsap.registerPlugin(ScrollTrigger);

    // Reduced motion: no smooth scroll, no RAF loop. ScrollTrigger still
    // registers so pinning works, it just reads native scroll.
    if (prefersReducedMotion()) {
      // Readiness is only knowable on the client, after registration; no
      // render path can compute it.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setReady(true);
      return () => {
        ScrollTrigger.getAll().forEach((t) => t.kill());
      };
    }

    const lenis = new Lenis({
      duration: 1.1,
      // Matches the feel of power3.inOut without being that curve — Lenis
      // wants a decaying function, not an in-out one, or scrolling feels like
      // it accelerates away from the wheel.
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      smoothWheel: true,
      // Touch devices have their own momentum; overriding it feels broken.
      syncTouch: false,
    });
    lenisRef.current = lenis;

    lenis.on("scroll", ScrollTrigger.update);

    const raf = (time: number) => {
      // GSAP's ticker is in milliseconds, Lenis wants the same.
      lenis.raf(time * 1000);
    };
    gsap.ticker.add(raf);
    gsap.ticker.lagSmoothing(0);

    // As above: client-only readiness.
    setReady(true);

    return () => {
      gsap.ticker.remove(raf);
      gsap.ticker.lagSmoothing(500, 33); // GSAP's default
      lenis.destroy();
      lenisRef.current = null;
      ScrollTrigger.getAll().forEach((t) => t.kill());
    };
  }, []);

  // Memoised so the object identity is stable across renders; the callbacks
  // read the ref when called, never during render.
  const value = useMemo<SmoothScrollValue>(
    () => ({
      getLenis: () => lenisRef.current,
      ready,
      stop: () => lenisRef.current?.stop(),
      start: () => lenisRef.current?.start(),
    }),
    [ready],
  );

  return (
    <SmoothScrollContext.Provider value={value}>
      {children}
    </SmoothScrollContext.Provider>
  );
}
