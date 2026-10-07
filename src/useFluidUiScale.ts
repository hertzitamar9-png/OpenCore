import { useLayoutEffect } from "react";

/** Keep viewport scaling separate from the user's stored font and density choices. */
export function useFluidUiScale() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    const previous = root.style.getPropertyValue("--ui-scale");
    let frame: number | undefined;
    const update = () => {
      frame = undefined;
      const ratio = Math.min(window.innerWidth / 1280, window.innerHeight / 720);
      const scale = Math.round(Math.max(0.94, Math.min(1.2, 1 + (ratio - 1) * 0.24)) * 1000) / 1000;
      root.style.setProperty("--ui-scale", String(scale));
    };
    const schedule = () => {
      if (frame === undefined) frame = window.requestAnimationFrame(update);
    };
    update();
    const observer = new ResizeObserver(schedule);
    observer.observe(root);
    window.addEventListener("resize", schedule);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", schedule);
      if (frame !== undefined) window.cancelAnimationFrame(frame);
      if (previous) root.style.setProperty("--ui-scale", previous);
      else root.style.removeProperty("--ui-scale");
    };
  }, []);
}
