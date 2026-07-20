import { expect, test } from "@playwright/test";

test("renders the administration overview skeleton", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("WorkHub 管理台");
  await expect(page.getByRole("heading", { name: "概览", level: 2 })).toBeVisible();
});
