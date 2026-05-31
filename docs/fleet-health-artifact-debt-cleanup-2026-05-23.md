# Fleet Health Artifact Debt Cleanup

Date: 2026-05-23

Goal: remove stale green-looking task states that failed the new golden artifact gate. This cleanup does not fabricate missing evidence. Tasks with recoverable evidence get corrected artifact paths; stale or superseded tasks are marked canceled so they no longer pretend to be completed.

## Decisions

- ProductLab T-153 and T-166..T-179 are treated as stale ledger entries from the failed T-165 UI restoration loop. Missing report/image artifacts mean they must not remain completed or waiting review.
- WorkAssistant T-167 is treated as a stale cockpit recheck receipt because its referenced manager artifact is missing and no matching replacement was found.
- TODO002 T-1..T-5 and T-7 point to existing May 15 community-study evidence and can keep completed status after artifact path repair.
- WebsiteChuhai T-21/T-22 point to existing SynthPack source files under `projects/synthpack/`; T-27 points to an existing Qia persona artifact proving the self-completed transfer/persona work.

## Follow-up

- Future `待验收` / `已完成` without a real artifact should be blocked by `task` / `send` and surfaced by `fleet-health`.
- If an owner wants to revive any canceled ProductLab T-165 item, create a new task with fresh screenshot/probe/data evidence instead of reopening the old ledger row.
