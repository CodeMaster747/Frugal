"use client";

import { AppSidebar } from "@/components/app-sidebar";
import { ServicePausedBanner } from "@/components/service-paused-banner";
import { RequireAuth } from "@/features/auth/components/require-auth";
import { DeviceSync } from "@/features/sms/components/device-sync";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <AuthenticatedShell>{children}</AuthenticatedShell>
    </RequireAuth>
  );
}

function AuthenticatedShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-full flex-1 items-start">
      {/* Skip link, required by NFR-6 and previously missing. Nine sidebar links
       * ahead of the content is a lot to tab past on every navigation. */}
      <a
        href="#content"
        className="sr-only rounded-control border border-hairline bg-surface px-4 py-2 type-body font-medium focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-30"
      >
        Skip to content
      </a>

      <AppSidebar />

      {/* Capped rather than full-bleed: past about 1200px a dashboard row
       * stretches wider than it can be read across. `min-w-0` is what lets wide
       * tables scroll inside their own container instead of pushing the page
       * sideways. pb-20 on mobile clears the fixed bottom tab bar. */}
      <main
        id="content"
        className="mx-auto min-w-0 flex-1 px-4 pt-6 pb-20 sm:px-6 md:pb-10 lg:px-8 xl:max-w-[75rem]"
      >
        {/* Above the page, and on every page, because a paused dependency is a
         * fact about the deployment rather than about whichever screen the user
         * happens to be on. Renders nothing in the ordinary case, which is most
         * of the time and always when running on the simulator. */}
        <ServicePausedBanner />
        {/* Renders nothing anywhere except the `smsreader` APK, where it
         * uploads whatever the broadcast receiver buffered while the app was
         * closed. It lives inside RequireAuth because the drain needs the
         * access token, which native code deliberately never holds. */}
        <DeviceSync />
        {children}
      </main>
    </div>
  );
}
