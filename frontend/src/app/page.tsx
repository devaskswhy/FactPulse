import { ApiStatus } from "@/components/api-status";

export default function Home() {
  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-lg space-y-6">
        <div className="space-y-2">
          <h1 className="text-4xl font-semibold tracking-tight">
            Hello FactPulse
          </h1>
          <p className="text-sm leading-relaxed opacity-70">
            A fact knowledge layer. Extracts facts from PDFs, grounds each one in
            the source span it came from, and detects whether facts across
            documents corroborate, contradict, or can be reconciled through
            context.
          </p>
        </div>

        <ApiStatus />

        <p className="text-xs opacity-50">
          Scaffold only — the UI gets built in a later step.
        </p>
      </div>
    </main>
  );
}
