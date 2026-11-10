# Validation before publishing

Checked on 10 November 2026 with Node.js 24 on Windows. The comments added for publication explain intent and safety boundaries in plain English; they do not add new execution permissions or change command behavior.

## Repeatable checks

Run these from the project directory after installing dependencies:

```powershell
npm run check:commands
npm run check:agent
npm run check:browser-tabs
npm run check:native-speech
npm run build
```

- `check:commands` exercises the 49 additional fixed grammar phrases, application aliases, command families, spoken numbers, browser snapshots, device selection, one-use approvals, on-demand results and planning cancellation. It uses stand-in tools rather than real desktop or phone actions.
- `check:agent` tests plan validation, permission gates, evidence labels, report read-back, export boundaries, uncertain-action replay prevention, restart recovery and authenticated worker dispatch with isolated data.
- `check:browser-tabs` uses simulated pages to test tab ownership, stale observations, network restrictions, popup handling and the eight-tab limit.
- `check:native-speech` checks the installed Windows speech engine with generated audio. The fixed and prefixed-dictation tests produced 17 routable transcripts. The test does not use the microphone, play speech or execute those commands. It skips on non-Windows systems and requires an English recognizer on Windows.
- `build` performs the Next.js production build and TypeScript checks.

These checks passed for the publication preparation. Additional local fixture checks covered gesture stability, echo/stop handling, desktop queues and installed-first app routing. Fixture success is not live hardware or acoustic verification.

## Still requires manual validation

1. Try two real spoken commands in sequence, including while another application is active.
2. Verify Stop interrupts speech but leaves listening on, and Stop Listening releases recognition until manually enabled.
3. Check actual application windows and the correct owned website tab, not only a launch-accepted message.
4. Test screenshots, coordinates and approved input on an authorized Android device.
5. Complete a provider-backed research/report task and inspect its sources and exported file.
6. Review actual monitoring data on the target machine; missing sensors and statistical warnings are not confirmed faults.

API keys, provider quotas, microphone permissions, network access, installed apps and ADB authorization affect live behavior. No paid model requests, malware scans, destructive repairs or real phone actions are part of the automated publication checks.

## Files kept off GitHub

`.env.local`, `.ultron`, `node_modules` and `.next` are excluded. This keeps actual keys, local conversations, telemetry, generated reports, browser profiles, dependencies and build output out of the repository. `.env.local.example` contains placeholders only. The secret-signature review is a precaution, not proof that every possible sensitive value has been detected. Keep the application local: demo sign-in is not production authentication.
