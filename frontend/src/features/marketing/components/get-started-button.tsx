"use client";

import Link from "next/link";

import { Button, type ButtonProps } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-provider";

/**
 * The home screen's only call to action, in both of its states.
 *
 * A signed-in visitor should not be sent to a sign-in form, but redirecting
 * them off the home screen would mean rendering nothing until the session
 * finishes restoring — a blank frame on every load, to save one click. So the
 * page always renders immediately and only this label settles: `restoring` is
 * treated as anonymous, which is what it turns out to be for every first-time
 * visitor and is a correct destination for a returning one either way.
 */
export function GetStartedButton({ size, className }: Pick<ButtonProps, "size" | "className">) {
  const { status } = useAuth();
  const signedIn = status === "authenticated";

  return (
    // `brand` rather than `primary`: this is the one action on the public site
    // that is about the product rather than about the screen, which is exactly
    // the budget the variant is reserved for.
    <Button variant="brand" size={size} className={className} asChild>
      <Link href={signedIn ? "/dashboard" : "/register"}>
        {signedIn ? "Open dashboard" : "Get Started"}
      </Link>
    </Button>
  );
}
