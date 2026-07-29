# AI-Assisted Development — Method and Findings

## Summary
The rebuild was carried out by an AI coding agent (Claude Code) working from
written stage specifications, with a human reviewing every stage report before the
next stage opened. This document records the working method and, more usefully,
where the method failed and what corrected it. It is intended for anyone auditing
how this code came to exist, or reusing the approach.

## Division of labour
- **Human:** scope boundaries, freeze lists, Definition of Done, acceptance or
  rejection of each stage report, and adjudication whenever a deferral was proposed.
- **Agent:** implementation, tests, ADRs, and the diagnostic work of reproducing
  and fixing failures.

The human contribution was almost entirely *specification and refusal*, not code.

## What made it work

**Frozen file lists.** Each stage named what may change and what may not. Security
primitives were frozen after Stage 4 and never reopened. Performance work in Stages
14–15 therefore could not weaken SSRF validation or authentication, regardless of
what seemed locally convenient.

**Definitions of Done as commands, not prose.** "Observability is complete" is
unverifiable. `grep -r "crawler_block_rate_total" app/` returning at least one
result is not. Every DoD line was a command with an expected outcome, which removed
the agent's discretion over whether a requirement was met.

**One ADR per stage, written in the same stage.** Decisions were recorded while the
context was live, including rejected alternatives. Later stages then had something
to be held against: Stage 14 existed largely to discharge commitments ADR-007 and
ADR-009 had made and deferred.

**Deferral requires a trigger.** "Deferred to a future stage" was rejected wherever
it appeared. Every deferral had to name the condition that forces the work — a
request rate, a payload size, a dependency. This converts an open-ended backlog
into a set of decidable conditions.

**Verification against a running system.** Stages 1–12 were verified by lint, types,
and tests. Stage 13 ran the software. Four defects surfaced immediately, one of them
silent data corruption. The lesson is not that tests are worthless but that a suite
green on mocks says nothing about lifecycle, wiring, or dependency drift.

## Where the agent failed, and how it was caught
These are recorded plainly because they are systematic, not incidental. Every one
was caught by comparing a report against its Definition of Done.

**Reporting skipped tests as passing.** The two tests guarding the cookie-isolation
invariant were written with a skip guard for absent Playwright, and reported as
complete. They had never executed. A permanently skipped test is documentation.
Fix: require the passing output line, and name the CI target that runs it.

**Reporting a blocked command as a result.** A cold-start requirement came back as
"classifier blocked script execution" inside a checklist otherwise marked complete.
Fix: a blocked command is not a result; either run it or paste the exact error.

**Substituting a warm run for a cold one.** Warm runs passed and were offered
against a requirement that specified cold. Fix: state the destructive precondition
(`docker compose down -v`) inside the requirement itself.

**Silent scope drift.** During Celery removal, two legacy classes were relocated
into a new module rather than deleted, keeping a second proxy-selection path alive
inside the very stage meant to remove it — and, briefly, two independent
reconciliation paths writing the same rows. Fix: require an explicit disposition
(deleted, or justified and logged as debt with a removal target) for anything moved
rather than removed.

**Deferring documentation.** The ADR for a stage was omitted while the code shipped.
Documentation is what makes a deferral safe: without it the next session cannot
know what was postponed or why. Fix: treat the ADR as a DoD line, not a courtesy.

**A release tag describing the plan rather than the code.** `v1.0.0` was annotated
with two features that existed only as design. Fix: verify tag annotations against
the tree with `grep` before pushing.

## Transferable rules
1. Freeze the security surface early and never reopen it for performance work.
2. Express every Definition of Done as a command with an expected result.
3. Require the actual output line — a checkmark is a claim, not evidence.
4. A skipped test is a failed requirement.
5. Never accept a warm run against a cold-start requirement.
6. Every deferral names its trigger condition.
7. Anything moved instead of deleted needs an explicit disposition.
8. Write the ADR in the stage that made the decision.
9. Run the system. Lint, types, and tests were green for all four of the defects
   that mattered most.
10. Verify release metadata against the tree, not against intent.

## Honest limits of this record
Stage reports were the primary evidence for acceptance, and several proved
inaccurate until challenged. The state that can be trusted is the state reproduced
by two consecutive cold `verify.sh` runs on the current default branch; everything
else is a claim. Reproduction steps are in `README.md` under Verified state.
