# Managed new tabs

Say or type `ULTRON, open new tab`. `Open a new tab`, `create a new tab`, `browser open new tab` and `new tab` are also supported. This opens and selects a blank tab in ULTRON's isolated Edge session, not your personal Edge window. The desktop speech grammar supports these phrases too; actual microphone recognition still depends on the recording input.

Existing managed tabs remain open. The latest explicitly opened tab becomes the automation target. `Browser open youtube` navigates that target tab. No arbitrary URL can be passed to `browserNewTab`; website navigation still goes through the public-URL validation and network restrictions. A new tab does not copy personal cookies or sign-ins.

The session is limited to eight tabs. Website-created popups are closed, downloads are cancelled, and dialogs are dismissed on every ULTRON-created page. Stale element snapshots are invalid after switching to a new tab. `Browser close` retains its existing meaning: close the entire isolated session, not personal browser tabs. Selective managed-tab switching/closing by voice is not added here.
