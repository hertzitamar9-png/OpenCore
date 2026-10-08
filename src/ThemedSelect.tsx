import { createElement, forwardRef, type ComponentPropsWithoutRef } from "react";
import "./ThemedSelect.css";

/** Standard select semantics with the app's themed, browser-rendered picker. */
export const ThemedSelect = forwardRef<HTMLSelectElement, ComponentPropsWithoutRef<"select"> & { truncate?: boolean }>(
  function ThemedSelect({ className = "", truncate = false, children, ...props }, ref) {
    return <select {...props} ref={ref} className={`themed-select ${className}`.trim()}>
      {truncate ? <button type="button" className="themed-select-button" tabIndex={-1} aria-hidden="true">{createElement("selectedcontent")}</button> : null}
      {children}
    </select>;
  },
);
