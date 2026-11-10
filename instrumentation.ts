export async function register() {
  // Restore only the user's saved voice choice when the server starts, never during a production build.
  if (process.env.NEXT_RUNTIME === 'nodejs' && process.env.NEXT_PHASE !== 'phase-production-build') {
    const { restoreDesktopVoice } = await import('./lib/desktopVoice');
    await restoreDesktopVoice();
  }
}
