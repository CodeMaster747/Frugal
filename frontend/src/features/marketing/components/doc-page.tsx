import { Section } from "@/components/ui/section";

/**
 * The shell the three footer destinations share.
 *
 * The container is the same 75rem one the header, the footer, and the app's
 * `<main>` use, and the prose column is capped inside it rather than centred in
 * the viewport. Centring would put the text's left edge somewhere the wordmark
 * above it and the footer below it do not start, which reads as two grids on
 * one page. The cap is still there — 1200px is not a readable measure for
 * running prose — it just hangs off the same left edge as everything else.
 */
export function DocPage({
  eyebrow,
  title,
  intro,
  children,
}: {
  eyebrow: string;
  title: string;
  intro: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto w-full px-4 py-16 sm:px-6 lg:px-8 xl:max-w-[75rem]">
      <div className="max-w-3xl">
        <header className="space-y-2">
          <p className="type-eyebrow text-ink-muted">{eyebrow}</p>
          <h1 className="type-editorial text-balance">{title}</h1>
          <p className="max-w-prose type-body text-ink-secondary">{intro}</p>
        </header>

        <div className="mt-12 space-y-8">{children}</div>
      </div>
    </div>
  );
}

/**
 * One anchored passage. `scroll-mt-20` clears the 56px sticky header, so a
 * deep link from the footer does not land with its heading hidden under it.
 */
export function DocSection({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Section id={id} title={title} className="scroll-mt-20">
      <div className="max-w-prose space-y-3 type-body text-ink-secondary">{children}</div>
    </Section>
  );
}
