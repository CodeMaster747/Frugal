import { expect, test, type Page } from "@playwright/test";

import { FOOTER_COLUMNS } from "../src/features/marketing/content";

/**
 * The public landing surface.
 *
 * `/` has moved twice: it was the dashboard, so an anonymous visitor's first
 * screen was a password field; then it was the marketing page; and since M18 it
 * is the map, because the first screen should be the thing the product does.
 * The marketing page still exists at `/about` — it is what a search engine
 * indexes and what somebody sent here by a friend reads.
 *
 * Three properties are worth guarding: `/` does not redirect an anonymous
 * visitor to sign-in, the map renders for them, and the footer's links go
 * somewhere real. A footer that looks complete and is full of dead anchors is
 * the failure mode these pages invite.
 *
 * Requires the backend stack (`make up`) for the signed-in case only.
 */

const PASSWORD = "CorrectHorse9Battery";

async function signUp(page: Page) {
  const email = `landing-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL("/dashboard", { timeout: 20_000 });
}

test.describe("landing page", () => {
  test("an anonymous visitor lands on the map, not on sign-in", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveURL("/");
    // The pins are aggregates over a k-anonymity floor and name nobody, so
    // there is nothing here to gate (ADR-013). Asking someone to sign in before
    // they can see whether the map knows anything about their area is asking
    // them to buy before looking.
    await expect(page.getByRole("link", { name: "Get started" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Find something cheaper" })).toBeVisible();
  });

  test("a signed-in visitor gets the dashboard link rather than sign-up", async ({ page }) => {
    await signUp(page);
    await page.goto("/");

    await expect(page).toHaveURL("/");
    await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Get started" })).toHaveCount(0);
  });

  test("Get started goes to registration", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Get started" }).click();

    await expect(page).toHaveURL(/\/register/);
    await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  });
});

test.describe("about page", () => {
  test("the product description still exists and is reachable", async ({ page }) => {
    // Moved off `/` in M18 rather than deleted: it is what a search engine
    // indexes, what a link preview quotes, and what somebody sent here by a
    // friend reads before signing up.
    await page.goto("/about");

    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      "Personal finance that tells you what to do next.",
    );
  });

  test("Get Started goes to registration", async ({ page }) => {
    await page.goto("/about");

    // The hero CTA, not the header's — scoped to main so the two cannot be
    // confused if the header one is ever removed.
    await page.getByRole("main").getByRole("link", { name: "Get Started" }).first().click();

    await expect(page).toHaveURL(/\/register/);
    await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  });

  test("a signed-in visitor is offered the dashboard instead", async ({ page }) => {
    await signUp(page);
    await page.goto("/about");

    await expect(page).toHaveURL("/about");
    const cta = page.getByRole("main").getByRole("link", { name: "Open dashboard" }).first();
    await expect(cta).toBeVisible();
    await expect(cta).toHaveAttribute("href", "/dashboard");
    await expect(page.getByRole("link", { name: "Get Started" })).toHaveCount(0);
  });

  test("the hero renders without JavaScript-gated reveals hiding it", async ({ page }) => {
    await page.goto("/about");

    // Every revealed block must settle to full opacity. A stuck observer would
    // leave the page technically present and visually blank.
    const reveals = page.locator("[data-reveal]");
    await expect(reveals.first()).toBeVisible();

    for (const handle of await reveals.all()) {
      await handle.scrollIntoViewIfNeeded();
      await expect(handle).toHaveCSS("opacity", "1");
    }
  });

  test("every footer link resolves to a real page and a real anchor", async ({ page }) => {
    await page.goto("/about");

    for (const column of FOOTER_COLUMNS) {
      await expect(
        page.getByRole("navigation", { name: column.heading }).getByRole("heading"),
      ).toHaveText(column.heading);

      for (const link of column.links) {
        if (link.href.startsWith("mailto:")) continue;

        const [path, anchor] = link.href.split("#");
        const response = await page.goto(path);
        expect(response?.status(), `${link.href} should not 404`).toBe(200);

        if (anchor) {
          await expect(
            page.locator(`#${anchor}`),
            `${link.href} should have a #${anchor} target`,
          ).toBeVisible();
        }
      }
    }
  });
});
