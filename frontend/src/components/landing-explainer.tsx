"use client";

import { useLayoutEffect, useRef } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

import { DURATION, EASE, prefersReducedMotion } from "@/lib/motion";

/**
 * Registered at module scope, not left to the provider.
 *
 * The provider registers it in a useEffect; this component builds tweens in a
 * useLayoutEffect. Layout effects of children run BEFORE passive effects of
 * parents, so the tweens were being created before the plugin existed and GSAP
 * warned "Invalid property scrollTrigger ... Missing plugin?". registerPlugin
 * is idempotent, so calling it here as well simply removes the ordering
 * dependency.
 */
gsap.registerPlugin(ScrollTrigger);

/**
 * The explainer. This is the ONE part of FactPulse that scroll-jacks.
 *
 * It pins, scrubs and staggers because it is explaining a concept to someone
 * who has not yet decided to use the product. Choreography earns its keep
 * there: the four stages are sequential, and making the reader advance through
 * them one at a time is the argument, not decoration.
 *
 * None of that belongs in the app itself. See the note at the top of
 * app-shell.tsx.
 */

const STAGES = [
  {
    index: "01",
    title: "Extract",
    body: "Every checkable claim a document actually states — figures, dates, roles, obligations — pulled out as a standalone fact with its own type, value, unit and time scope.",
    detail: "fact_type is chosen by the model, not picked from a list.",
  },
  {
    index: "02",
    title: "Ground",
    body: "Each fact carries the verbatim quote that supports it, the page it sits on, and the rectangle it occupies. A quote that cannot be found in the source is flagged, not kept quietly.",
    detail: "No fact exists here without a page and a box you can point at.",
  },
  {
    index: "03",
    title: "Corroborate & contradict",
    body: "Facts from different sources are compared against each other. Two documents agreeing raises confidence; two genuinely conflicting under the same scope is surfaced as a contradiction.",
    detail: "Every verdict comes with a rationale citing both sides.",
  },
  {
    index: "04",
    title: "Reconcile",
    body: "Most apparent conflicts are not conflicts. $4.2M and $5.1M disagree until you notice one is FY2024 consolidated GAAP and the other CY2024 non-GAAP including an acquisition.",
    detail: "Different period, scope, unit or basis — named explicitly.",
  },
];

export function LandingExplainer() {
  const rootRef = useRef<HTMLElement>(null);

  useLayoutEffect(() => {
    if (prefersReducedMotion()) return;

    const context = gsap.context(() => {
      const cards = gsap.utils.toArray<HTMLElement>("[data-stage]");
      const heading = rootRef.current?.querySelector("[data-explainer-heading]");
      const rule = rootRef.current?.querySelector("[data-explainer-rule]");

      // The intro is a normal entrance, not scrubbed: it should play at its own
      // pace when it comes into view rather than being dragged by the wheel.
      gsap.from([heading, rule], {
        yPercent: 40,
        opacity: 0,
        duration: DURATION.base,
        ease: EASE,
        stagger: 0.08,
        scrollTrigger: {
          trigger: rootRef.current,
          start: "top 70%",
        },
      });

      // The pinned run. One timeline scrubbed by scroll position, so the
      // reader controls the pace in both directions -- scrubbing back up
      // reverses cleanly, which a set of independent triggers would not.
      const timeline = gsap.timeline({
        scrollTrigger: {
          trigger: "[data-explainer-pin]",
          start: "top top",
          // One viewport of scroll per stage, so each gets equal dwell time.
          end: () => `+=${window.innerHeight * STAGES.length}`,
          pin: true,
          scrub: 1, // a beat of lag, so it glides rather than snapping
          anticipatePin: 1,
          invalidateOnRefresh: true,
        },
      });

      cards.forEach((card, index) => {
        const lines = card.querySelectorAll("[data-stage-line]");

        timeline
          .fromTo(
            card,
            { autoAlpha: 0, yPercent: 12 },
            { autoAlpha: 1, yPercent: 0, duration: 1, ease: EASE },
            index,
          )
          .fromTo(
            lines,
            { autoAlpha: 0, y: 24 },
            { autoAlpha: 1, y: 0, duration: 0.7, ease: EASE, stagger: 0.12 },
            index + 0.1,
          );

        // Every stage but the last leaves before the next arrives, so only one
        // is legible at a time.
        if (index < STAGES.length - 1) {
          timeline.to(
            card,
            { autoAlpha: 0, yPercent: -12, duration: 0.8, ease: EASE },
            index + 0.75,
          );
        }
      });

      // The progress rail tracks the same timeline, so it can never disagree
      // with what is on screen.
      timeline.fromTo(
        "[data-explainer-progress]",
        { scaleY: 0 },
        { scaleY: 1, duration: STAGES.length, ease: "none" },
        0,
      );
    }, rootRef);

    return () => context.revert();
  }, []);

  return (
    <section ref={rootRef} className="relative bg-bg">
      {/* Intro */}
      <div className="mx-auto flex min-h-[70vh] max-w-5xl flex-col justify-center px-6 py-24 md:px-10">
        <div data-explainer-heading>
          <p className="font-mono text-xs uppercase tracking-[0.3em] text-accent">
            The fact knowledge layer
          </p>
          <h1 className="mt-6 max-w-3xl text-4xl leading-[1.1] tracking-tight text-text md:text-6xl">
            Two documents disagree.{" "}
            <span className="text-text-dim">
              The useful answer is almost never
            </span>{" "}
            &ldquo;contradiction&rdquo;.
          </h1>
        </div>
        <div
          data-explainer-rule
          className="mt-10 flex items-center gap-4 text-sm text-text-dim"
        >
          <span className="h-px w-16 bg-border" />
          <span>Scroll to see how FactPulse tells them apart</span>
        </div>
      </div>

      {/* Pinned run */}
      <div
        data-explainer-pin
        className="relative flex h-screen items-center overflow-hidden"
      >
        {/* Progress rail */}
        <div className="absolute left-6 top-1/2 hidden h-56 w-px -translate-y-1/2 bg-border md:block">
          <div
            data-explainer-progress
            className="h-full w-full origin-top bg-accent"
            style={{ transform: "scaleY(0)" }}
          />
        </div>

        <div className="relative mx-auto w-full max-w-5xl px-6 md:px-20">
          {STAGES.map((stage) => (
            <article
              key={stage.index}
              data-stage
              className="absolute inset-x-6 top-1/2 -translate-y-1/2 md:inset-x-20"
              style={{ opacity: 0, visibility: "hidden" }}
            >
              <div
                data-stage-line
                className="flex items-center gap-4 font-mono text-xs uppercase tracking-[0.3em] text-accent"
              >
                <span>{stage.index}</span>
                <span className="h-px w-10 bg-accent/40" />
              </div>

              <h2
                data-stage-line
                className="mt-6 text-3xl tracking-tight text-text md:text-5xl"
              >
                {stage.title}
              </h2>

              <p
                data-stage-line
                className="mt-6 max-w-2xl text-lg leading-relaxed text-text-dim md:text-xl"
              >
                {stage.body}
              </p>

              <p
                data-stage-line
                className="mt-8 border-l border-border pl-4 font-mono text-sm text-text-dim"
              >
                {stage.detail}
              </p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
