import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * One feature passage: a column of copy beside a column of product.
 *
 * `media` alternates down the page, but only from 1024px up. Below that the
 * text always comes first in the DOM and stays first on screen — alternation is
 * a device for a two-column layout, and on a phone it would just shuffle the
 * reading order for no reason.
 */
export function FeatureRow({
  eyebrow,
  title,
  body,
  points,
  media = "right",
  visual,
}: {
  eyebrow: string;
  title: string;
  body: string;
  points: string[];
  media?: "left" | "right";
  visual: React.ReactNode;
}) {
  const mediaLeft = media === "left";

  return (
    <div className="grid items-center gap-8 lg:grid-cols-2 lg:gap-12">
      <div className={cn("space-y-4", mediaLeft && "lg:order-2")}>
        <p className="type-eyebrow text-ink-muted">{eyebrow}</p>
        <h2 className="type-editorial text-balance">{title}</h2>
        <p className="max-w-prose type-body text-ink-secondary">{body}</p>
        <ul className="space-y-2">
          {points.map((point) => (
            <li key={point} className="flex gap-3 type-body text-ink-secondary">
              <Check className="mt-1 size-4 shrink-0 text-ink-muted" aria-hidden />
              <span className="max-w-prose">{point}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className={cn(mediaLeft && "lg:order-1")}>{visual}</div>
    </div>
  );
}
