/**
 * The motion scale. One easing, three durations, imported everywhere.
 *
 * The point of exporting these rather than typing `0.6` and `'power3.inOut'`
 * at each call site is that motion reads as a system only if it is one. A
 * single one-off duration is enough to make the interface feel assembled from
 * parts. If a tween seems to need a value that is not here, that is a signal
 * to reconsider the tween, not to add a fourth number.
 *
 * Kept in sync with the CSS custom properties in globals.css, which cover the
 * few transitions cheaper to express as CSS than as a tween.
 */

export const EASE = "power3.inOut";

export const DURATION = {
  fast: 0.3,
  base: 0.6,
  slow: 1.1,
} as const;

/**
 * Stagger steps for list reveals.
 *
 * Deliberately small. These are used in the app shell, where a list of
 * documents or facts should resolve quickly enough that it never delays
 * someone trying to read it -- see the note at the top of app-shell.tsx about
 * why the app and the landing page move differently.
 */
export const STAGGER = {
  tight: 0.03,
  list: 0.05,
} as const;

/** True when the viewer has asked for reduced motion. */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
