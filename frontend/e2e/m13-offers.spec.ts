import { expect, test, type Page } from "@playwright/test";

/**
 * The offers panel on the advisor page.
 *
 * Two properties only a browser can settle. First that the panel does **not**
 * search on load — the free allowance is a couple of hundred searches a month
 * for the whole deployment, so a search fired automatically would spend the
 * budget on people who never asked (ADR-008). Second that simulated prices are
 * labelled as such, because a panel of invented prices presented as real
 * listings is a false claim about named retailers.
 *
 * Runs against the simulated provider, which is the CI and local default.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

async function evaluateSomething(page: Page) {
  const email = `m13-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await page.getByTestId("load-demo").click();
  await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 40_000 });

  await page.goto("/advisor");
  await page.getByLabel("What are you thinking of buying?").fill("macbook pro");
  await page.getByTestId("search-products").click();
  await expect(page.getByTestId("search-results")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Should I?" }).first().click();
  await expect(page.getByTestId("advice-card")).toBeVisible({ timeout: 20_000 });
}

test.describe("M13 offers", () => {
  test("nothing is searched until asked", async ({ page }) => {
    const calls: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/market/offers")) calls.push(r.url());
    });

    await evaluateSomething(page);

    await expect(page.getByRole("button", { name: "Look for a better price" })).toBeVisible();
    expect(calls, "the panel must not spend quota on page load").toHaveLength(0);
  });

  test("offers are cheapest first, and simulated prices say so", async ({ page }) => {
    await evaluateSomething(page);
    await page.getByRole("button", { name: "Look for a better price" }).click();

    const panel = page.getByTestId("offers-panel");
    await expect(panel).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("offer-list")).toBeVisible({ timeout: 20_000 });

    // Ascending price is the only ordering claim the panel makes.
    const prices = await page.getByTestId("offer-list").locator("li p.tabular").allInnerTexts();
    const numeric = prices.map((p) => Number(p.replace(/[^0-9.]/g, "")));
    expect(numeric.length).toBeGreaterThan(1);
    expect([...numeric].sort((a, b) => a - b)).toEqual(numeric);

    await expect(page.getByTestId("offer-caveats")).toContainText(/simulated/i);
  });

  test("the advice itself is unaffected by the offers panel", async ({ page }) => {
    await evaluateSomething(page);
    const verdict = await page.getByTestId("verdict").innerText();

    await page.getByRole("button", { name: "Look for a better price" }).click();
    await expect(page.getByTestId("offers-panel")).toBeVisible({ timeout: 20_000 });

    // The affordability answer never depended on a price comparison, and a
    // paused or empty one must not change it.
    await expect(page.getByTestId("verdict")).toHaveText(verdict);
  });
});
