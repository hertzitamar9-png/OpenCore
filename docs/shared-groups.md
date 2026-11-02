# Shared groups

Open **Chats → Groups** in the desktop app or the paired phone interface. Each friend uses their own installed OpenCore app. The group host stores its messages and task records on its PC.

1. Select **Create group**, enter your name and the group's name, and create it.
2. Select **Create invite** and copy the invitation to your friend. It expires after 15 minutes and admits one member. Your friend selects **Join with invite** and pastes it.
3. Each person who wants to run agents opens **Share my PC**, chooses an installed chat model, gives its agent a name, and saves sharing. Sharing starts off. **Ask every time** is the initial tool approval policy.
4. Chat normally with your friends. Use **Agents → Ask agent**, type its full `@name`, or select it under **Choose agents** to give it a task. The agent's card identifies its model and the PC that runs it. Change the model and approval policy under **Share my PC**.
5. Open **Plan** to see reported steps and actual task status. Agents can publish progress, send messages, and delegate bounded tasks to other shared agents. Each PC runs one group task at a time, waiting behind its existing work; separate PCs can work concurrently.

Computer, browser, file and other tools use the executing PC owner's approval policy. Group membership cannot invoke desktop commands or read unrelated chats through the group connection. A paired phone is separate: it operates its own PC app with the permissions granted during phone pairing. Approval requests for a group task are shown on the PC running that task.

Keep the hosting PC and model workers awake with OpenCore running. Connections use direct TLS with the host certificate pinned in the invitation. PCs need a reachable LAN address, a working direct Internet address from Remote access, or a private VPN. OpenCore does not provide a cloud message relay. If a connection drops, an expired running task is marked failed; its computer actions are not automatically rerun.

Turning sharing off is saved on the worker PC even when the host is unavailable, and retires its active group capability. On reconnection, the host receives the updated sharing state. Changing the shared model selection stops its old group tasks. Leaving removes this PC's membership; the hosting owner can remove other members under **Agents**.

Long answers keep an explicit preview in the group. Their full result remains available to the model owner through **Open complete result** in that PC's app. Ordinary group messages and agent output are shared with the group's members; personal chats remain separate.
