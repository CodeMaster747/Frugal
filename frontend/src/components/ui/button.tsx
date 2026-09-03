import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

const button = cva(
  // `[&_svg]:size-4` sets every icon inside a button, so call sites must not pass
  // their own size class. Icons are optically centred with `items-center` rather
  // than aligned to the text baseline -- a baseline-aligned icon sits low, because
  // its bounding box has no descender to account for.
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-control font-medium whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // Near-black on off-white, and the reverse in dark. Still the workhorse:
        // most primary actions in the application are the obvious next step on a
        // screen the user already chose to be on, and they do not need to be
        // branded to be found. Opacity rather than a second colour: ink ->
        // ink-secondary is a large visible jump in both modes, where this reads
        // as a press.
        primary: "bg-ink text-page hover:opacity-90",
        // The brand reservation, and the only variant that uses it. Reserved for
        // moments where the action carries the product's identity rather than a
        // screen's workflow: signing up, starting a trial, loading demo data.
        //
        // This is not the "saturated colour is for data" rule being broken -- it
        // is the carve-out that keeps it intact. `--brand` is defined in
        // globals.css as chrome-only and sits at a lightness the chart palette
        // never reaches, which is what stops it reading as a series.
        //
        // Deliberately scarce: a screen with two brand buttons has none.
        brand: "bg-brand text-brand-contrast hover:bg-brand-strong",
        // Hover shifts the border as well as the fill. On a bordered surface the
        // fill change alone is nearly invisible at these contrast ratios.
        secondary:
          "border border-gridline bg-transparent hover:border-baseline hover:bg-surface",
        ghost: "text-ink-secondary hover:bg-surface hover:text-ink",
        // `text-white` is correct here and is not a theme leak: --status-critical
        // is mode-invariant by design (.dark deliberately does not override it),
        // so its foreground must be constant too. White clears 4.81:1 on #d03b3b;
        // --page would drop to 4.00:1 in dark mode and fail AA.
        danger: "bg-critical text-white hover:opacity-90",
      },
      size: {
        // 44px minimum touch target on the default size (NFR-6).
        md: "type-body h-11 px-4",
        sm: "type-body h-9 px-3",
        lg: "h-12 px-6 text-base",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = ComponentProps<"button"> &
  VariantProps<typeof button> & { asChild?: boolean };

export function Button({ className, variant, size, asChild, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(button({ variant, size }), className)} {...props} />;
}
