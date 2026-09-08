import { AppShell } from "@/components/app-shell";
import { LandingExplainer } from "@/components/landing-explainer";
import { LandingProof } from "@/components/landing-proof";
import { Preloader } from "@/components/preloader";

/**
 * One page, three parts with deliberately different jobs.
 *
 * The explainer pitches the idea. LandingProof backs it with numbers read
 * live from the same database the app below queries -- pitch, then receipts,
 * then the tool itself. The seam into the app is a hard visual break rather
 * than a fade, so it reads as arriving somewhere new rather than as more of
 * the same page.
 */
export default function Home() {
  return (
    <>
      <Preloader />
      <LandingExplainer />
      <LandingProof />
      <div id="app" className="border-t border-border">
        <AppShell />
      </div>
    </>
  );
}
