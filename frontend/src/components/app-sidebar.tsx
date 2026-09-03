"use client";

import {
  ArrowLeftRight,
  Bell,
  Bookmark,
  Coins,
  FlaskConical,
  HandCoins,
  HeartPulse,
  LayoutDashboard,
  LogOut,
  Map as MapIcon,
  MessageSquare,
  Receipt,
  Settings,
  ShoppingBag,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-provider";
import { cn } from "@/lib/utils";

/**
 * The application shell's navigation.
 *
 * Nine destinations in a flat horizontal bar was past what the pattern holds --
 * the labels were breaking mid-word and the whole nav scrolled sideways. Grouping
 * is what makes nine legible: three short lists read faster than one long one,
 * and the group names say why the items belong together.
 *
 * Nav lives in a data array rather than in markup so that the sidebar, the
 * collapsed rail, and the mobile tab bar cannot drift apart -- all three render
 * from this.
 */

interface NavItem {
  href: string;
  label: string;
  Icon: LucideIcon;
}

interface NavGroup {
  /** Groups without a name render as the first, unlabelled block. */
  name?: string;
  items: NavItem[];
}

const NAV: NavGroup[] = [
  {
    items: [
      { href: "/dashboard", label: "Overview", Icon: LayoutDashboard },
      { href: "/transactions", label: "Transactions", Icon: ArrowLeftRight },
      { href: "/transactions/sms", label: "Bank messages", Icon: MessageSquare },
      { href: "/receipts", label: "Receipts", Icon: Receipt },
    ],
  },
  {
    name: "Insights",
    items: [
      { href: "/health", label: "Health", Icon: HeartPulse },
      { href: "/forecast", label: "Forecast", Icon: TrendingUp },
      { href: "/simulator", label: "What if?", Icon: FlaskConical },
      { href: "/advisor", label: "Should I buy it?", Icon: ShoppingBag },
    ],
  },
  {
    name: "Market",
    items: [
      { href: "/watchlist", label: "Watchlist", Icon: Bookmark },
      { href: "/alerts", label: "Alerts", Icon: Bell },
    ],
  },
  {
    name: "Community",
    items: [
      { href: "/contribute", label: "Add a shop", Icon: MapIcon },
      { href: "/contributions", label: "My contributions", Icon: HandCoins },
      { href: "/points", label: "Points", Icon: Coins },
    ],
  },
];

/** The five that fit a thumb, in the order they are reached for. */
//: Five, because a sixth would put each tab below the 44px minimum on a small
//: phone. The map earns its slot by being the screen the app opens on -- a user
//: who navigates into the dashboard needs one tap back to it, not a browser
//: gesture.
//:
//: "Mine" replaced Advisor here in M19. The sidebar is `hidden md:flex`, so the
//: whole Community group -- contributions, points, add-a-shop -- was reachable
//: on a phone only by typing the URL. The Android build is a WebView over this
//: exact layout, which made that the difference between shipped and not.
//: Advisor keeps its sidebar entry and is one tap from Overview.
const MOBILE_TABS: NavItem[] = [
  { href: "/", label: "Map", Icon: MapIcon },
  { href: "/dashboard", label: "Overview", Icon: LayoutDashboard },
  { href: "/transactions", label: "Txns", Icon: ArrowLeftRight },
  { href: "/contributions", label: "Mine", Icon: HandCoins },
  { href: "/settings", label: "Settings", Icon: Settings },
];

/**
 * Every destination matches its own subtree, which is what keeps Transactions
 * lit on `/transactions/import` and Receipts lit on `/receipts/[id]`.
 *
 * No route here is `/`. That was true when the root was the marketing page and
 * it is still true now that it is the map: the map is not a sidebar
 * destination, it is what the app opens on, and listing it would light up on
 * every prefix. The Map link below goes there explicitly and is compared
 * exactly.
 */
function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppSidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <>
      {/* --- sidebar: full at >=1280px, icon rail from 768px -----------------
       *
       * Sticky and viewport-tall so nine destinations stay reachable on a long
       * page. Only the nav list scrolls; brand and footer are pinned. */}
      <div className="hidden shrink-0 border-r border-hairline md:sticky md:top-0 md:flex md:h-dvh md:w-16 md:flex-col xl:w-60">
        {/* No rule under the brand. The sidebar's right border is the only line
         * the shell needs; a second one here would box the wordmark in. */}
        <div className="flex h-14 shrink-0 items-center px-3 xl:px-4">
          {/* No logo mark: there is no brand to draw, and inventing a glyph
           * would be decoration. The rail shows the wordmark's initial, which is
           * the wordmark compressed rather than a new element. */}
          <Link
            href="/dashboard"
            className="flex items-center gap-2 truncate rounded-control type-section font-semibold"
            aria-label="Frugal, go to overview"
          >
            <span aria-hidden className="xl:hidden">
              F
            </span>
            <span aria-hidden className="hidden xl:inline">
              Frugal
            </span>
          </Link>
        </div>

        <nav
          aria-label="Main"
          className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto p-2"
        >
          {NAV.map((group, i) => (
            <div key={group.name ?? `group-${i}`} className="flex flex-col gap-1">
              {/* sr-only rather than hidden at rail width: the grouping is still
               * useful to a screen reader when the label has no room to show. */}
              {group.name && (
                <h2 className="sr-only px-2 pt-1 pb-1 type-eyebrow text-ink-muted xl:not-sr-only">
                  {group.name}
                </h2>
              )}
              {group.items.map((item) => (
                <NavLink key={item.href} item={item} active={isActive(pathname, item.href)} />
              ))}
            </div>
          ))}
        </nav>

        <div className="flex flex-col gap-1 border-t border-hairline p-2">
          <NavLink
            item={{
              href: "/settings",
              label: user?.display_name ?? "Settings",
              Icon: Settings,
            }}
            active={isActive(pathname, "/settings")}
          />
          <div className="flex flex-col items-center gap-2 pt-1 xl:flex-row xl:justify-between">
            {/* Three 32px buttons side by side do not fit a 64px rail, so the
             * control stacks until there is room for it to sit in a row. */}
            <ThemeToggle className="flex-col xl:flex-row" />
            <Button variant="ghost" size="sm" onClick={() => void logout()} title="Sign out">
              <LogOut aria-hidden />
              <span className="sr-only xl:not-sr-only">Sign out</span>
            </Button>
          </div>
        </div>
      </div>

      {/* --- mobile: bottom tab bar under 768px ----------------------------- */}
      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-20 flex border-t border-hairline bg-page md:hidden"
      >
        {MOBILE_TABS.map(({ href, label, Icon }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                // 56px tall: clears the 44px minimum with room for the label.
                "flex h-14 flex-1 flex-col items-center justify-center gap-1 type-meta transition-colors",
                active ? "text-ink" : "text-ink-muted",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              <span className="truncate">{label}</span>
            </Link>
          );
        })}
      </nav>
    </>
  );
}

function NavLink({ item: { href, label, Icon }, active }: { item: NavItem; active: boolean }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      // `title` is the only label at rail width, where the text is hidden.
      title={label}
      className={cn(
        // 36px rows with a 12px gutter: compact enough that nine items need no
        // scrolling, loose enough to stay tappable.
        "relative flex h-9 items-center gap-3 rounded-control px-2 type-body transition-colors",
        // Icons are optically centred against the row, not baseline-aligned --
        // an icon box has no descender, so a shared baseline sits it low.
        "[&_svg]:size-4 [&_svg]:shrink-0",
        "justify-center xl:justify-start",
        active
          ? "bg-surface font-medium text-ink"
          : "text-ink-secondary hover:bg-surface hover:text-ink",
      )}
    >
      {/* A 2px rail rather than a filled pill. The active row is already a
       * lighter surface; the rail is what makes it unambiguous without turning
       * the sidebar into a set of buttons. Hidden at rail width, where the
       * surface change is the only thing that fits. */}
      {active && (
        <span
          className="absolute inset-y-1 left-0 hidden w-0.5 rounded-full bg-brand xl:block"
          aria-hidden
        />
      )}
      <Icon aria-hidden />
      <span className="hidden truncate xl:inline">{label}</span>
    </Link>
  );
}
