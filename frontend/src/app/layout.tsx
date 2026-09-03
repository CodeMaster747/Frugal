import type { Metadata } from "next";
import { IBM_Plex_Mono, Instrument_Sans, Instrument_Serif } from "next/font/google";

import { ThemeProvider } from "@/components/theme-provider";
import { AuthProvider } from "@/features/auth/auth-provider";
import { QueryProvider } from "@/lib/api/query-client";

import "./globals.css";

// Three faces, each with exactly one job.
//
// Instrument Sans carries the interface. It ships `tnum`, which the `.tabular`
// rule in globals.css depends on -- a face without it makes that rule a silent
// no-op and money columns stop aligning with no error anywhere.
const instrumentSans = Instrument_Sans({
  variable: "--font-instrument-sans",
  subsets: ["latin"],
});

// Plex Mono carries figures that must align. It has no `tnum` and does not need
// one: every glyph in a monospace face is the same advance width already.
const plexMono = IBM_Plex_Mono({
  // Not variable on Google, so the weights have to be named.
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  subsets: ["latin"],
});

// The display serif carries marketing headings and the cold-start welcome,
// through the `type-editorial` roles in globals.css. It is deliberately NOT
// wired into `type-hero` or `type-display`: both of those are used on figures
// as well as headings, and this face has no `tnum`.
const instrumentSerif = Instrument_Serif({
  weight: "400",
  variable: "--font-instrument-serif",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Frugal — The Intelligent Financial Decision Platform",
  description:
    "Frugal analyses your financial life and explains every recommendation it makes — no score, verdict, or forecast without the reasoning behind it.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning: next-themes stamps the theme class on <html>
    // before paint to avoid a flash, so server and client markup differ here
    // by design.
    <html
      lang="en"
      suppressHydrationWarning
      className={`${instrumentSans.variable} ${plexMono.variable} ${instrumentSerif.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <ThemeProvider>
          <QueryProvider>
            <AuthProvider>{children}</AuthProvider>
          </QueryProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
