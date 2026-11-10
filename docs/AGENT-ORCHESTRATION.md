# Bounded agent orchestration

ULTRON reuses its existing planner, SQLite task store, memory retrieval, desktop/browser/ADB tools and permission gateway. It does not introduce an independent assistant or unrestricted shell executor.

## Execution lifecycle

1. Create a plan. The configured provider receives the goal and any opted-in memory.
2. Review and start it. This opts this task into background dispatch.
3. A hidden local Node worker requests the next ready step, even when the workspace is closed.
4. Each step pauses for its existing exact, one-use approval. The worker never supplies approval tokens.
5. Tool output is stored with a verified-observation or acknowledged-effect label. An accepted launch is not proof that an application window appeared.
6. Pause, cancel or `ULTRON Stop` prevents further scheduling. An in-flight OS action cannot necessarily be undone.

The separate worker is a dispatcher, not an isolated tool sandbox. Existing server-side tool implementations still perform the approved work. It needs the local Next server to remain running. It is not a Windows service and does not survive shutdown. No Windows autostart is installed.

## Recovery and safety

- Tasks and results persist in `.ultron/memory.sqlite`.
- After a server restart, previously ready/running/approval background tasks are paused when the agent endpoint is first used. They are not silently replayed.
- A step marked started without a stored result has an uncertain outcome. Retrying it automatically is blocked: inspect the external system and revise the plan instead. Even read-only failures currently take this conservative path.
- Permission requests that have not reached a tool can be requested again after review. Approvals expire and remain action-bound.
- Worker authentication uses a random per-server bearer token passed only to the child environment. It is not returned to the UI or written to disk. Provider keys are not inherited by the dispatcher.
- Worker restarts have bounded backoff. Its child process exits after its parent disappears or worker authorization is rejected. Repeated failure is shown in the workspace.
- Browser instructions, retrieved memory and documents remain untrusted input. Purchases, logins and destructive operations still require manual takeover.

## Evidence is not universal verification

- Browser navigation/inspection: observed URL and title, not verified web claims or completed transactions.
- File/knowledge search: actual returned entries, subject to scan limits.
- Report: new file write followed by exact content read-back; this does not fact-check generated prose.
- Desktop launches, browser clicks/fills and phone actions: acknowledged unless a specific postcondition is independently available.
- Manual steps: user-reported, not independently verified.

Legacy task results have no new evidence label and are displayed as unverified. Generated reports remain in `.ultron/reports`, with a user-triggered download. A plan can include `exportReport` to save a new copy in `Documents/ULTRON Reports` and `openReport` to open that exact exported file. Both steps require approval; neither accepts model-specified paths. Exports never overwrite existing files and are read back for verification. Opening is acknowledged, not proof of a visible editor window. Custom/redirected Documents locations outside the conventional user Documents folder are not discovered automatically.

Example goal: “Research electric vehicles in India, make a cited report, save it in Documents and open it.” Review source observations and each permission request. Search snippets alone are not sufficient evidence for a detailed factual report; open source pages and replan from their observations when necessary. Browser element IDs cannot be invented by the planner, so interactive multi-page tasks may require inspection and a user-reviewed replan.

## Validation

Run `npm run check:agent` and `npm run build`. The self-check uses isolated in-memory records and tool/provider fixtures. It covers approval gating, no replay of uncertain actions, evidence classification, report read-back, restart recovery, authenticated dispatch and pause-all without changing the user's computer. It does not establish live microphone accuracy, browser appearance, or end-to-end research quality.
