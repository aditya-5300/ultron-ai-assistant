// Keep short source summaries and their limits alongside the synthetic troubleshooting articles.
const EVIDENCE = [
  {
    id: 'ms-battery-care', topics: ['battery-wear'], title: 'Caring for your battery in Windows',
    url: 'https://support.microsoft.com/en-us/windows/experience/power-battery/caring-for-your-battery-in-windows',
    reviewed: '2026-10-28', scope: 'Windows lithium-ion battery care; not a device-specific failure model',
    checks: ['Review Installed battery, Recent usage and Battery usage in a Windows battery report to distinguish capacity history from current consumption.'],
    caveat: 'A battery report estimates usage and capacity; this source does not validate ULTRON capacity thresholds or predict a failure date.'
  },
  {
    id: 'ms-battery-saving', topics: ['battery-drain', 'power-plan'], title: 'Battery saving tips for Windows',
    url: 'https://support.microsoft.com/en-gb/windows/experience/power-battery/battery-saving-tips-for-windows?nochrome=true',
    reviewed: '2026-10-28', scope: 'Windows power settings; availability and labels vary by device and OS version',
    checks: ['Compare display brightness, screen timeout and sleep settings during similar unplugged workloads.', 'Review a lower power mode when performance is not required; do not change settings without approval.'],
    caveat: 'Brightness and radio activity are not measured by this collector. These are checks to perform, not detected root causes.'
  },
  {
    id: 'ms-event-41', topics: ['unexpected-shutdown'], title: 'Advanced troubleshooting for Event ID 41',
    url: 'https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/event-id-41-restart',
    reviewed: '2026-10-28', scope: 'Windows unexpected shutdown investigation',
    checks: ['Inspect Event 41 details locally for BugcheckCode and PowerButtonTimestamp, and correlate with what happened before the restart.', 'A zero bug-check value is not proof of a faulty power supply; error information may not have been written.'],
    caveat: 'The collector records event metadata only, not these detail fields. Do not invent their values or infer a unique cause from Event 41.'
  },
  {
    id: 'ms-storage-counters', topics: ['storage-health'], title: 'MSFT_StorageReliabilityCounter class',
    url: 'https://learn.microsoft.com/en-us/windows-hardware/drivers/storage/msft-storagereliabilitycounter',
    reviewed: '2026-10-28', scope: 'Device-reported Windows storage counters',
    checks: ['Compare newly obtained reliability counters with earlier readings from the same physical device; interpret unavailable counters as unknown.'],
    caveat: 'Counter definitions do not validate ULTRON warning cutoffs, guarantee device health, or establish remaining service life.'
  }
];

function enrichArticle(article) {
  const evidence = EVIDENCE.filter(source => source.topics.includes(article.id));
  return {
    ...article,
    checks: [...new Set([...article.checks, ...evidence.flatMap(source => [...source.checks, source.caveat])])],
    sources: [...new Set([...article.sources, ...evidence.map(source => source.url)])],
    evidence,
    knowledgeMethod: evidence.length ? 'Curated source-backed retrieval; synthetic diagnostic rules remain unvalidated heuristics.' : 'Synthetic diagnostic guidance; no reviewed external evidence mapped yet.'
  };
}
module.exports = { EVIDENCE, enrichArticle };
