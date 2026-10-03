---
description: Generate images, 3D assets or animation in OpenCore Assets Studio
argument-hint: /image, /3d, /3d-animation or /2d-animation followed by a request
---
$ARGUMENTS

Use the OpenCore bridge studio_use tool to list installed models and queue one
job for the requested category. The first word must explicitly select /image,
/3d, /3d-animation or /2d-animation; ask for the category if it is missing.
Compose the actual prompt and settings. Report the queued job ID and tell the
user to open Assets Studio in OpenCore. End this turn so generation can start.
The bridge will report completion; do not repeatedly poll or repeat generation.
