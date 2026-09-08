"use client";

import { useLayoutEffect, useRef } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

import { DURATION, EASE, prefersReducedMotion } from "@/lib/motion";
import { PipelineDiagram, ReconciledExample } from "@/components/landing-visuals";

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
 * The explainer: what this is, shown next to an example of it working.
 *
 * Two earlier versions of this page were wrong in opposite directions. The
 * first pinned the viewport and scrubbed one stage per screen-height of
 * scroll, which produced long stretches where nothing happened and a blank
 * frame between stages. The second unpinned it but kept everything in one
 * narrow centred column, so on any normal monitor the text ran down the left
 * third and the remaining two thirds were empty -- and then the app below,
 * which is full-width and dense, arrived like a different website.
 *
 * This version matches the app's own width and fills it: the argument on the
 * left, a real reconciled pair from the corpus on the right, the pipeline as
 * a diagram rather than a paragraph, and the four stages as a grid instead of
 * a column. Same content, roughly half the scroll, no dead space beside it.
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
    title: "Reconcile & supersede",
    body: "Most apparent conflicts are not conflicts. $4.2M and $5.1M disagree until you notice one is FY2024 consolidated GAAP and the other CY2024 non-GAAP — and a resigned director is not a contradiction, it is a change.",
    detail: "Different period, scope, unit, basis — or simply a later date.",
  },
];

export function LandingExplainer() {
  const rootRef = useRef<HTMLElement>(null);

  useLayoutEffect(() => {
    if (prefersReducedMotion()) return;

    const context = gsap.context(() => {
      // Hero: plays once on load rather than on scroll -- it is already in
      // view, and waiting for a scroll trigger that will never fire would
      // leave it invisible.
      gsap.from("[data-hero-line]", {
        y: 24,
        opacity: 0,
        duration: DURATION.base,
        ease: EASE,
        stagger: 0.08,
      });
      gsap.from("[data-hero-visual]", {
        opacity: 0,
        y: 24,
        duration: DURATION.slow,
        ease: EASE,
        delay: 0.15,
      });

      gsap.from("[data-pipeline-node]", {
        opacity: 0,
        y: 12,
        duration: DURATION.fast,
        ease: EASE,
        stagger: 0.06,
        scrollTrigger: { trigger: "[data-pipeline]", start: "top 85%" },
      });

      // Each stage card reveals independently as it crosses into view. No
      // pin, no shared timeline, so nothing can leave the screen blank.
      gsap.utils.toArray<HTMLElement>("[data-stage]").forEach((card) => {
        gsap.from(card, {
          opacity: 0,
          y: 24,
          duration: DURATION.base,
          ease: EASE,
          scrollTrigger: { trigger: card, start: "top 88%" },
        });
      });
    }, rootRef);

    return () => context.revert();
  }, []);

  return (
    <section ref={rootRef} className="relative bg-bg">
      <div className="mx-auto max-w-7xl px-6 py-16 md:px-10 md:py-24">
        {/* Hero: argument left, evidence right. */}
        <div className="grid items-center gap-10 lg:grid-cols-[1.05fr_1fr] lg:gap-16">
          <div>
            <p
              data-hero-line
              className="font-mono text-xs uppercase tracking-[0.3em] text-accent"
            >
              The fact knowledge layer
            </p>
            <h1
              data-hero-line
              className="mt-5 text-4xl leading-[1.08] tracking-tight text-text md:text-6xl"
            >
              Two documents disagree.{" "}
              <span className="text-text-dim">
                The useful answer is almost never
              </span>{" "}
              &ldquo;contradiction&rdquo;.
            </h1>
            <p
              data-hero-line
              className="mt-6 max-w-xl text-base leading-relaxed text-text-dim md:text-lg"
            >
              FactPulse extracts every checkable claim from a PDF, pins each one
              to the exact rectangle it came from, and works out whether facts
              across documents agree, genuinely conflict, or only look like they
              do.
            </p>
            <div data-hero-line className="mt-8 flex flex-wrap items-center gap-3">
              <a
                href="#app"
                className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-bg transition-opacity duration-200 hover:opacity-90"
              >
                Explore the corpus ↓
              </a>
              <a
                href="https://github.com/devaskswhy/FactPulse"
                target="_blank"
                rel="noreferrer"
                className="rounded-md border border-border px-4 py-2 font-mono text-sm text-text-dim transition-colors duration-200 hover:border-text-dim hover:text-text"
              >
                Source on GitHub
              </a>
            </div>
          </div>

          <div data-hero-visual className="min-w-0">
            <ReconciledExample />
          </div>
        </div>

        {/* The pipeline, as a diagram rather than a paragraph. */}
        <div data-pipeline className="mt-20 md:mt-28">
          <p className="mb-4 font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim">
            What happens to a document
          </p>
          <PipelineDiagram />
        </div>

        {/* Four stages, as a grid. */}
        <div className="mt-16 grid gap-5 md:mt-20 md:grid-cols-2">
          {STAGES.map((stage) => (
            <article
              key={stage.index}
              data-stage
              className="rounded-xl border border-border bg-surface/20 p-6 transition-colors duration-200 hover:border-text-dim/40"
            >
              <div className="flex items-center gap-3 font-mono text-xs uppercase tracking-[0.3em] text-accent">
                <span>{stage.index}</span>
                <span className="h-px w-8 bg-accent/40" />
              </div>
              <h2 className="mt-4 text-xl tracking-tight text-text md:text-2xl">
                {stage.title}
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-text-dim">
                {stage.body}
              </p>
              <p className="mt-4 border-l border-border pl-3 font-mono text-xs leading-relaxed text-text-dim">
                {stage.detail}
              </p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
