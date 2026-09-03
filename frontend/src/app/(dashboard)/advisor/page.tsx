"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, Clock, CreditCard, Search, X } from "lucide-react";
import { useState } from "react";

import { ExplanationPanel } from "@/components/explanation-panel";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { Field } from "@/components/ui/field";
import { Section } from "@/components/ui/section";
import {
  evaluatePurchase,
  searchProducts,
  type Advice,
  type Offer,
  type Verdict,
} from "@/features/advisor/api";
import { ImpactDumbbell } from "@/features/advisor/components/impact-dumbbell";
import { OffersPanel } from "@/features/market/components/offers-panel";
import { formatDate, formatMoney } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";

/**
 * Verdict presentation.
 *
 * Status colours are reserved and always ship with an icon and a sentence —
 * a recommendation carried by colour alone would be unreadable in greyscale,
 * to a colourblind user, and to a screen reader.
 */
const VERDICT: Record<
  Verdict,
  { label: string; blurb: string; tone: string; ring: string; Icon: typeof Check }
> = {
  buy_now: {
    label: "Buy it",
    blurb: "This fits comfortably in your finances.",
    tone: "text-good",
    ring: "border-good/40",
    Icon: Check,
  },
  buy_on_emi: {
    label: "Buy it — on instalments",
    blurb: "Affordable, but paying cash would thin your cushion more than it needs to.",
    tone: "text-series-1",
    ring: "border-series-1/40",
    Icon: CreditCard,
  },
  wait: {
    label: "Wait",
    blurb: "Not yet — but there is a date.",
    tone: "text-warning",
    ring: "border-warning/40",
    Icon: Clock,
  },
  not_recommended: {
    label: "Not recommended",
    blurb: "This would put your finances somewhere they should not go.",
    tone: "text-critical",
    ring: "border-critical/40",
    Icon: X,
  },
};

export default function AdvisorPage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [manual, setManual] = useState({ name: "", price: "" });

  const results = useQuery({
    queryKey: keys.advisor.search(submitted),
    queryFn: () => searchProducts(submitted),
    enabled: submitted.length > 0,
  });

  const advice = useMutation({ mutationFn: evaluatePurchase });

  const evaluateOffer = (offer: Offer) =>
    advice.mutate({
      product_query: offer.name,
      price: offer.price,
      external_id: offer.external_id,
    });

  return (
    <div className="space-y-8">
      <div>
        <h1 className="type-title">Should I buy it?</h1>
        <p className="mt-1 type-body text-ink-secondary">
          Search for something, or enter a price. The answer is based on your actual finances,
          and every part of it is shown.
        </p>
      </div>

      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(query.trim());
        }}
      >
        <div className="min-w-56 flex-1">
          <Field
            label="What are you thinking of buying?"
            placeholder="e.g. MacBook Air"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={!query.trim()} data-testid="search-products">
          <Search aria-hidden />
          Search
        </Button>
      </form>

      {submitted && results.data && (
        <div data-testid="search-results">
          {results.data.length > 0 ? (
            <Section variant="bordered" padding="flush" className="divide-y divide-hairline">
              {results.data.map((offer) => (
                <div key={offer.external_id} className="flex flex-wrap items-center gap-3 p-4">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate type-body font-medium">{offer.name}</span>
                    <span className="block type-meta text-ink-muted">{offer.seller}</span>
                  </span>
                  <span className="tabular type-body font-medium">
                    {formatMoney(offer.price, offer.currency)}
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => evaluateOffer(offer)}
                    disabled={advice.isPending}
                  >
                    Should I?
                  </Button>
                </div>
              ))}
            </Section>
          ) : (
            <Empty>
              Nothing in the catalogue matches that. Enter the price yourself below — the advice
              is about your finances, not the product.
            </Empty>
          )}
        </div>
      )}

      <Section
        as="form"
        variant="bordered"
        title="Or price it yourself"
        onSubmit={(e) => {
          e.preventDefault();
          advice.mutate({ product_query: manual.name || "Purchase", price: manual.price });
        }}
      >
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="What is it?"
              placeholder="Second-hand road bike"
              value={manual.name}
              onChange={(e) => setManual({ ...manual, name: e.target.value })}
            />
            <Field
              label="Price"
              inputMode="decimal"
              placeholder="45000"
              value={manual.price}
              onChange={(e) => setManual({ ...manual, price: e.target.value })}
            />
          </div>
          <Button
            type="submit"
            size="sm"
            disabled={!manual.price || advice.isPending}
            data-testid="evaluate-manual"
          >
            {advice.isPending ? "Working…" : "Ask"}
          </Button>
        </div>
      </Section>

      {advice.isError && (
        <p role="alert" className="type-body text-critical">
          {(advice.error as Error).message}
        </p>
      )}

      {advice.data && <AdviceCard advice={advice.data} onPick={evaluateOffer} />}
    </div>
  );
}

function AdviceCard({ advice, onPick }: { advice: Advice; onPick: (offer: Offer) => void }) {
  const verdict = VERDICT[advice.verdict];
  const downgraded = advice.verdict !== advice.score_verdict;

  return (
    <section className="space-y-6" data-testid="advice-card" data-verdict={advice.verdict}>
      {/* The verdict is the answer to the question the page asks, so it is the
       * one surface allowed to carry a tinted border -- but at 1px, not the 2px
       * it used to be. A double-weight border was the loudest element in the app,
       * and the icon, the coloured heading and the sentence beneath it already
       * say which way the answer went. */}
      <div className={`rounded-card border bg-surface ${verdict.ring}`}>
        <div className="flex flex-wrap items-start justify-between gap-6 p-6">
          <div className="flex min-w-0 items-start gap-3">
            <verdict.Icon className={`mt-0.5 size-5 shrink-0 ${verdict.tone}`} aria-hidden />
            <div className="min-w-0 space-y-1">
              <h2
                className={`type-section font-semibold ${verdict.tone}`}
                data-testid="verdict"
              >
                {verdict.label}
              </h2>
              <p className="type-body text-ink-secondary">{verdict.blurb}</p>
              <p className="type-body text-ink-muted">
                {advice.product_query} · {formatMoney(advice.price, advice.currency)}
              </p>
            </div>
          </div>

          <div className="shrink-0 space-y-0.5 text-right">
            <p className="tabular type-display">
              {Math.round(Number(advice.affordability_score))}
              <span className="type-section text-ink-muted">/100</span>
            </p>
            <p className="type-meta text-ink-muted">
              affordability · {Math.round(Number(advice.confidence) * 100)}% confidence
            </p>
          </div>
        </div>

        {(advice.affordable_from || advice.constraints.length > 0) && (
          // A raised band rather than nested bordered boxes. These are
          // qualifications on the verdict above, and a rule plus a surface step
          // says that more quietly than four more borders would.
          <div className="space-y-3 rounded-b-card border-t border-hairline bg-surface-raised p-6">
            {advice.affordable_from && (
              <p className="type-body" data-testid="affordable-from">
                At your current rate of saving, affordable from{" "}
                <strong>{formatDate(advice.affordable_from)}</strong> — that is with the price
                covered <em>and</em> three months of expenses still in reserve.
              </p>
            )}

            {advice.constraints.length > 0 && (
              <div className="space-y-2" data-testid="constraints">
                {downgraded && (
                  <p className="type-meta font-medium text-ink-secondary">
                    The score alone would have said &ldquo;
                    {VERDICT[advice.score_verdict].label.toLowerCase()}&rdquo;. A hard limit
                    changed that:
                  </p>
                )}
                {advice.constraints.map((constraint) => (
                  <p
                    key={constraint.code}
                    className="flex items-start gap-2 type-body text-ink-secondary"
                  >
                    <AlertTriangle
                      className="mt-0.5 size-4 shrink-0 text-warning"
                      aria-hidden
                    />
                    {constraint.message}
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Sits between the verdict and the impact detail on purpose. The verdict
       * answers the question asked; "could I pay less for it" is the first thing
       * a reader wants next, and it is a different question from the impact
       * breakdown below. */}
      <OffersPanel
        query={advice.product_query}
        askingPrice={advice.price}
        currency={advice.currency}
      />

      <Section variant="bordered" headingLevel={3} title="What this does to your position">
        <ImpactDumbbell
          before={advice.simulation.before}
          after={advice.simulation.after}
          estimatedHealth={advice.simulation.health_score_after_is_estimated}
        />
        {advice.simulation.goal_impact.length > 0 && (
          <p className="mt-4 type-body text-ink-secondary">
            Delays <strong>{advice.simulation.goal_impact[0].goal}</strong> by about{" "}
            {Math.round(advice.simulation.goal_impact[0].delay_days / 30)} months.
          </p>
        )}
      </Section>

      {advice.emi_options.length > 0 && (
        <Section
          variant="bordered"
          headingLevel={3}
          title="If you spread the cost"
          description="A monthly figure is designed to feel small. The total interest is what it actually costs. Plans that would take your repayments past 43% of income are marked."
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[30rem] text-left type-body" data-testid="emi-table">
              <thead className="type-meta text-ink-muted">
                <tr className="border-b border-hairline">
                  <th scope="col" className="py-2 font-medium">
                    Tenure
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    Monthly
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    Total interest
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    Debt load after
                  </th>
                </tr>
              </thead>
              <tbody>
                {advice.emi_options.map((option) => (
                  <tr
                    key={option.tenure_months}
                    className={`border-b border-hairline last:border-0 ${
                      option.is_serviceable ? "" : "text-ink-muted"
                    }`}
                  >
                    <td className="py-2">
                      {option.tenure_months} months
                      {/* Shown, but marked. Six unmarked rows read as six equally
                          available choices, which is the impression a retailer's
                          checkout wants and this table does not. */}
                      {!option.is_serviceable && (
                        <span className="ml-2 type-meta text-warning">beyond your means</span>
                      )}
                    </td>
                    <td className="tabular py-2 text-right">
                      {formatMoney(option.monthly, "INR")}
                    </td>
                    <td
                      className={`tabular py-2 text-right ${
                        option.is_serviceable ? "text-critical" : ""
                      }`}
                    >
                      {formatMoney(option.total_interest, "INR")}
                      <span className="ml-1 type-meta text-ink-muted">
                        (+{(Number(option.interest_share) * 100).toFixed(1)}%)
                      </span>
                    </td>
                    <td className="tabular py-2 text-right text-ink-secondary">
                      {(Number(option.new_debt_ratio) * 100).toFixed(0)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {advice.alternatives.length > 0 && (
        <Section variant="bordered" headingLevel={3} title="Cheaper, and what they would mean">
          <ul className="divide-y divide-hairline" data-testid="alternatives">
            {advice.alternatives.map((alternative) => (
              <li
                key={alternative.external_id}
                className="flex flex-wrap items-center gap-3 py-3"
              >
                <span className="min-w-0 flex-1 truncate type-body">{alternative.name}</span>
                <span className="tabular type-body">
                  {formatMoney(alternative.price, "INR")}
                </span>
                <span
                  className={`type-meta font-medium ${VERDICT[alternative.verdict_if_chosen].tone}`}
                >
                  {VERDICT[alternative.verdict_if_chosen].label}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    onPick({
                      external_id: alternative.external_id,
                      name: alternative.name,
                      category: "",
                      price: alternative.price,
                      currency: "INR",
                      brand: null,
                      seller: "",
                    })
                  }
                >
                  Ask about this
                </Button>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section headingLevel={3} title="How this was decided">
        <ExplanationPanel explanation={advice.explanation} />
      </Section>
    </section>
  );
}
