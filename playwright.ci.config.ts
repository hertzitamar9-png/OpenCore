import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'adaptive-layout.spec.ts',
  outputDir: './artifacts/adaptive-layout-results',
  reporter: 'line',
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:1420',
    browserName: 'chromium',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:1420',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
