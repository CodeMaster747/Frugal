/**
 * The public footer's link table, in one place.
 *
 * The three column names are fixed surfaces rather than editorial choices:
 * support, learning, and contact are the three things someone leaves a landing
 * page to find. Every href resolves to a page that exists and an anchor that is
 * really on it — `e2e/landing.spec.ts` walks this table and fails if one stops
 * being true, because a footer of dead links is worse than a shorter footer.
 */

/**
 * Placeholder. Frugal has no inbox yet; replace this before the page is public
 * rather than shipping an address that bounces.
 */
export const SUPPORT_EMAIL = "support@frugal.app";

export interface FooterLink {
  label: string;
  href: string;
}

export interface FooterColumn {
  heading: string;
  links: FooterLink[];
}

export const FOOTER_COLUMNS: FooterColumn[] = [
  {
    heading: "Get Support",
    links: [
      { label: "Help centre", href: "/support" },
      { label: "Troubleshooting", href: "/support#troubleshooting" },
      { label: "Account & security", href: "/support#account" },
      { label: "Data & privacy", href: "/support#privacy" },
      { label: "System status", href: "/support#status" },
    ],
  },
  {
    heading: "Learn to Use",
    links: [
      { label: "Quick start", href: "/guide#start" },
      { label: "Get the Android app", href: "/download" },
      { label: "Importing transactions", href: "/guide#import" },
      { label: "Receipts & OCR", href: "/guide#receipts" },
      { label: "Reading your health score", href: "/guide#health" },
      { label: "Using the Purchase Advisor", href: "/guide#advisor" },
    ],
  },
  {
    heading: "Contact Us",
    links: [
      { label: "Email us", href: `mailto:${SUPPORT_EMAIL}` },
      { label: "Report a bug", href: "/contact#bug" },
      { label: "Request a feature", href: "/contact#feature" },
      { label: "Send feedback", href: "/contact#feedback" },
      { label: "Security disclosure", href: "/contact#security" },
    ],
  },
];
