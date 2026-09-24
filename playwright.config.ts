import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "C:/Users/hertz/AppData/Local/Temp/opencore-control-center-qa/results",
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:1420",
    channel: "chrome",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
