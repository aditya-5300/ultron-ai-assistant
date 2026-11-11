# ULTRON command reference

Prefix spoken commands with `ULTRON`. Enable either desktop voice or browser hands-free, not both. Text commands use the same routing. Reloading a production app requires a server restart after rebuilding.

## Stop controls

- `ULTRON, Stop` / `Cancel` / `Wait`: interrupt the current response, clear queued work and pause agent tasks without disabling listening. Already-started external actions may finish.
- `ULTRON, Stop Listening` / `Disable Voice`: release recognition and keep voice disabled until manually enabled again. The server stays running.
- `ULTRON, show commands`: display command help in chat or the desktop voice result panel.

Voice cannot approve sensitive actions. Use the displayed approval button. A supported command is not a guarantee that the microphone transcribed it correctly.

## Apps

Use `open`, `launch` or `start` with Notepad, Calculator, Paint, File Explorer (File Manager), Task Manager, VS Code (Visual Studio Code), WhatsApp, Spotify, Telegram, Instagram, YouTube, Gmail, Google or GitHub.

Examples: `ULTRON, open Notepad`, `ULTRON, open YouTube`, `ULTRON, open WhatsApp`.

Use `close`, `quit` or `exit` with a supported application to request closing. Approval is required. Desktop closing is limited to supported identifiable windows; website closing is limited to app-owned tabs. Shared host/PWA processes and personal browser tabs may require manual handling. File Explorer is not force-killed. Installed-first discovery is best-effort, not proof an unknown custom installation is absent.

## Files, folders and drives

- `ULTRON, open Desktop` / `Documents` / `Downloads` / `project`
- `ULTRON, open D drive`
- `ULTRON, open File Manager and D drive`
- `ULTRON, find files named report in documents`
- `ULTRON, find files named README in project`
- `ULTRON, open file "C:\Users\adity\Documents\notes.txt"`
- `ULTRON, summarize file "C:\Users\adity\Documents\notes.txt"`

Use an actual existing path. Readable files must be inside the permitted roots, supported types and size limits. Hidden files, key files and credential paths are rejected. Search is bounded to 2,500 entries and 24 results. Speech can mishear exact filenames/paths; type them when precision matters. Document summaries are local extractive summaries, not an unrestricted document agent.

## Managed browser

- `ULTRON, open new tab` / `open a new tab` / `create a new tab`
- `ULTRON, browser open YouTube` / `Google` / `GitHub`
- `ULTRON, browser open example.com` (`example dot com` is also understood)
- `ULTRON, browser inspect`
- `ULTRON, browser click 1` (`one` also works)
- `ULTRON, browser fill 2 with hello` (`two` also works)
- `ULTRON, browser scroll up` / `browser scroll down`
- `ULTRON, browser back`
- `ULTRON, browser close`

This is an isolated Edge session, not your personal browser. Inspect first and use current element IDs (1–60). Click, fill, scroll and back require approval bound to the exact snapshot and target. The desktop listener now retains snapshots and shows element IDs in its latest result panel. Changed pages/targets are rejected until inspected again. `Browser close` closes the isolated session, not just one tab. Popups/downloads and sensitive operations remain restricted. The existing `browser refresh` alias only inspects the page; use `browser inspect` rather than treating it as a reload command.

## Android (authorized ADB required)

- `ULTRON, list phone devices`
- `ULTRON, phone use device DEVICE_ID`
- `ULTRON, phone screenshot`
- `ULTRON, phone open YouTube` / `Chrome` / `settings` / `camera`
- `ULTRON, phone open com.android.settings`
- `ULTRON, phone home` / `back` / `enter` / `power`
- `ULTRON, phone swipe up` / `down` / `left` / `right`
- `ULTRON, phone tap 300 500`
- `ULTRON, phone tap three hundred comma five hundred`
- `ULTRON, phone type hello`

Screen-changing actions need approval. Coordinates must be inside the actual device screen. Ambiguous number pairs are rejected; using a comma between coordinates is clearer. Device selection is retained, not silently switched to another phone. Package names vary; ADB authorization, installed apps and screen behavior require device testing. Phone text is limited to 500 English letters/numbers/spaces and supported basic punctuation, with shell symbols rejected.

## Security scan

`ULTRON, scan my computer` or `ULTRON, run a Defender quick scan` requests a Windows Defender quick scan with approval. It can consume resources and Defender may quarantine according to existing policy. No protection settings are changed. A timeout is reported as uncertain, not a successful or malware-free scan.

On Windows, say or type `ULTRON, shut down the PC` or `ULTRON, restart the PC` as a standalone command. Nothing is scheduled until you click **Approve once**. Save your work. During the 60-second countdown, use the banner's Cancel button or say or type `ULTRON, cancel shutdown` / `ULTRON, cancel restart`. Closing the browser does not cancel a countdown; stopping the ULTRON server before it submits the Windows request does. Once submitted to Windows, the countdown cannot be cancelled from ULTRON. ULTRON requests normal shutdown or restart without forcing apps closed. These commands are not available to agent plans.

## Agent goals (AI provider required)

- `ULTRON, research SSD versus HDD and make a cited report`
- `ULTRON, research electric vehicles in India, save a report in Documents and open it`
- `ULTRON, make a report about the attached document`
- `ULTRON, book me a ticket`

These create saved plans for review, not immediate unrestricted actions. Recognized goals and opted-in memory go to the configured AI provider. Inspect recognized wording before proceeding. Purchases/login/destructive operations remain manual. A booking needs destination, dates and traveler details; ULTRON does not purchase a ticket. A document must be attached/saved to knowledge or have an accessible path; saying “attached document” does not grant the desktop listener access to an arbitrary foreground document. Review all sources and report claims.

## Chains and text arguments

Join up to six supported direct actions with `then`: `ULTRON, open Notepad then open Calculator` or `ULTRON, close YouTube then open VS Code`. Later steps stop on an error or permission request. The remainder of `phone type …` and `browser fill … with …` is literal text; do not append another operation to those text fields.

## Validation

`npm run check:commands` covers parser and desktop dispatch fixtures, snapshots, device selection, approvals, result retrieval and voice planning cancellation. `npm run check:native-speech` runs synthesized audio through the installed Windows speech engine and validates that emitted transcripts route to supported handlers. No real microphone, app, scan, phone or provider is used by these checks. Synthetic speech success is not live microphone or hardware verification.
