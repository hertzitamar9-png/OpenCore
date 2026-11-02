# Shared workspaces and durable agent activity

The user wants OpenCore groups to be a substantial place to work with friends and local or API agents. Continue the current PC-hosted, pinned-TLS design. The user has asked for autonomous implementation rather than repeated design approvals.

## User-visible behavior

- Groups open a spacious workspace on desktop and a phone layout on Android, preserving existing private chats.
- A group has channels and named projects. People can discuss one project together, keep separate project channels, or open a private direct conversation with a person or an agent.
- Each PC owner shares named agents backed by installed local models or configured API models. The same model can back several agents with different names, instructions and reply policies.
- Each agent can reply to mentions, every human message, or explicit selection only. Owners can restrict callers and channels. A message can follow those rules, select specific agents, or address humans only. Agent replies never recursively trigger automatic replies.
- Project names are shared, while each owner maps a shared project to an existing local OpenCore project. Local paths, credentials and unrelated conversations are not published to members. All tools continue to use the executing owner's selected approval policy.
- Queue, cancel, plans, live tool activity and answers remain attributable to the right agent, channel and PC. Cancelling work is checked by the worker and stops its local request. No remote interaction focuses the desktop window.
- The group UI discovers configured API profiles without exposing API keys; those profiles use the existing native execution path, including normal tool approvals and saved activity.

## Reliability

ECHO must retain bounded exact no-progress fingerprints across context handoffs and refuse repeated stalled actions before rerunning them. Successful changed work and bounded live polling remain possible. Tool records and available model summaries must remain visible through stream checkpoints, completion and navigation. A stopped loop has an explicit error; it is not presented as successful work. No universal promise about model quality or zero repetition is made.

Every conversational text-generative catalog family gets Native/ECHO metadata and the same physical quantizations in both modes. Speech-only transcribers, image-only models and embeddings are not conversational models. Unsupported architectures retain truthful runtime limitations. Add the exact two requested repositories: ccore/Qwen3.5-4B-GLB-3D-Coder and microsoft/Mage-VL-Cowork, with genuine publisher/community packages and verified lineage.

## Approaches

1. Extend the existing direct host, task leases, local worker and native API provider path (chosen). This preserves chats, avoids distributing secrets and keeps remote tools owner-controlled.
2. Add a cloud relay and new account system. This contradicts the user's local-data preference and creates unrelated infrastructure.
3. Replace the recovered native application. Matching complete native source is unavailable, so that risks startup and installer regressions. Use reviewed data/frontend/helper changes within the verified package process.

## Constraints

Use main only, no new worktrees. Preserve unrelated files, current chats, model files, credentials, current app process and the permanent byte-pinned Setup.exe. Never install external bridges or import histories by default. Publish only after source, rendered UI, isolated startup/install, incremental updater and public signature/feed checks pass. Native context target is 1,048,576 only within actual support; ECHO target is 32,768 within actual support. API tests use isolated protocol fixtures, not paid user credentials.
