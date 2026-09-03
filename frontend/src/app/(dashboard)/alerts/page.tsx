"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Empty } from "@/components/ui/empty";
import { Field } from "@/components/ui/field";
import { Section } from "@/components/ui/section";
import { Select } from "@/components/ui/select";
import {
  generateNotifications,
  getNotifications,
  getPreferences,
  markAllNotificationsRead,
  markNotificationRead,
  updatePreferences,
  type Preferences,
} from "@/features/simulator/api";
import { formatDate } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";

const CATEGORIES: { key: keyof Preferences; label: string; blurb: string }[] = [
  {
    key: "budget_enabled",
    label: "Budgets",
    blurb: "When a budget is close to its limit, while there is still time to act.",
  },
  {
    key: "bill_enabled",
    label: "Bills",
    blurb: "Recurring payments falling due in a few days.",
  },
  {
    key: "renewal_enabled",
    label: "Subscription renewals",
    blurb: "A week's notice, because cancelling takes longer than paying.",
  },
  {
    key: "goal_milestone_enabled",
    label: "Goal milestones",
    blurb: "When a savings goal passes a quarter mark.",
  },
  {
    key: "forecast_shortfall_enabled",
    label: "Projected shortfalls",
    blurb: "When your balance is projected to go negative. Sent immediately.",
  },
  {
    key: "price_drop_enabled",
    label: "Price drops",
    blurb: "When something on your watchlist gets materially cheaper.",
  },
];

const FREQUENCIES: { value: Preferences["digest_frequency"]; label: string }[] = [
  { value: "immediate", label: "As they happen" },
  { value: "daily", label: "Once a day" },
  { value: "weekly", label: "Once a week" },
  { value: "off", label: "Never" },
];

export default function AlertsPage() {
  const queryClient = useQueryClient();

  const feed = useQuery({ queryKey: keys.notifications.feed(), queryFn: getNotifications });
  const preferences = useQuery({
    queryKey: keys.notifications.preferences(),
    queryFn: getPreferences,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: keys.notifications.feed() });

  const generate = useMutation({ mutationFn: generateNotifications, onSuccess: invalidate });
  const readOne = useMutation({ mutationFn: markNotificationRead, onSuccess: invalidate });
  const readAll = useMutation({ mutationFn: markAllNotificationsRead, onSuccess: invalidate });

  /**
   * Preference changes apply optimistically.
   *
   * Without this the checkbox is controlled by server state with nothing local
   * in between, so a click leaves it visibly unmoved until the refetch lands.
   * A toggle that does not move when clicked reads as broken, and the user's
   * next move is to click it again — which would send a second, opposite
   * change.
   *
   * The previous value is captured and restored if the write fails, so a
   * failure leaves the UI showing what is actually stored.
   */
  const save = useMutation({
    mutationFn: updatePreferences,
    onMutate: async (changes) => {
      // Applied *before* the await, not after. `cancelQueries` yields, and a
      // toggle that only moves on the next microtask still reads as a click
      // that did nothing — the cancel is about stopping an in-flight refetch
      // from overwriting, which it does just as well afterwards.
      const previous = queryClient.getQueryData<Preferences>(["notification-preferences"]);
      if (previous) {
        queryClient.setQueryData<Preferences>(["notification-preferences"], {
          ...previous,
          ...changes,
        });
      }
      await queryClient.cancelQueries({ queryKey: keys.notifications.preferences() });
      return { previous };
    },
    onError: (_error, _changes, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["notification-preferences"], context.previous);
      }
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: keys.notifications.preferences() }),
  });

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="type-title">Alerts</h1>
          <p className="mt-1 type-body text-ink-secondary">
            Only things worth interrupting you for. Everything here can be switched off.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
            data-testid="generate-alerts"
          >
            <RefreshCw aria-hidden className={generate.isPending ? "animate-spin" : ""} />
            Check now
          </Button>
          {feed.data && feed.data.unread_count > 0 && (
            <Button variant="ghost" size="sm" onClick={() => readAll.mutate()}>
              Mark all read
            </Button>
          )}
        </div>
      </div>

      <Section title="Recent">
        {feed.isPending ? (
          <p className="type-body text-ink-muted">Loading…</p>
        ) : feed.data && feed.data.data.length > 0 ? (
          <ul className="space-y-2" data-testid="notification-feed">
            {feed.data.data.map((notification) => (
              <li
                key={notification.id}
                className={`rounded-card border border-hairline bg-surface p-4 ${
                  notification.read_at === null ? "border-l-2 border-l-series-1" : ""
                }`}
                data-testid="notification"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="type-body font-medium">{notification.subject}</p>
                    <p className="mt-0.5 type-body text-ink-secondary">{notification.body}</p>
                    <p className="mt-1 type-meta text-ink-muted">
                      {notification.category.replace(/_/g, " ")} ·{" "}
                      {formatDate(notification.created_at)}
                      {notification.urgency === "immediate" && " · sent immediately"}
                    </p>
                  </div>
                  {notification.read_at === null && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => readOne.mutate(notification.id)}
                    >
                      Mark read
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty icon={<Bell />}>
            Nothing to tell you. That is the normal state — alerts appear only when a budget is
            close, a bill is due, a goal moves, or your balance is projected to run short.
          </Empty>
        )}
      </Section>

      {preferences.data && (
        <Section
          variant="bordered"
          data-testid="preferences"
          title="What you want to hear about"
        >
          {/* Divided rather than spaced: six settings in a row read as a list of
           * switches, and a rule between them is what makes each one a discrete
           * choice instead of a paragraph of checkboxes. */}
          <ul className="divide-y divide-hairline">
            {CATEGORIES.map((category) => (
              <li key={category.key}>
                <Checkbox
                  id={String(category.key)}
                  label={category.label}
                  description={category.blurb}
                  checked={Boolean(preferences.data[category.key])}
                  onChange={(e) =>
                    save.mutate({ [category.key]: e.target.checked } as Partial<Preferences>)
                  }
                />
              </li>
            ))}
          </ul>

          <div className="mt-6 grid gap-4 border-t border-hairline pt-6 sm:grid-cols-3">
            <Select
              id="digest"
              label="How often"
              value={preferences.data.digest_frequency}
              onChange={(e) =>
                save.mutate({
                  digest_frequency: e.target.value as Preferences["digest_frequency"],
                })
              }
            >
              {FREQUENCIES.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </Select>

            <Field
              label="Send at (hour)"
              inputMode="numeric"
              value={String(preferences.data.digest_hour)}
              onChange={(e) => {
                const hour = Number(e.target.value);
                if (hour >= 0 && hour <= 23) save.mutate({ digest_hour: hour });
              }}
            />
          </div>

          <p className="type-meta text-ink-muted">
            {/* Stated plainly, because a user who has turned something off should
                be able to see that it stayed off. */}
            A category you switch off is never recorded at all, not merely hidden. Projected
            shortfalls are sent as they happen; everything else waits for your chosen time.
          </p>
        </Section>
      )}
    </div>
  );
}
