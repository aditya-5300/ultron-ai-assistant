const shared = globalThis as typeof globalThis & { ultronResponses?: Set<AbortController> };
const active = shared.ultronResponses ??= new Set<AbortController>();

export function cancellableResponse(parent?: AbortSignal) {
  const controller = new AbortController();
  active.add(controller);
  const abort = () => controller.abort();
  if (parent?.aborted) abort();
  else parent?.addEventListener('abort', abort, { once: true });
  return {
    signal: controller.signal,
    dispose() { active.delete(controller); parent?.removeEventListener('abort', abort); },
  };
}

export function interruptResponses() {
  // Cancel active AI requests as well as hiding their output in the interface.
  for (const controller of active) controller.abort();
  active.clear();
}
