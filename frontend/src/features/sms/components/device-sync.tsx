"use client";

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { keys } from "@/lib/api/query-keys";

import { drainDeviceBuffer, hasSmsReader, isNativeCurrent } from "../native";

/**
 * Drains the device's SMS buffer whenever the app comes to the foreground.
 *
 * Renders nothing. Mounted inside the authenticated shell, because the drain
 * POSTs with a bearer token the auth provider holds -- native code has no
 * credentials of its own, which is the entire reason this component exists
 * rather than the receiver uploading directly.
 *
 * `visibilitychange` rather than a Capacitor `appStateChange` listener: it is
 * the same signal on Android and it costs no import in a browser, where this
 * component compiles in and does nothing.
 *
 * Failures are swallowed on purpose. The buffer is only cleared after a
 * successful upload, so a failed drain is retried on the next foreground; the
 * user has nothing to do about it, and surfacing it would be noise on a screen
 * they opened to do something else.
 */
export function DeviceSync() {
  const queryClient = useQueryClient();
  const running = useRef(false);

  useEffect(() => {
    if (!hasSmsReader()) return;

    let cancelled = false;

    const drain = async () => {
      if (running.current || document.visibilityState !== "visible") return;
      running.current = true;
      try {
        if (!(await isNativeCurrent())) return;
        const result = await drainDeviceBuffer();
        if (!cancelled && result.drained > 0) {
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: keys.sms.queue() }),
            queryClient.invalidateQueries({ queryKey: keys.finance.transactions.all() }),
          ]);
        }
      } catch {
        // Buffer intact; the next foreground retries.
      } finally {
        running.current = false;
      }
    };

    void drain();
    document.addEventListener("visibilitychange", drain);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", drain);
    };
  }, [queryClient]);

  return null;
}
