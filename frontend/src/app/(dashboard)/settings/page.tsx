"use client";

import { Bell } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Section } from "@/components/ui/section";
import { useAuth } from "@/features/auth/auth-provider";
import { FormError } from "@/features/auth/components/form-error";
import { deleteAccount, updateMe } from "@/features/auth/api";
import { setAccessToken } from "@/lib/api/client";

export default function SettingsPage() {
  const { user, setUser } = useAuth();
  const router = useRouter();

  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);

  const save = async () => {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      setUser(await updateMe({ display_name: displayName }));
      setSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setDeleting(true);
    setError(null);
    try {
      await deleteAccount();
      setAccessToken(null);
      router.push("/login");
    } catch (e) {
      setError(e);
      setDeleting(false);
    }
  };

  return (
    <div className="max-w-lg space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="type-title">Settings</h1>
        {/* Alerts also sits on Overview, but Overview swaps itself for an empty
         * state until there is data -- and the first thing a new account may
         * want is to turn notifications down. This header always renders, and
         * what to be interrupted about is a preference like any other here. */}
        <Button variant="secondary" size="sm" asChild>
          <Link href="/alerts">
            <Bell aria-hidden />
            Alerts
          </Link>
        </Button>
      </div>

      <Section title="Profile">
        <div className="space-y-4">
          <FormError error={error} />

          <Field
            label="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
          <div className="flex items-center gap-3">
            <Button onClick={() => void save()} disabled={saving || !displayName.trim()}>
              {saving ? "Saving…" : "Save changes"}
            </Button>
            {saved && (
              <span role="status" className="type-body text-good">
                ✓ Saved
              </span>
            )}
          </div>
        </div>
      </Section>

      {/* The one tinted container left in the app. An irreversible action earns a
       * border that says so, and at 30% the critical token is a warning rather
       * than an alarm. */}
      <Section
        variant="bordered"
        title={<span className="text-critical">Delete account</span>}
        className="border-critical/30 bg-transparent"
      >
        <div className="space-y-4">
          <p className="type-body text-ink-secondary">
            This permanently removes your account and every transaction, budget, and goal in it.
            It cannot be undone.
          </p>
          {/* Typed confirmation rather than a modal: deletion is irreversible, so
              the friction is the point. */}
          <Field
            label='Type "DELETE" to confirm'
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="DELETE"
          />
          <Button
            variant="danger"
            disabled={confirmText !== "DELETE" || deleting}
            onClick={() => void remove()}
          >
            {deleting ? "Deleting…" : "Delete my account"}
          </Button>
        </div>
      </Section>
    </div>
  );
}
