import { FileUp, LineChart, Scale } from "lucide-react";
import type { Metadata } from "next";

import { Section } from "@/components/ui/section";
import { FeatureRow } from "@/features/marketing/components/feature-row";
import { GetStartedButton } from "@/features/marketing/components/get-started-button";
import { Reveal } from "@/features/marketing/components/reveal";
import {
  AdvisorVisual,
  ForecastVisual,
  HealthVisual,
  ImportVisual,
  SimulationVisual,
} from "@/features/marketing/components/visuals";

/**
 * What the product is, and why.
 *
 * This was `/` until M18, when the map took that slot: the landing screen is now
 * the thing the product does rather than a description of it. The description
 * still has to exist -- it is what a search engine indexes, what a link preview
 * quotes, and what somebody sent here by a friend reads before signing up -- so
 * it moved rather than being deleted.
 *
 * A server component throughout, with two client islands: the CTA, which has to
 * know whether you are signed in, and Reveal. Everything else is static markup
 * inside the same container the dashboard uses, so the landing page and the
 * product are visibly the same width, the same type, and the same palette.
 *
 * Every claim here traces to docs/01-srs.md. There is no social proof on this
 * page and there should not be: Frugal has no users to count, and an invented
 * number on the front of a product about explaining its own numbers would be a
 * strange place to start.
 */

export const metadata: Metadata = {
  // The root layout's title already names the product and its positioning, and
  // this is the page that title was written for.
  description:
    "Frugal models your financial state continuously and turns it into explained recommendations — a health score, a cash-flow forecast, and a verdict on whether you can afford what you are about to buy.",
};

const CONTAINER = "mx-auto w-full px-4 sm:px-6 lg:px-8 xl:max-w-[75rem]";

const STEPS = [
  {
    Icon: FileUp,
    title: "Bring your history",
    body: "Import a bank CSV, add transactions by hand, or load twelve months of demo data in one click.",
  },
  {
    Icon: LineChart,
    title: "Frugal models it",
    body: "Health score, cash-flow forecast, and recurring items are recomputed from your data, not from averages.",
  },
  {
    Icon: Scale,
    title: "Ask it a decision",
    body: "Put a purchase to it and get a verdict, a date, and the factors that produced both.",
  },
];

export default function HomePage() {
  return (
    <>
      {/* --- hero ----------------------------------------------------------
       *
       * Deliberately not wrapped in Reveal. Everything above the fold has to be
       * painted, not faded in from an effect: a reveal here would mean a blank
       * first frame and would make the headline the last thing to arrive rather
       * than the first. Motion starts below, where there is a scroll to trigger
       * it. */}
      <section className={`${CONTAINER} pt-16 pb-16 sm:pt-20`}>
        <div className="mx-auto max-w-2xl space-y-6 text-center">
          <p className="type-eyebrow text-ink-muted">Frugal</p>
          <h1 className="type-editorial text-balance md:type-editorial-hero">
            Personal finance that tells you what to do next.
          </h1>
          <p className="mx-auto max-w-prose type-body text-ink-secondary">
            Most money tools describe what you already spent. Frugal models your financial state
            continuously and turns it into recommendations you can interrogate — every score,
            verdict, and forecast decomposes into the inputs that produced it.
          </p>
          <div className="flex flex-col items-center gap-3">
            <GetStartedButton size="lg" />
            <p className="type-meta text-ink-muted">
              Nothing to connect. Load twelve months of demo data and every engine has something
              to show.
            </p>
          </div>
        </div>

        <div className="mx-auto mt-12 max-w-2xl">
          <AdvisorVisual />
        </div>
      </section>

      {/* --- how it works ------------------------------------------------- */}
      <section className={`${CONTAINER} py-16`}>
        <Reveal className="space-y-8">
          <div className="space-y-2">
            <p className="type-eyebrow text-ink-muted">How it works</p>
            <h2 className="type-editorial text-balance">
              Three steps, then it answers questions.
            </h2>
          </div>
        </Reveal>

        <div className="mt-8 grid gap-4 sm:grid-cols-3">
          {STEPS.map(({ Icon, title, body }, i) => (
            <Reveal key={title} delay={i * 80}>
              <Section variant="bordered" className="flex h-full flex-col gap-4">
                <span className="text-ink-muted [&_svg]:size-5" aria-hidden>
                  <Icon />
                </span>
                <div className="space-y-1">
                  <h3 className="type-section">{title}</h3>
                  <p className="type-body text-ink-secondary">{body}</p>
                </div>
              </Section>
            </Reveal>
          ))}
        </div>
      </section>

      {/* --- features, alternating ---------------------------------------- */}
      <Reveal as="section" className={`${CONTAINER} py-16`}>
        <FeatureRow
          eyebrow="Purchase advisor"
          title="Should I buy it? Answered, with the reasoning attached."
          body="Submit a purchase and Frugal returns one of four verdicts — buy now, buy on EMI, wait, or not recommended — alongside an affordability score and the factors behind it."
          points={[
            "Weights sum to 1.00 and contributions sum to the score. A test asserts it.",
            "A before-and-after simulation shows what the purchase does to your balance, savings rate, and goal dates.",
            "On a wait verdict, it estimates the date the purchase becomes affordable.",
          ]}
          media="right"
          visual={<SimulationVisual />}
        />
      </Reveal>

      <Reveal as="section" className={`${CONTAINER} py-16`}>
        <FeatureRow
          eyebrow="Financial health"
          title="A score you can take apart."
          body="One number from 0 to 100, composed of six sub-metrics — savings rate, emergency-fund coverage, debt-to-income, budget discipline, cash-flow stability, and growth."
          points={[
            "Computed from a published, versioned rubric. There is no opaque model behind it.",
            "Every sub-metric exposes its raw value, weight, contribution, and band thresholds.",
            "Too little history yields a partial score with explicit caveats, never a made-up number.",
          ]}
          media="left"
          visual={<HealthVisual />}
        />
      </Reveal>

      <Reveal as="section" className={`${CONTAINER} py-16`}>
        <FeatureRow
          eyebrow="Cash-flow forecast"
          title="Projections that name their own method."
          body="Projected balance at 30, 60, and 90 days, with confidence intervals and the dates where your balance would cross the floor you set."
          points={[
            "The method is chosen by how much history you have, and the response says which one ran.",
            "Recurring items — salary, rent, EMI, subscriptions — are detected and fed into every tier.",
            "Confidence is calibrated and shown at the same visual weight as the number itself.",
          ]}
          media="right"
          visual={<ForecastVisual />}
        />
      </Reveal>

      <Reveal as="section" className={`${CONTAINER} py-16`}>
        <FeatureRow
          eyebrow="Getting data in"
          title="Statements and receipts, without the retyping."
          body="Import a bank CSV with column mapping and a per-row preview, or photograph a receipt and let the OCR pipeline read it."
          points={[
            "Re-importing the same file changes nothing — a content hash makes imports idempotent.",
            "Every extracted field carries its own confidence score, not one score for the receipt.",
            "Anything below threshold is flagged for review rather than committed quietly.",
          ]}
          media="left"
          visual={<ImportVisual />}
        />
      </Reveal>

      {/* --- closing ------------------------------------------------------- */}
      <section className={`${CONTAINER} pt-8 pb-20`}>
        <Reveal>
          <Section variant="bordered" className="space-y-6 py-12 text-center">
            <div className="space-y-2">
              <h2 className="type-editorial text-balance">
                Start with a question you have now.
              </h2>
              <p className="mx-auto max-w-prose type-body text-ink-secondary">
                Create an account, load the demo data, and put a real decision to it.
              </p>
            </div>
            <div className="flex justify-center">
              <GetStartedButton size="lg" />
            </div>
          </Section>
        </Reveal>
      </section>
    </>
  );
}
