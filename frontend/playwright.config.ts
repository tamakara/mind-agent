import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: "html",
  webServer: {
    command: "node scripts/serve-dist.mjs",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
  },
  use: {
    baseURL: process.env.WORKHUB_E2E_BASE_URL ?? "http://127.0.0.1:5173",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
