import * as React from "react";

const effects = {
  barrel_roll: ["Barrel roll", "do a barrel roll", "/barrel-roll"],
  tilt: ["Tilt", "tilt the app", "/tilt"],
  constellation: ["Constellation", "show a constellation", "/constellation"],
  confetti: ["Confetti", "launch confetti", "/confetti"],
  pulse: ["Pulse", "pulse the app", "/pulse"],
};
const eventName = "opencore-local-effect";

export function playAppEffect(text) {
  const prompt = text.trim().toLowerCase().replace(/[.!?]+$/, "").replace(/\s+/g, " ");
  const effect = Object.entries(effects).find(([, phrases]) => phrases.some(phrase => phrase.toLowerCase() === prompt));
  return !!effect && !window.dispatchEvent(new CustomEvent(eventName, { detail: effect[0], cancelable: true }));
}

export function useAppEffects(configuration, listen) {
  const [effect, setEffect] = React.useState("");
  const enabled = configuration?.easterEggsEnabled === true;
  React.useEffect(() => {
    if (!enabled) { setEffect(""); return; }
    let timer, frame, closed = false;
    const play = name => {
      if (closed || name !== "reset" && !Object.hasOwn(effects, name)) return false;
      clearTimeout(timer);
      cancelAnimationFrame(frame);
      setEffect("");
      if (name !== "reset") {
        frame = requestAnimationFrame(() => {
          setEffect(name);
          timer = setTimeout(() => setEffect(""), 1200);
        });
      }
      return true;
    };
    const local = event => { if (play(event.detail)) event.preventDefault(); };
    window.addEventListener(eventName, local);
    const native = listen?.("opencore-easter-egg", ({ payload }) => play(payload?.name)).catch(() => null);
    return () => {
      closed = true;
      clearTimeout(timer);
      cancelAnimationFrame(frame);
      window.removeEventListener(eventName, local);
      void native?.then(stop => stop?.());
    };
  }, [enabled, listen]);
  const reduced = configuration?.appearance?.reducedMotion || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const name = effects[effect]?.[0];
  return {
    className: effect ? "app-effect-" + (reduced ? "static" : effect) : "",
    overlay: name ? React.createElement("div", { className: "app-effect-caption", role: "status" },
      effect === "constellation" ? "✦  " + name + "  ✦" : name) : null,
  };
}

export function AppEffectPreviews() {
  return React.createElement("div", { className: "platform-toolbar app-effect-previews", "aria-label": "Preview Easter eggs" },
    Object.values(effects).map(([name, phrase]) => React.createElement("button", {
      key: name, type: "button", onClick: () => playAppEffect(phrase), title: "In chat, type “" + phrase + "”",
    }, name)));
}
