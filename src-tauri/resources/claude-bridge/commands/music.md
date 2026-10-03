---
description: Generate music in OpenCore Music Studio
argument-hint: Song request
---
/music $ARGUMENTS

Use the OpenCore bridge studio_use tool to list installed models, then compose
the requested title, style, lyrics and generation settings and queue one music
job. Report the queued job ID and tell the user to open Music Studio in OpenCore.
End this turn so the app can release the text model and start generation. The
bridge will report completion; do not repeatedly poll or regenerate the song.
