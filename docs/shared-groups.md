# Chat Core

Open **Chat Core** from OpenCore's navigation rail or the Chats sidebar. The same workspace is available in the paired phone interface. The group host stores shared messages and task records on its PC.

## Start with friends

1. Open **All groups**, select **Create group**, and enter your name and the group's name. On a narrow screen, open **Browse** first.
2. Select **Invite people** and send the invitation to your friend. It expires after 15 minutes and admits one member. Your friend selects **Join with invite** in **All groups**.
3. Each PC owner opens **Settings → Agents on this PC**, chooses local models and quantizations, creates named roles, and saves sharing. Planner, Coder, Reviewer and Tester templates are available. Several roles can use one model, or different roles can use different models. Sharing starts off; tool approval starts at **Ask every time**.
4. Supported catalog models can be selected before download. For each missing model, explicitly enable **Install and prepare this model before its first task**. Normal installation admission, progress and generation verification apply. Publisher terms, runtime compatibility and hardware limits still apply.
5. Open **People & agents → Build a team** to combine roles on one PC or across friends' PCs. One PC can host several roles and queue its work; different PCs can run concurrently. Optional configured API models use credentials stored only on the executing PC. Their task context is sent to that configured provider.

## Conversations and projects

Use **People & agents → Ask agent**, an `@name` or alias, or **Choose agents**. Each message can follow agent rules, select particular agents, or use **Humans only**. Each role can answer mentions, every human message, or explicit selection only. Agent replies never automatically start another round of replies.

Create channels for separate conversations. The owner can create named shared projects and connect channels to them. Each participating PC maps a shared project to an existing local OpenCore project; different PCs can use different folders for the same project. Folder paths stay local. A task and its delegated children retain the project assigned when the task was created.

Use **Direct chat** beside a person or agent for a private channel. Only its participants can read its messages, plans and activity through the group connection. An agent's PC owner is included when their agent is a participant. Private records remain on the group host, whose owner controls that machine's storage.

**Tasks** shows actual task steps, activity, preparation progress and terminal status. Cancellation stops that task's own chat and associated studio job. Agents can report progress and explicitly delegate bounded tasks; queued work is not presented as completed work. A later error remains a failure even if partial answer text exists.

## Advanced controls

Each role has enable/pause, reasoning effort, tool approval, subagent enablement/limit, recent channel history from zero to 100 messages, delegation permission, acceptance of delegated tasks, and queue priority. Role subagents remain capped by the PC-wide group limit. This PC's apps, the in-app browser and paired Chrome are enabled separately; tool approval still applies. Instructions and execution controls stay in the owner PC's configuration. Member/channel allowlists and aliases determine where a role can answer. Narrowing access retires local task capabilities even if the host is unavailable.

Under **Settings → Display**, density, timestamps and PC names apply on this device. Selected channels and unsent drafts survive reopening. **Settings → Membership** contains the option to leave a group or delete a group you host.

## Connection and storage

Keep the host and workers awake with OpenCore running. Connections use direct TLS pinned in the invitation. PCs need a reachable LAN address, direct Internet address from Remote access, or private VPN. There is no cloud message relay. Membership does not grant native-app access, unrelated chats or phone-pairing permissions.

Each PC runs one group task at a time behind existing work. Lost or expired tasks are marked failed rather than rerunning computer actions. Turning sharing off is saved locally and revokes active capabilities; the host receives it on reconnection. Long answers have an explicit preview; **Open complete result** on the model owner's PC opens its saved full answer. Shared activity excludes the owner's private submission instructions, although the chosen model can reflect its instructions in answers.
