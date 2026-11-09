import * as React from "react";

const effects = {
  barrel_roll: ["Barrel roll", "do a barrel roll", "/barrel-roll", "do a barrel rol", "do a better role"],
  tilt: ["Tilt", "tilt the app", "/tilt"],
  constellation: ["Constellation", "show a constellation", "/constellation"],
  confetti: ["Confetti", "launch confetti", "/confetti"],
  pulse: ["Pulse", "pulse the app", "/pulse"],
  bounce: ["Bounce", "bounce the app", "/bounce"],
  glow: ["Glow", "light up the app", "/glow"],
  ripple: ["Ripple", "ripple the app", "/ripple"],
  spotlight: ["Spotlight", "show a spotlight", "/spotlight"],
  dance: ["Dance", "make the app dance", "/dance"],
};
const eventName = "opencore-local-effect";
const customKey = "opencore.local-effect.v1";

function localEffect() {
  try {
    const recipe = JSON.parse(localStorage.getItem(customKey) || "null");
    return validateEffect(recipe);
  } catch { return null; }
}

function validateEffect(recipe) {
  if (!recipe || typeof recipe.label !== "string" || typeof recipe.symbol !== "string" ||
      !["dance", "bounce", "float"].includes(recipe.motion)) return null;
  const label = recipe.label.trim(), symbol = recipe.symbol.trim();
  if (!label || label.length > 48 || !symbol || [...symbol].length > 8 || /[\p{Cc}\p{Cf}]/u.test(label)) return null;
  return { label, symbol, motion: recipe.motion };
}

export function playAppEffect(text) {
  const prompt = text.trim().toLowerCase().replace(/[.!?]+$/, "").replace(/\s+/g, " ");
  const effect = Object.entries(effects).find(([, phrases]) => phrases.some(phrase => phrase.toLowerCase() === prompt));
  const name = effect?.[0] ?? (prompt === "/custom-effect" && localEffect() ? "custom" : "");
  return !!name && !window.dispatchEvent(new CustomEvent(eventName, { detail: name, cancelable: true }));
}

export function useAppEffects(configuration, listen) {
  const [effect, setEffect] = React.useState(null);
  const enabled = configuration?.easterEggsEnabled === true;
  React.useEffect(() => {
    if (!enabled) { setEffect(null); return; }
    let timer, frame, closed = false;
    const play = (name, agent = false) => {
      const recipe = name === "custom" || agent && name === "constellation" ? localEffect() : null;
      if (closed || !recipe && name !== "reset" && !Object.hasOwn(effects, name)) return false;
      clearTimeout(timer);
      cancelAnimationFrame(frame);
      setEffect(null);
      if (name !== "reset") {
        frame = requestAnimationFrame(() => {
          setEffect(recipe ? { name: "custom", ...recipe } : { name, label: effects[name][0] });
          timer = setTimeout(() => setEffect(null), recipe ? 2400 : 1200);
        });
      }
      return true;
    };
    const local = event => { if (play(event.detail)) event.preventDefault(); };
    window.addEventListener(eventName, local);
    const native = listen?.("opencore-easter-egg", ({ payload }) => play(payload?.name, true)).catch(() => null);
    return () => {
      closed = true;
      clearTimeout(timer);
      cancelAnimationFrame(frame);
      window.removeEventListener(eventName, local);
      void native?.then(stop => stop?.());
    };
  }, [enabled, listen]);
  const reduced = configuration?.appearance?.reducedMotion || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  return {
    className: effect ? "app-effect-" + (reduced ? "static" : effect.name) : "",
    overlay: effect ? React.createElement("div", { className: "app-effect-caption", role: "status" },
      effect.symbol && React.createElement("span", {
        className: "app-effect-symbol" + (reduced ? "" : " motion-" + effect.motion), "aria-hidden": true,
      }, effect.symbol), effect.name === "constellation" ? "✦ " + effect.label + " ✦" : effect.label) : null,
  };
}

export function AppEffectPreviews() {
  const [saved, setSaved] = React.useState(localEffect);
  const [draft, setDraft] = React.useState(() => saved ? JSON.stringify(saved, null, 2) : "");
  const [status, setStatus] = React.useState("");
  const h = React.createElement;
  const save = () => {
    try {
      const recipe = validateEffect(JSON.parse(draft));
      if (!recipe) throw Error("Use a short label, a symbol and a supported motion.");
      localStorage.setItem(customKey, JSON.stringify(recipe));
      setSaved(recipe);
      setStatus("Effect saved on this device.");
    } catch (error) { setStatus(String(error.message || error)); }
  };
  return h(React.Fragment, null,
    h("div", { className: "platform-toolbar app-effect-previews", "aria-label": "Preview Easter eggs" },
      Object.values(effects).map(([name, phrase]) => h("button", {
        key: name, type: "button", onClick: () => playAppEffect(phrase), title: "In chat, type “" + phrase + "”",
      }, name))),
    h("details", { className: "app-effect-editor" },
      h("summary", null, "Custom effect on this device"),
      h("label", null, "Effect recipe",
        h("textarea", { rows: 5, maxLength: 1024, value: draft,
          placeholder: JSON.stringify({ label: "Dancing robot", symbol: "🤖", motion: "dance" }),
          onChange: event => setDraft(event.currentTarget.value) })),
      h("small", null, "Ask your Brain for a recipe with label, symbol and motion. Motions are dance, bounce and float. A saved recipe plays when the agent calls Constellation."),
      h("div", { className: "platform-toolbar" },
        h("button", { type: "button", onClick: save }, "Save effect"),
        h("button", { type: "button", disabled: !saved, onClick: () => playAppEffect("/custom-effect") }, "Preview effect"),
        h("button", { type: "button", disabled: !saved, onClick: () => {
          try { localStorage.removeItem(customKey); setSaved(null); setDraft(""); setStatus("Custom effect removed."); }
          catch (error) { setStatus(String(error.message || error)); }
        } }, "Remove effect")),
      status && h("small", { role: "status" }, status)));
}
