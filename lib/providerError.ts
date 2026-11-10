export function providerError(provider: string, status: number) {
  if (status === 503 || status === 502 || status === 504) return `${provider} is temporarily busy or unavailable (HTTP ${status}). Please try again shortly. This does not by itself mean your API key is invalid. Local app and drive commands do not need the AI provider.`;
  if (status === 429) return `${provider} rate limit or quota reached (HTTP 429). Wait before retrying and check your provider quota.`;
  if (status === 401 || status === 403) return `${provider} denied access (HTTP ${status}). Check your API key, model access and account permissions.`;
  return `${provider} request failed (HTTP ${status}). Check the configured model and provider status. No successful AI response was received.`;
}
