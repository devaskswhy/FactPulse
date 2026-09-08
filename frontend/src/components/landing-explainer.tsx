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
 * The explainer: four ideas, in order, each revealed as it scrolls into view.
 *
 * This used to pin the viewport and scrub through the four stages on a
 * dedicated timeline -- one full screen-height of scroll consumed per stage
 * regardless of how little text that stage held. In practice that produced
 * exactly what it sounds like: long stretches of scroll where nothing was
 * happening, a stage's few lines of text stranded in the middle of an
 * otherwise empty frame, and on the transition between stages a genuinely
 * blank screen while the outgoing card had faded and the incoming one had
 * not yet arrived. Choreography that shows a reader nothing is not
 * choreography, it is friction.
 *
 * A plain scroll-reveal fixes the actual problem instead of tuning the old
 * timeline's offsets: each stage sits in normal document flow, sized to its
 * own content, and fades up once as it crosses into view. Total scroll
 * distance is now however long four short paragraphs actually take to read,
 * not four multiples of the viewport height.
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
      // Intro: a normal entrance, plays once as it comes into view.
      gsap.from("[data-explainer-heading], [data-explainer-rule]", {
        yPercent: 30,
        opacity: 0,
        duration: DURATION.base,
        ease: EASE,
        stagger: 0.08,
        scrollTrigger: {
          trigger: rootRef.current,
          start: "top 75%",
        },
      });

      // Each stage fades up once as it crosses into view. No pin, no scrub,
      // no shared timeline -- every card is independent, so there is nothing
      // for one stage's animation to owe another and nothing that can leave
      // the screen blank between them.
      gsap.utils.toArray<HTMLElement>("[data-stage]").forEach((card) => {
        gsap.from(card, {
          opacity: 0,
          y: 32,
          duration: DURATION.base,
          ease: EASE,
          scrollTrigger: {
            trigger: card,
            start: "top 80%",
          },
        });
      });
    }, rootRef);

    return () => context.revert();
  }, []);

  return (
    <section ref={rootRef} className="relative bg-bg">
      <div className="mx-auto max-w-4xl px-6 py-20 md:px-10 md:py-28">
        {/* Intro */}
        <div data-explainer-heading>
          <p className="font-mono text-xs uppercase tracking-[0.3em] text-accent">
            The fact knowledge layer
          </p>
          <h1 className="mt-5 max-w-2xl text-3xl leading-[1.15] tracking-tight text-text md:text-5xl">
            Two documents disagree.{" "}
            <span className="text-text-dim">
              The useful answer is almost never
            </span>{" "}
            &ldquo;contradiction&rdquo;.
          </h1>
        </div>
        <div
          data-explainer-rule
          className="mt-8 flex items-center gap-4 text-sm text-text-dim"
        >
          <span className="h-px w-12 bg-border" />
          <span>How FactPulse tells them apart</span>
        </div>

        {/* Stages, in normal flow -- each sized to its own content. */}
        <div className="mt-16 space-y-14 md:mt-20 md:space-y-20">
          {STAGES.map((stage) => (
            <article key={stage.index} data-stage className="max-w-2xl">
              <div className="flex items-center gap-4 font-mono text-xs uppercase tracking-[0.3em] text-accent">
                <span>{stage.index}</span>
                <span className="h-px w-10 bg-accent/40" />
              </div>
              <h2 className="mt-4 text-2xl tracking-tight text-text md:text-4xl">
                {stage.title}
              </h2>
              <p className="mt-4 text-base leading-relaxed text-text-dim md:text-lg">
                {stage.body}
              </p>
              <p className="mt-5 border-l border-border pl-4 font-mono text-sm text-text-dim">
                {stage.detail}
              </p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
