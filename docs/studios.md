# Studios and category skills

Models contains the YuE2 music checkpoints alongside the other categories. Music
Studio and Models use the same pinned download and verification service. An
existing matching YuE2 installation can be registered without copying its weights.
Uninstalling that registration preserves the separate installation and its songs.

Installing a model unlocks its category skill in the composer. Skills are selected
per prompt. `/music`, `/image`, `/3d`, `/3d-animation`, `/2d-animation`, `/speech`,
`/computer-use`, and `/text` are available only for installed categories. The
browser skills use the application's existing browser connections.

The text agent uses `music_generate` to compose and submit a song and `studio_use`
to list installed models, submit exact generation
settings, inspect jobs, or cancel jobs from the same conversation. It cannot
configure or run an arbitrary program through that tool. Forms and chat submit to
the same durable SQLite queue. Queued work starts after the chat response finishes,
so the text and generation models do not compete for GPU memory. Completion is
reported in chat, and original prompts/settings, progress, errors and output files
remain visible in the corresponding studio. The queue retains requests across a
restart; interrupted work is never falsely marked complete or restarted silently.

## Backend availability

Music uses the existing local YuE2 Studio service and verifies its installation
before sending requests. Downloading music weights does not install a missing
YuE2 inference environment. A separate local YuE2 runtime is required.

Assets Studio includes offline workers for TripoSR and supported Diffusers image
pipelines. Connect a compatible Python interpreter; TripoSR also requires its
official source folder. Components retain checkpoint precision. Other 3D and
animation architectures require their upstream runtime connected through the
worker contract below. A downloaded checkpoint does not prove that its runtime
or memory requirements fit the current hardware. The app reports missing setup
and generation errors instead of fabricating outputs.

Speech uses the existing transcription service for explicitly selected audio
files and does not turn on microphone dictation as a side effect.

## Additional asset workers

Choose a local Python interpreter and a trusted `.py` worker in Assets Studio.
The application invokes them directly, without a shell:

```text
python -u worker.py --request REQUEST_JSON --output OUTPUT_DIRECTORY
```

The UTF-8 request includes `modelId`, `category`, `prompt`, `settings`,
`conversationId`, `modelRoot`, and `sourceDir`. Model weights are under
`modelRoot/models/library/modelId`. File inputs are in `settings.inputPath`.
Chat inputs must belong to that conversation's attachments or generated outputs.

Write generated images, audio, videos, meshes or motion files beneath the output
directory. Exit zero only when generation succeeds. Optional `progress.json`
contains an object with a `stage` string and backend-specific progress values.
Update it atomically. Standard output and errors are retained in `generation.log`.
The app validates output paths, skips symbolic links, and never treats a zero exit
with no output as a successful generation. Cancellation stops the owned worker
and preserves partial files.

Workers are local executable code: connection happens through the user's file
picker, never through model-generated executable paths. Do not have workers
silently download weights, alter precision, or launch detached GPU processes.

## Local integration verification

`scripts/verify-studio-integration.mjs` runs one explicit ECHO-to-YuE2 audio
integration smoke against a running debug OpenCore WebView with CDP enabled.
It requires `OPENCORE_STUDIO_SMOKE_DIR`, verified existing checkpoints, and an
idle queue. It is not run automatically in CI and is not a model-quality benchmark.
