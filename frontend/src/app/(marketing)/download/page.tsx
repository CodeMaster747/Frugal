import type { Metadata } from "next";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { DocPage, DocSection } from "@/features/marketing/components/doc-page";
import { SUPPORT_EMAIL } from "@/features/marketing/content";

export const metadata: Metadata = {
  title: "Download for Android — Frugal",
  description:
    "Install Frugal on Android. Two builds: the standard app, and a second one that reads bank SMS and needs a manual install.",
};

/**
 * Written by `scripts/publish-apk-hashes.sh` from the actual files in
 * `public/downloads/`. Run it after every release; do not edit by hand.
 *
 * Published because a self-signed binary from a website, with no way to verify
 * it, asks for more trust than anyone should extend. Generated rather than
 * copied for the same reason: the day someone forgets, the page shows the
 * *previous* build's hash -- worse than showing none, because a mismatch reads
 * as evidence of tampering rather than as a stale page.
 */
const BUILDS = {
  standard: {
    file: "/downloads/frugal-latest.apk",
    sha256: "fa36a5cd3af7f36cacd41496af9af46ea16bb334211255037a13fcdb8d312ae9",
  },
  sms: {
    file: "/downloads/frugal-sms-latest.apk",
    sha256: "709ede66311caae777535f2537070a98e39c6d940ecd684411c44fba1c00345f",
  },
} as const;

export default function DownloadPage() {
  return (
    <DocPage
      eyebrow="Android"
      title="Download for Android"
      intro="Frugal is not on the Play Store — you install it from here. There are two builds, and for almost everyone the first one is the right choice."
    >
      <DocSection id="standard" title="Frugal — the standard build">
        <p className="font-medium">This is the one to install.</p>
        <p>
          It installs normally, works for everyone, and asks for no special permissions at all.
          Your bank messages still reach it, two ways:
        </p>
        <ul className="ml-5 list-disc space-y-2">
          <li>
            <strong>Import a message backup.</strong> Export your SMS with any backup app,
            upload the file, and Frugal reads years of transactions in one go. No permission,
            and it works in the browser too.
          </li>
          <li>
            <strong>Share a single message.</strong> Long-press a bank alert in your Messages
            app, tap Share, and pick Frugal. Useful for the transaction you want recorded right
            now.
          </li>
        </ul>
        <DownloadLink build={BUILDS.standard} label="Download Frugal" />
      </DocSection>

      <DocSection id="sms" title="Frugal (SMS) — reads messages automatically">
        <p>
          The same app, plus permission to read bank SMS as they arrive. It captures messages in
          the background and uploads them the next time you open the app.
        </p>
        <p className="rounded-card bg-surface-raised p-4">
          <strong>Android will block this download.</strong> Google Play Protect stops the
          installation of any app from outside the Play Store that asks for SMS access — it
          checks before the app has run, and there is no way for us to ask for an exception.
          This is not a problem with the file, and it happens to every app in this category.
        </p>
        <p>
          You can still install it from a computer, which bypasses that check. It takes about
          five minutes and needs a USB cable:
        </p>
        <ol className="ml-5 list-decimal space-y-2">
          <li>
            On your phone: <strong>Settings → About phone</strong>, tap{" "}
            <strong>Build number</strong> seven times to turn on Developer options.
          </li>
          <li>
            <strong>Settings → System → Developer options</strong>, turn on{" "}
            <strong>USB debugging</strong>.
          </li>
          <li>
            On your computer, install{" "}
            <a
              href="https://developer.android.com/tools/releases/platform-tools"
              rel="noreferrer noopener"
              target="_blank"
            >
              Android platform-tools
            </a>
            , then connect the phone and accept the prompt.
          </li>
          <li>
            Run <code>adb install frugal-sms.apk</code>.
          </li>
          <li>
            Open the app, then <strong>App info → ⋮ → Allow restricted settings</strong> before
            granting SMS access. Android hides that toggle for apps installed this way.
          </li>
        </ol>
        <p>
          Both builds can be installed side by side — they are separate apps, so this one will
          not replace the standard one.
        </p>
        <DownloadLink build={BUILDS.sms} label="Download Frugal (SMS)" />
      </DocSection>

      <DocSection id="what-we-read" title="What the SMS build actually reads">
        <p>
          Only messages from bank sender IDs that also look like a transaction. The filter runs{" "}
          <strong>on your phone, before anything is saved</strong>, so a message from a friend
          is never stored and never leaves the handset. Promotional messages from your bank,
          one-time passcodes, and payment requests are all excluded.
        </p>
        <p>
          Of the messages that do pass, we keep a redacted copy: the amount, merchant and
          reference stay so the review screen can show its work, while your phone number, any
          passcode, and your account balance are removed. The original text is deleted once the
          transaction is saved, or after 30 days for anything left unreviewed.
        </p>
        <p>
          Nothing is added to your accounts automatically unless the reading is confident{" "}
          <em>and</em> you have already told Frugal which of your accounts a bank&apos;s number
          belongs to. Everything else waits for you.
        </p>
      </DocSection>

      <DocSection id="ios" title="iPhone">
        <p>
          There is no iPhone app, and there will not be one that reads messages: iOS has no way
          for any app to read your SMS. Frugal works fully in Safari on iPhone, and the backup
          import gives you the same transaction history.
        </p>
        <p>
          Questions about any of this: <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
          Or read <Link href="/support">Support</Link>.
        </p>
      </DocSection>
    </DocPage>
  );
}

function DownloadLink({
  build,
  label,
}: {
  build: { file: string; sha256: string };
  label: string;
}) {
  return (
    <div className="space-y-3 rounded-card border border-hairline p-4">
      <Button asChild>
        <a href={build.file} download>
          {label}
        </a>
      </Button>
      <p className="type-meta text-ink-muted">
        SHA-256: <code className="break-all">{build.sha256}</code>
      </p>
    </div>
  );
}
