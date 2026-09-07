"use client";

import type { ReactNode } from "react";

/**
 * Shared frame for the not-yet-built screens.
 *
 * Each states what it will do and what backend endpoints already serve it, so
 * the shell communicates the shape of the finished app rather than showing
 * three identical "coming soon" boxes.
 */
export function ScreenPlaceholder({
  title,
  lead,
  endpoints,
  children,
}: {
  title: string;
  lead: string;
  endpoints: string[];
  children?: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-3xl px-6 py-10 md:px-10 md:py-14">
      <p className="font-mono text-xs uppercase tracking-[0.3em] text-accent">
        {title}
      </p>
      <p className="mt-5 text-lg leading-relaxed text-text-dim">{lead}</p>

      {children}

      <div className="mt-10 rounded-lg border border-border bg-surface/50 p-5">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-text-dim">
          Backend already serves
        </p>
        <ul className="mt-3 space-y-1.5">
          {endpoints.map((endpoint) => (
            <li key={endpoint} className="font-mono text-xs text-text">
              <span className="text-text-dim">→</span> {endpoint}
            </li>
          ))}
        </ul>
      </div>

      <p className="mt-6 font-mono text-xs text-text-dim">
        Screen built in the next step.
      </p>
    </div>
  );
}
