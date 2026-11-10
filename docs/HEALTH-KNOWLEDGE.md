# Source-backed health knowledge

Reviewed 2026-10-28. `lib/health/evidence.cjs` stores original short paraphrases of four Microsoft sources, with source URLs, review dates, applicability, and limitations. No full articles or forum posts are copied. Source guidance is mapped to battery wear/drain, power plans, shutdowns and storage diagnostics.

`core.cjs` merges relevant evidence into KB checks. The existing AI snapshot selects KB articles by detected issue category, so approved AI analyses receive the relevant source records without sending process names or raw event messages. This is deterministic topic retrieval, not embedding search or model fine-tuning. Source text never grants permission to execute commands. The model must distinguish observed facts, source guidance, and unvalidated application thresholds.

Review sources before changing this file. Keep paraphrases short, retain attribution and scope, and never import instructions or executable commands from retrieved pages. There is no unattended web scraper, automatic source refresh, telemetry upload, or training job. Missing source coverage remains explicitly synthetic.

## Validation before predictive training

The current models are historical median/MAD baselines and gated trend regressions. Public troubleshooting pages are not labeled failure outcomes. No improvement in predictive accuracy has been measured by adding these references.

Before training a replacement, obtain appropriately licensed and consented data with hardware family, sensor definitions, timestamps, workload/power context, confirmed incident outcomes, and negative cases. Match training features to sensors actually available here. Split by device and time (not random neighboring rows); keep an untouched evaluation set. Compare with existing rules using precision/recall, false alerts per device-day, calibration and warning lead time. Evaluate missing sensors, sleep gaps, changed workloads and new hardware separately. Deploy in advisory shadow mode first, keep versioned models and a rollback path, and retain approval gates regardless of accuracy.

Feedback is an annotation, not ground truth: a user marking an alert useful does not establish a hardware failure. Do not silently retrain on feedback, synthetic articles, or the model's own recommendations.
