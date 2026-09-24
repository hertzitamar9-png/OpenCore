import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(globalThis, "ResizeObserver", {
  writable: true,
  configurable: true,
  value: TestResizeObserver,
});

Object.defineProperty(HTMLElement.prototype, "scrollTo", {
  writable: true,
  configurable: true,
  value: () => {},
});

Object.defineProperty(HTMLElement.prototype, "scrollTo", {
  writable: true,
  configurable: true,
  value: () => undefined,
});
