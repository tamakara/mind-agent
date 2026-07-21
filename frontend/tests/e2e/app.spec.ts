import { expect, test } from "@playwright/test";

test("renders the administration overview skeleton", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("WorkHub 管理台");
  await expect(page.getByRole("heading", { name: "概览", level: 2 })).toBeVisible();
});

test("serves client routes and health checks from the same origin", async ({ page }) => {
  await page.goto("/employees/active");
  await page.reload();

  await expect(page.getByRole("heading", { name: "概览", level: 2 })).toBeVisible();

  const health = await page.request.get("/readyz");
  expect(health.ok()).toBe(true);
  expect(new URL(health.url()).origin).toBe(new URL(page.url()).origin);
});
