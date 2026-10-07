import { forwardRef, type ComponentPropsWithoutRef } from "react";
import "./ThemedSelect.css";

/** Standard select semantics with the app's themed, browser-rendered picker. */
export const ThemedSelect = forwardRef<HTMLSelectElement, ComponentPropsWithoutRef<"select">>(
  function ThemedSelect({ className = "", ...props }, ref) {
    return <select {...props} ref={ref} className={`themed-select ${className}`.trim()} />;
  },
);
