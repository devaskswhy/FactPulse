import { AppShell } from "@/components/app-shell";
import { LandingExplainer } from "@/components/landing-explainer";
import { Preloader } from "@/components/preloader";

/**
 * One page, two halves with deliberately different personalities.
 *
 * The explainer scrolls and pins; the app below does not. The seam between
 * them is a hard visual break rather than a fade, so it reads as arriving
 * somewhere new rather than as more of the same page.
 */
export default function Home() {
  return (
    <>
      <Preloader />
      <LandingExplainer />
      <div id="app" className="border-t border-border">
        <AppShell />
      </div>
    </>
  );
}
