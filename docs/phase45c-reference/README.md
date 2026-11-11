# Phase 4.5-C frozen review snapshot

These copies preserve the effective configuration, primary scope, selection protocol, frozen selections, and acceptance results for review. The tracked text may have normalized line endings. `reference_sha256.json` records the exact original bytes of all 109 local Phase 4.5-C input and result files, which remain in their existing ignored directories. No locked input or evaluation output was regenerated.

The paired replay decision digest is `7af53a20293990e30f1b984ca6630e16b28e6b16d06e4a08b4c4a247c362d620`. The approved recommendation is **GO TO PHASE 5 WITH REDUCED SUBSYSTEM SCOPE** (CPU/workload, disk capacity, process responsiveness). This does not authorize starting Phase 5 or deploying the detector.

The hybrid produced 12 duplicate process notices inside already-detected events. They were excluded from the frozen false-interruption metric. Counting them as unwanted notifications would raise the rate to about 1.48 interruptions/device-day, above the 0.5 target. Duplicate process-notice suppression is mandatory before any future user-facing deployment.

See the [completion report](../PHASE-4-5-C-REDUCED-SCOPE-VALIDATION.md) for the full evaluation and its limitations.
