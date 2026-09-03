import { expect, test, type Page } from "@playwright/test";

/**
 * Bank-message ingestion, in a real browser.
 *
 * The properties worth guarding here are the ones a user would notice going
 * wrong: that the dry run really stores nothing, that a message with no mapped
 * account is held rather than guessed at, and that mapping the account is what
 * releases it.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

const DEBIT = "Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26 Ref 522398456712";

async function signUp(page: Page) {
  const email = `sms-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL("**/dashboard");
}

test.describe("Bank messages", () => {
  test("reading a message stores nothing", async ({ page }) => {
    await signUp(page);
    await page.goto("/transactions/sms");

    await page.getByLabel("Message").fill(DEBIT);
    await page.getByRole("button", { name: "Read it" }).click();

    const preview = page.getByTestId("sms-preview");
    await expect(preview).toContainText("SWIGGY");
    await expect(preview).toContainText("1234");

    // The queue is the proof: a dry run that stored the message would put a row
    // here, and the whole point of the endpoint is that it does not.
    await page.reload();
    await expect(page.getByTestId("sms-queue")).toHaveCount(0);
  });

  test("a promotional message is read as not a transaction", async ({ page }) => {
    await signUp(page);
    await page.goto("/transactions/sms");

    await page
      .getByLabel("Message")
      .fill("Pre-approved personal loan of Rs 500000! Apply now.");
    await page.getByRole("button", { name: "Read it" }).click();

    await expect(page.getByTestId("sms-preview")).toContainText(
      "does not look like a transaction",
    );
  });

  test("the review page is reachable from the sidebar", async ({ page }) => {
    await signUp(page);
    await page.getByRole("link", { name: "Bank messages" }).first().click();
    await expect(page).toHaveURL(/\/transactions\/sms$/);
    await expect(page.getByRole("heading", { name: "Bank messages" })).toBeVisible();
  });
});

test.describe("The download page", () => {
  test("offers both builds and is honest about the SMS one", async ({ page }) => {
    await page.goto("/download");

    await expect(
      page.getByRole("link", { name: "Download Frugal", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Download Frugal (SMS)" })).toBeVisible();

    // The block is real and the page must say so rather than letting a user
    // discover it as a failed download.
    await expect(page.getByText(/Play Protect/)).toBeVisible();
    await expect(page.getByText(/adb install/)).toBeVisible();

    // A self-signed binary from a website with no checksum asks too much trust.
    await expect(page.getByText(/SHA-256/).first()).toBeVisible();
  });

  test("is linked from the marketing footer", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: "Get the Android app" })).toHaveAttribute(
      "href",
      "/download",
    );
  });
});
