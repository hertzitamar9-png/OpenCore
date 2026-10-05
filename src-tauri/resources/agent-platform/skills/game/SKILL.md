---
name: game-development
description: Use when building or changing a playable game, its movement, levels, progression, multiplayer, HUD, or game assets.
---

# Game development

Start from the user's playable outcome and the existing game architecture. Identify controls, winning and losing, progression, target devices, and the behavior the change must preserve. Inspect the actual runtime before fixing a reported problem.

Implement one complete player interaction at a time: input, simulation, visible feedback, persistence, and a way to verify it. Keep physics and game state independent of frame rate. Make collisions readable and respect deliberate player choices and unlock gates. Use isolated saves for alternate editions.

For online multiplayer, prove that separate clients exchange synchronized authoritative state. A room menu or local bots provides no evidence of a working network match. Report hosting and connection limits explicitly.

Use the connected browser or desktop tools to play the affected path, including a failure or retry. Capture actual controls, progression, errors, and rendering evidence. A build succeeding provides compile evidence; a screenshot provides appearance evidence; player interaction provides gameplay evidence.

For requested assets, use the installed compatible studio runtime. A queued studio receipt means generation was submitted; inspect its completed status and output before claiming delivery. Preserve the original prompt and supported settings. Package the actual requested playable or importable artifact and include concise controls.
