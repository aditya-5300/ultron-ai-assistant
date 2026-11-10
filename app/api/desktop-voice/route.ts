import { assertLocalRequest, readControlBody } from '@/lib/controlSecurity';
import { desktopVoiceState, desktopVoiceResult, setDesktopVoice, approveDesktopVoice, declineDesktopVoice, desktopVoiceEvents, interruptDesktopVoice } from '@/lib/desktopVoice';
export const runtime = 'nodejs';
export async function POST(request: Request) {
  // Buttons may enable, stop or approve voice work; spoken audio cannot approve actions here.
  try {
    assertLocalRequest(request);
    const body = JSON.parse((await readControlBody(request, 2000)).toString());
    if (body.action === 'state') return Response.json(desktopVoiceState());
    if (body.action === 'result' && Number.isInteger(body.id)) return Response.json(desktopVoiceResult(body.id));
    if (body.action === 'events') return desktopVoiceEvents(request.signal);
    if (body.action === 'interrupt') { interruptDesktopVoice(); return Response.json(desktopVoiceState()); }
    if (body.action === 'enable') return Response.json(await setDesktopVoice(true));
    if (body.action === 'disable') return Response.json(await setDesktopVoice(false));
    if (body.action === 'approve' && typeof body.token === 'string') return Response.json(await approveDesktopVoice(body.token));
    if (body.action === 'decline') return Response.json(declineDesktopVoice());
    throw new Error('Unsupported voice action.');
  } catch (error) { return Response.json({ error: error instanceof Error ? error.message : 'Voice action failed' }, { status: 400 }); }
}
