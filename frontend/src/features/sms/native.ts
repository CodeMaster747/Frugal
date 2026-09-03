/**
 * The web half of the Android bridge.
 *
 * Two Kotlin plugins have existed since M12 with nothing on this side calling
 * them: `SmsReceiver` (the `smsreader` flavour only) and `ShareIntent` (both
 * flavours). `ingestBatch()` in `./api.ts` was written for the drain and was
 * never invoked. Live capture therefore captured, buffered, and stopped.
 *
 * Three rules, each of which the Kotlin says out loud:
 *
 * 1. **Guard every call with `isPluginAvailable`.** The standard APK compiles
 *    no SMS plugin and a browser has no bridge at all. Both must see a clean
 *    absence, not a rejected promise.
 * 2. **Never clear the buffer on read.** `drainPending` peeks; `acknowledge`
 *    clears, and only after the server has accepted the batch. Clearing on read
 *    would lose a week of transactions to one flaky connection, silently.
 * 3. **Check the native version first.** With Capacitor's `server.url` the
 *    JavaScript comes from the server and the plugins come from a sideloaded
 *    APK with no update channel, so a deployed frontend can be newer than every
 *    installed build. `GET /system/providers` publishes `min_native_version`.
 */

import { Capacitor, registerPlugin } from "@capacitor/core";

import { API_BASE_URL } from "@/lib/api/client";

import { ingestBatch } from "./api";

/** One buffered message, in the shape `SmsBroadcastReceiver.kt` writes. */
interface NativeMessage {
  sender: string;
  body: string;
  /** ISO-8601, stamped on the device at receipt. */
  receivedAt: string;
}

interface SmsReceiverPlugin {
  nativeVersion(): Promise<{ version: number }>;
  hasPermission(): Promise<{ granted: boolean }>;
  requestPermission(): Promise<{ granted: boolean }>;
  drainPending(): Promise<{ messages: NativeMessage[]; count: number }>;
  acknowledge(): Promise<void>;
}

interface ShareIntentPlugin {
  nativeVersion(): Promise<{ version: number }>;
  consumeSharedText(): Promise<{ text: string | null }>;
}

const SmsReceiver = registerPlugin<SmsReceiverPlugin>("SmsReceiver");
const ShareIntent = registerPlugin<ShareIntentPlugin>("ShareIntent");

export function isNative(): boolean {
  return Capacitor.isNativePlatform();
}

/** True only on the `smsreader` build. The standard APK compiles no such plugin. */
export function hasSmsReader(): boolean {
  return Capacitor.isPluginAvailable("SmsReceiver");
}

export function hasShareIntent(): boolean {
  return Capacitor.isPluginAvailable("ShareIntent");
}

/**
 * Whether this APK is new enough for the frontend it just loaded.
 *
 * Read from the unauthenticated providers endpoint, which never raises. A
 * failure to reach it is not a reason to block the app: an unreachable check
 * returns `true` and the individual plugin calls degrade on their own.
 */
export async function isNativeCurrent(): Promise<boolean> {
  if (!hasSmsReader() && !hasShareIntent()) return true;

  try {
    const response = await fetch(`${API_BASE_URL}/system/providers`);
    if (!response.ok) return true;
    const { min_native_version: required } = (await response.json()) as {
      min_native_version?: number;
    };
    if (typeof required !== "number") return true;

    const plugin = hasSmsReader() ? SmsReceiver : ShareIntent;
    const { version } = await plugin.nativeVersion();
    return version >= required;
  } catch {
    return true;
  }
}

export async function smsPermission(): Promise<boolean> {
  if (!hasSmsReader()) return false;
  const { granted } = await SmsReceiver.hasPermission();
  return granted;
}

export async function requestSmsPermission(): Promise<boolean> {
  if (!hasSmsReader()) return false;
  const { granted } = await SmsReceiver.requestPermission();
  return granted;
}

export interface DrainResult {
  /** How many the device had buffered. Zero is the common case. */
  drained: number;
  created: number;
  already_held: number;
  filtered: number;
  committed: number;
}

const EMPTY: DrainResult = {
  drained: 0,
  created: 0,
  already_held: 0,
  filtered: 0,
  committed: 0,
};

/**
 * Upload everything the device has buffered, then clear it.
 *
 * `acknowledge()` runs only after `ingestBatch` resolves. If the upload throws,
 * the buffer is untouched and the next foreground retries it -- which is the
 * whole reason the Kotlin splits peek from clear.
 *
 * Server-side hashing makes a re-sent batch idempotent, so retrying costs a
 * request and never a duplicate transaction.
 */
export async function drainDeviceBuffer(): Promise<DrainResult> {
  if (!hasSmsReader()) return EMPTY;
  if (!(await smsPermission())) return EMPTY;

  const { messages } = await SmsReceiver.drainPending();
  if (messages.length === 0) return EMPTY;

  const counts = await ingestBatch(
    messages.map((m) => ({
      body: m.body,
      sender: m.sender,
      received_at: m.receivedAt,
    })),
  );

  await SmsReceiver.acknowledge();
  return { drained: messages.length, ...counts };
}

/** Text handed over by the Android share sheet, if any is waiting. */
export async function consumeSharedText(): Promise<string | null> {
  if (!hasShareIntent()) return null;
  const { text } = await ShareIntent.consumeSharedText();
  return text;
}
