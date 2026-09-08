"use client";

/**
 * The two pictures the landing page argues with.
 *
 * Both are drawn from the committed corpus rather than invented for the
 * marketing page -- the reconciled pair below is demo case 3a in
 * docs/DEMO_CASES.md (facts 78 and 13, relationship 10), and the pipeline
 * stages are the actual steps in services/, in order. A landing page that
 * illustrates itself with fictional data is the thing this whole project is
 * arguing against.
 *
 * Inline SVG and plain divs rather than a charting dependency: these are five
 * boxes and some arrows, and the palette has to match the app's own verdict
 * colours exactly, which a chart library would fight rather than help.
 */

const PIPELINE = [
  { label: "Parse", detail: "PDF → text + layout" },
  { label: "Chunk", detail: "~900 tokens, overlapped" },
  { label: "Extract", detail: "facts, typed by the model" },
  { label: "Ground", detail: "quote → page → bbox" },
  { label: "Link", detail: "cosine → verdict" },
];

export function PipelineDiagram() {
  return (
    <div className="w-full overflow-x-auto">
      <ol className="flex min-w-[640px] items-stretch gap-2">
        {PIPELINE.map((step, i) => (
          <li key={step.label} className="flex flex-1 items-center gap-2">
            <div
              data-pipeline-node
              className="flex-1 rounded-lg border border-border bg-surface/40 px-3 py-3"
            >
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-[10px] text-accent">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="text-sm text-text">{step.label}</span>
              </div>
              <p className="mt-1 font-mono text-[10px] leading-relaxed text-text-dim">
                {step.detail}
              </p>
            </div>
            {i < PIPELINE.length - 1 && (
              <svg
                width="14"
                height="14"
                viewBox="0 0 14 14"
                aria-hidden
                className="shrink-0 text-border"
              >
                <path
                  d="M2 7h9M8 4l3 3-3 3"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function MiniFact({
  source,
  page,
  statement,
  value,
  scope,
  basis,
}: {
  source: string;
  page: string;
  statement: string;
  value: string;
  scope: string;
  basis: string;
}) {
  return (
    <div className="flex-1 rounded-lg border border-border bg-bg/60 p-3">
      <p className="font-mono text-[10px] text-text-dim">
        {source} · {page}
      </p>
      <p className="mt-2 text-[13px] leading-relaxed text-text">{statement}</p>
      <dl className="mt-3 grid grid-cols-3 gap-1.5 border-t border-border pt-2 font-mono text-[10px]">
        {[
          ["value", value],
          ["scope", scope],
          ["basis", basis],
        ].map(([k, v]) => (
          <div key={k}>
            <dt className="text-text-dim">{k}</dt>
            <dd className="mt-0.5 truncate text-text" title={v}>
              {v}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/**
 * Demo case 3a, exactly as the engine produced it: same metric, same year,
 * two numbers, and the reconciling axis is neither time nor units but what
 * the number *is*.
 */
export function ReconciledExample() {
  return (
    <figure className="rounded-xl border border-border bg-surface/30 p-4">
      <figcaption className="mb-3 flex items-center justify-between gap-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-dim">
          Live in the corpus below
        </span>
        <span className="rounded border border-accent/50 bg-accent/10 px-2 py-0.5 font-mono text-[10px] tracking-wide text-accent">
          RECONCILED
        </span>
      </figcaption>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
        <MiniFact
          source="RBI Annual Report"
          page="p6"
          statement="The global economy grew by 3.3 per cent in 2024."
          value="3.3 percent"
          scope="2024"
          basis="actual"
        />
        <div className="flex items-center justify-center sm:w-8">
          <span className="font-mono text-[10px] text-text-dim">vs</span>
        </div>
        <MiniFact
          source="Economic Survey"
          page="p5"
          statement="The IMF projected global growth of 3.2 per cent for 2024."
          value="3.2 percent"
          scope="2024"
          basis="projection"
        />
      </div>

      <div className="mt-3 rounded-lg border border-accent/30 bg-accent/[0.06] p-3">
        <p className="font-mono text-[10px] uppercase tracking-wide text-accent">
          differs by definition
        </p>
        <p className="mt-1.5 text-[13px] leading-relaxed text-text-dim">
          One is an outcome, the other a forecast. Same metric, same year, and
          not a contradiction — the engine names the axis instead of raising a
          flag a human then has to investigate.
        </p>
      </div>
    </figure>
  );
}
