# ULTRON AI Assistant

A local-first Windows AI assistant with voice activation, a 3D gesture-controlled
orb, task planning, system automation, and health monitoring.

An Iron Man-inspired holographic orb built with **Next.js**, **Three.js**, and
**MediaPipe** hand tracking. This local build also adds a browser-based AI
assistant layer powered by Gemini by default, with an optional OpenAI fallback.

![ULTRON orb UI](docs/screenshot.png)

## Getting started

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Enable the AI assistant with Gemini

Create `.env.local` from `.env.local.example` and add your Gemini API key:

```bash
AI_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-1.5-flash
```

Restart `npm run dev` after changing environment variables. API keys stay
server-side in `app/api/ultron/route.ts`.

## Controls

### Mouse / touch

| Input | Action |
| --- | --- |
| Drag | Spin the orb |
| Scroll / pinch | Zoom in and out |

### Hand gestures

Click **GESTURES OFF** or press `G`, then allow camera access.

| Gesture | Action |
| --- | --- |
| Pinch one hand and move it | Spin the orb |
| Pinch with both hands and spread/close | Zoom in and out |

### AI assistant

| Control | Action |
| --- | --- |
| `VOICE` / `V` | Capture a spoken command with browser speech recognition |
| `WEB` | Reserved for provider web-search mode |
| `AUDIO` | Toggle browser speech synthesis for replies |
| `FILE` | Add one local text/code file as assistant context |
| `SEND` | Stream a response from the configured AI provider |
| `Esc` | Stop the current assistant response |

### System control

The **SYSTEM CONTROL** panel exposes allowlisted local actions through
`app/api/system-control/route.ts`.

| Control | Action |
| --- | --- |
| Desktop `OPEN` | Opens allowlisted Windows apps: Notepad, Calculator, Paint, Explorer |
| Browser `OPEN` | Opens a URL in a new browser tab |
| Files `FIND` | Searches Desktop, Documents, Downloads, or the project folder |
| File result buttons | Open or locally summarize PDF, DOCX, text and code documents |
| Browser `AUTOMATE URL` | Opens an isolated Edge session for permissioned browser actions |
| Browser `INSPECT`, `CLICK`, `FILL` | Shows screenshot/numbered elements; asks approval before actions |
| ADB `DEVICES` | Lists connected Android devices |
| ADB `APP` | Opens an Android app by package name |
| ADB `HOME` / `BACK` | Sends Android key events |
| ADB `TYPE` | Types text into the active Android field |
| ADB `TAP` / `SWIPE` | Sends Android touchscreen input |
| ADB `SHOT` | Reads an Android screenshot into the UI |

Android controls require Android platform-tools:

```bash
winget install Google.PlatformTools
adb devices
```

Enable Developer Options and USB debugging on the phone, connect by USB, accept
the RSA prompt, then restart `npm run dev`.

Browser automation is provided by a local Playwright controller using installed
Microsoft Edge. It never attaches to your everyday profile or tabs. The managed
session is temporary: closing it loses its cookies and page state. Popups and
downloads are blocked. Public HTTP(S) pages are supported; private-network and
controller URLs are blocked except the local `/control-sandbox` test form.
Site-specific behavior, CAPTCHAs and anti-bot restrictions can still block tasks.
Sensitive inputs and recognizable destructive, payment and authentication
buttons require manual takeover; do not use automation for high-stakes actions.

### Browser quick start

1. Open **Tools → Browser → TEST SANDBOX** to try a harmless local form.
2. Choose numbered element **1**, enter non-sensitive text, and press **FILL**.
3. Review the exact action in **Permission required**, then **Approve once**.
4. Return to Tools, choose **Submit test form**, press **CLICK**, and approve.
5. Review the resulting screenshot. Use **INSPECT** after manual navigation.

Each approval token is bound to the action and browser snapshot, expires in 60
seconds, and is consumed once. Changed pages and reused tokens are rejected.
The same approvals apply to manual controls and voice commands. Cancel discards
the pending action and any remaining steps; already-completed steps cannot be
undone. A cancelled network request cannot roll back an OS action already sent.
The server audit keeps the last 50 outcomes in memory without file/form text.

## How it works

### Welcome screen

The `/` route is a reference-inspired blue glass welcome screen with a live
Three.js orb, reflective gyroscope rings, drag rotation, scroll zoom, and opt-in
camera gestures. The existing assistant is available at `/assistant` through
**Open local assistant**. The welcome form accepts `demo@ultron.local` with password
`UltronDemo123!` and navigates to `/assistant`. **Fill demo credentials** fills the
form for testing. Incorrect values show a validation message. This is client-side
demo navigation, not an authentication or access-control system. No credentials
are transmitted or persisted, and `/assistant` remains directly accessible.
Provider buttons, account creation, and password recovery remain UI previews.
Do not enter real passwords. The renderer keeps its existing 8K pixel cap.
The welcome orb uses `lib/gyroAssembly.ts` for beveled segmented chrome bands,
a spherical cyan lattice core, vertical energy beam, radiating lines and a machined
concentric pedestal. Its procedural reflection environment avoids external
model or HDR downloads. It is a live 3D interpretation of the reference, not a
flat image or a pixel-identical reproduction.

### Assistant dashboard

After demo sign-in, `/assistant` opens the blue-glass dashboard with a responsive
sidebar, live rotating golden-ring orb hero, five prompt shortcuts, voice/audio
controls, file attachments, and a shared chat composer. Home, Chat, Knowledge,
Tools, Analytics, Projects, Automations, and Settings are local navigation views.
Search (Ctrl/Cmd+K) prepares a chat draft; it does not perform web search.
Tool cards open the existing allowlisted desktop, browser, file, and ADB controls.
Analytics shows only current-session counts and events. Projects offers a project
brief workflow, not persistent project management. Automations explicitly shows
that scheduling is not connected. Settings controls session-only interface
accents and browser audio output. No historical metrics or cloud services are
simulated as live. The welcome screen remains at `/`.

### Browser extension compatibility

ULTRON already supplies a dark theme. The root document includes Dark Reader's
`darkreader-lock` meta tag in its initial head so the extension does not recolor
the UI or inject SVG attributes before React hydrates. If an existing tab shows
a hydration warning mentioning `data-darkreader-inline-stroke`, hard-refresh
the page (Ctrl+Shift+R). If it persists, disable Dark Reader for localhost and
reload. No icon-level hydration warnings are suppressed.

### Voice system commands

Press **VOICE**, speak one command, then wait for the result. Typed commands use
the same local dispatcher and do not require Gemini credits or an API key.
The VOICE button is push-to-talk. Level 4 also provides an explicitly opt-in
hands-free listener; it is off by default.

- `Ultron, open YouTube` / `open google.com`: request a website in the default Windows browser.
- `open Notepad`, `open Calculator`, `open Paint`, `open File Explorer`.
- `close Notepad`, `close Calculator`, `close Paint`: ask for confirmation first.
  Save work before confirming; closure can affect all windows of that app.
  Explorer and arbitrary processes cannot be closed. No force-kill is used.
- `open Downloads`, `open Documents`, `open Desktop`, `open Project`.
- `find files named report in documents`: results appear in System Control.
- `open file C:\Users\your-name\Documents\notes.txt`: open a permitted document.
- `summarize file C:\Users\your-name\Documents\report.pdf`: local extractive
  summary, not a model-generated summary; PDF/DOCX/text/code, maximum 5 MB.
  Text extraction is limited to 24,000 characters and the first 30 PDF pages.
  Scanned PDF images need OCR, which is not included. **ASK AI ABOUT DOCUMENT**
  prepares a chat draft; sending that draft shares document context with the
  configured AI provider. Local reading and summaries do not need an API key.
- `browser open youtube`, `browser open sandbox`, `browser inspect`.
- `browser click 2`, `browser fill 1 with hello`, `browser scroll down`,
  `browser back`, `browser close`. Element numbers come from the current snapshot.
- `phone devices`, `phone screenshot`.
- `phone open com.android.settings`, `phone home`, `phone back`, `phone enter`,
  `phone power`, `phone tap 500 1000`, `phone swipe 500 1400 500 500`,
  `phone type hello world`: require ADB and confirmation before changing the phone.
- `phone open settings`, `phone open youtube`, `phone swipe up`,
  `phone use device SERIAL`: phone aliases, directional swipes and device selection.
- `open Notepad then open YouTube`: up to six explicit supported steps. Execution
  stops on errors and pauses for each required approval. Fills/phone typing keep
  their entire text as a single action, including the word “then”.

For confirmation, press VOICE again and say `confirm`, or type it, within 60
seconds. Say `cancel` to decline. A new command replaces the pending command.
The chat reports local tool results, not an AI's prediction of execution.
Windows launch acceptance does not guarantee a visible window or a loaded website.
System endpoints accept only same-origin localhost requests; keep this app local.
Arbitrary desktop clicking, arbitrary shell execution, payment/authentication
automation and unattended sensitive actions are not supported. Level 4 adds
bounded planning from goals through the Agent tasks workspace, not unrestricted
computer access. Unsupported direct system commands are rejected rather than sent to
the model to pretend execution. ADB is device-dependent: USB authorization and
real-phone verification are required before treating phone tasks as verified.

Keep the server bound to `127.0.0.1`. Demo sign-in is not a security boundary;
control endpoints instead enforce localhost Host/Origin checks, bounded payloads,
allowlists, canonical file paths, and one-time approvals. Do not expose this
development server to your LAN or the internet. The existing framework dependency
audit has outstanding advisories; review and patch these before any deployment.

- **`lib/orbScene.ts`** creates the Three.js scene: wireframe shells, inner core,
  floating text sprites, debris, dust particles, scan rings, bloom, and shader
  post-processing.
- **`lib/handTracker.ts`** runs MediaPipe HandLandmarker on webcam video. One
  pinched hand rotates the orb; two pinched hands zoom.
- **`app/api/ultron/route.ts`** keeps AI API keys private and streams text
  deltas from Gemini or OpenAI back to the browser.
- **`app/api/system-control/route.ts`** exposes allowlisted desktop, file, and
  Android ADB controls without allowing arbitrary shell commands.
- **`components/JarvisOrb.tsx`** connects the orb, gestures, voice capture,
  speech output, file context, web-search toggle, chat transcript, and system
  control panel.

## Level 4: Agentic ULTRON

### Task planning and execution

Open **Agent tasks** in the sidebar. Enter a goal, create a plan, review every
step, then select **Review complete · start**. Planning uses the configured
Gemini or OpenAI API and requires a working key/model/quota. Provider failures
are errors, not pretend successful tasks. No local action happens during planning.

Try:

- `Research electric vehicles in India and make a cited report.`
- `Find files named README in project, read the first result and make a report.`
- `Book me a ticket.` (ULTRON asks for missing details; it cannot buy tickets.)
- `Plan a task: open Calculator then find files named report in documents.`

Typed or spoken goals beginning with `plan`, `research`, `make/write/create a
report`, or `book a ticket` open the task planner. Each external step requires
its own one-time, 60-second approval. Browser/phone mutations may additionally
require the existing controller's exact-target approval. Reading a local document,
opening a website/API URL and sending observations to AI are separate permissions.
Replanning also asks before sharing prior observations. Review tool-result details
before approving any cloud transmission.

Supported tools include the existing allowlisted desktop controls, file search
and extraction, isolated browser navigation/inspection/click/fill/scroll, Android
ADB commands, local document retrieval and public HTTPS API **reads**. API reads
use the managed browser and cannot add authentication headers or call write APIs.
Web search reads rendered Google results; sites may block automation or need
manual consent. Reports cite observed URLs/documents and should be checked against
the original sources. It is not an unrestricted deep-research or vision agent.

Task history includes reviewed steps, actual tool results and a timestamped
activity timeline. Pause/cancel stops later steps, but cannot undo completed or
in-flight external actions. API generation can be aborted. After a crash/restart,
an interrupted task pauses rather than silently repeating an uncertain action.
Reloaded tasks do not auto-run. Use **Revise plan with observations** when new
page information changes the plan; revised plans require review again.
Financial actions, credentials, CAPTCHAs, downloads, destructive browser actions
and sensitive forms remain manual. A manual step can only be marked complete by
the user; its result explicitly states that ULTRON did not execute or verify it.

Generated Markdown reports are new, non-overwriting files in `.ultron/reports/`
and can be downloaded from the task. Arbitrary output paths are not supported.
A local worker dispatches reviewed tasks while the server runs, even with the
workspace closed. Each step still requires approval. Approved report export and
opening are limited to generated reports in Documents/ULTRON Reports; this is not
a general-purpose background scheduler or a Windows service.

### Local memory and personalization

Open **Memory** to change your display name and response preferences, resume/delete
conversations, save the current attached document to knowledge, or forget data.
Conversations save automatically after replies finish, with up to 100 messages
per conversation. The local SQLite database is `.ultron/memory.sqlite`; buckets
retain at most 200 records each. Preferences, document text, task results and
conversation history are **unencrypted**. The `.ultron` directory is Git-ignored.
Do not save credentials or highly sensitive information. Pattern-based redaction
is a precaution, not a guarantee that every kind of secret will be detected.

Sharing saved memory with the AI provider is **off by default**. Turn it on in
Memory and save preferences to include relevant keyword-retrieved document chunks,
preferences, limited recent conversations and task history in chat/planning.
Explicit report/replan permissions can share the particular task's observations
even when general memory sharing is off. Attaching a file to chat still shares
that selected context when you send a message, as in Level 3.

Document knowledge uses bounded keyword chunk retrieval, not embeddings or a
vector database. Scanned-image OCR is not included. Deleting memory removes its
database records, but does not erase downloaded/generated reports, provider-held
data, filesystem backups or copies. Use the exact deletion phrase in Memory to
clear records. Stop running tasks first. This app memory is separate from Codex
memory and does not modify it.

### Hands-free conversation

Click **Enable hands-free** to grant microphone access and start listening for
`Ultron` (or `Hey Ultron`). Say `Ultron, open Notepad`; follow-ups are accepted
for 30 seconds. During assistant speech, use the wake phrase again for a new
request; `Ultron stop` interrupts speech/chat and pauses an active task. Escape
also stops chat and pauses the selected task. Hands-free speech cannot approve
pending system/agent actions: use the on-screen approval buttons.

The listener restarts when browser recognition sessions end. It is not a native,
offline, always-on wake-word engine. Browser Web Speech support varies; recognition
may use a browser/vendor cloud service and can stop when the tab is suspended,
the network fails or the screen locks. The indicator and disable button remain
visible. Use headphones: echo cancellation and transcript matching reduce but
cannot eliminate speaker feedback. Barge-in accuracy and real microphone/Android
behavior must be verified on the user's hardware. TTS remains browser speech,
not OpenAI Realtime audio; the displayed speech waveform is synthetic, while
the microphone waveform uses real audio volume.

### Level 4 implementation map

- `lib/agentRunner.ts`: validated tool plan, bounded execution and permission gates.
- `lib/agentProvider.ts`: server-only Gemini/OpenAI JSON planning and report generation.
- `lib/assistantStore.ts`: SQLite persistence, redaction and keyword retrieval.
- `app/api/agent/route.ts`, `app/api/memory/route.ts`: same-origin localhost APIs.
- `components/AgentWorkspace.tsx`, `components/MemoryWorkspace.tsx`: task review,
  timeline, report downloads and memory management.
- `lib/useHandsFree.ts`, `lib/useConversationMemory.ts`: opt-in continuous speech
  and local conversation persistence.

No new dependency is needed for Level 4; SQLite uses Node's built-in `node:sqlite`
(Node 22.13+; tested with Node 24). Keep the app local; demo login is not real
authentication. Existing framework dependency advisories still need remediation
before any network deployment.

## Background agent orchestration

See [the command reference](docs/COMMANDS.md) for apps, folders, file searches,
managed browser actions, Android operations, scan requests, agent goals and stop
controls. The desktop listener now supports prefixed free-text command arguments
when Windows English dictation is available. Use `npm run check:commands` and
`npm run check:native-speech` for fixture and generated-audio validation.

`ULTRON, open new tab` opens a blank tab in the isolated managed Edge session,
preserving existing tabs. See [managed new tabs](docs/BROWSER-TABS.md) for scope
and limits, and run `npm run check:browser-tabs` for fixture-based checks.

Started agent tasks now use a separate local dispatcher instead of depending on
an open workspace. Exact-step approvals remain mandatory. Task evidence separates
observed results from accepted but unverified effects; uncertain interrupted
actions are not automatically replayed. Generated reports can be exported to
Documents and opened through individually approved steps.

See [the orchestration guide](docs/AGENT-ORCHESTRATION.md) for lifecycle, recovery,
verification limits and the `npm run check:agent` validation command.

## System monitoring agent

Open **System monitor** in the sidebar or `/monitor` for the independent, lightweight
System Observatory. It adds local Windows telemetry, bounded history, synthetic KB
troubleshooting, statistical trend projections, an activity timeline and reviewed
corrective actions. Collection continues with the browser/web server closed until
stopped or the Windows session ends; reboot autostart is not installed. Optional
AI analysis asks before sharing a visible telemetry snapshot. No screenshots,
keystrokes, arbitrary repairs or silent system changes are enabled.

See [the monitoring guide](docs/SYSTEM-MONITOR.md) for coverage, model assumptions,
privacy, retained data, limitations, supported fixes and worker operation.

## Validation and code comments

Plain-English comments explain the main data flows, permissions and recovery
decisions without narrating every line. See [validation](docs/VALIDATION.md) for
repeatable checks and the distinction between automated tests and live hardware
verification.

## License

MIT
