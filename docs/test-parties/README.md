# Test Parties

Manual QA results, kept in git. Test cases live in [`docs/test-plans/`](../test-plans); this
folder records who ran which case, against which build, and what happened. The `/test-party`
skill runs parties through [`party.py`](../../.claude/skills/test-party/party.py), the only writer of results —
don't edit these files by hand. `/write-test-plan` writes the cases.

## Vocabulary

- **Test Case** — one row of a `docs/test-plans/*.csv` file, identified by its **Case ID**
  (`AB-012`). The CSV on `main` is the only source of truth for what a case says.
- **Case ID** — `<prefix>-<nnn>`, permanent. Rewording a case keeps its ID; a case that now
  tests something different gets a new one.
- **Test Party** — one round of manual testing across a chosen set of features. It spans as
  many builds as it takes to finish; a code change never resets it.
- **Build** — what local build is running: the **app version** and the **deploy SHA**
  (the commit deployed). Either can change without the other; `n/a` or `unknown` when it
  couldn't be read.
- **Result** — one attempt at one case: `Passed`, `Failed`, or `Blocked`, with the build it
  ran against and a one-line note (the failed step and what was seen, or why it was blocked).
- **Stale result** — a result recorded against different Steps or Expected Result text than
  the case has now. Whitespace doesn't count. A stale result no longer counts.
- **Remaining case** — a case in the party's scope that is untested, has a stale result, or
  last `Failed` on a different build from the one now deployed. A failure on the current
  build waits for a fix to ship; a `Blocked` case waits for its blocker to go, and is rerun
  only when someone names it.

## Layout

```
docs/test-parties/<yyyy-mm-dd>-<name>/
  party.md        # Features: <slug>, <slug> — or "all"
  <tester>.csv    # one append-only log per tester
```

Each tester's log has
`case_id, result, case_hash, app_version, deploy_sha, timestamp, note, method`. `method` is
how the case was judged: `human` when a person ran it, `agent` when an agent drove it.
For each case the newest line across all logs counts; on a
timestamp tie, `Failed` beats `Blocked` beats `Passed`.

Each tester writes only their own log, so testers never conflict. Use one tester name per
person and one machine per tester name.

## Branches

A party lives on a branch `test-party/<yyyy-mm-dd>-<name>`, never on
`main` until it ends. `party.py` keeps the branch in its own git worktree under
`.git/test-party-worktrees/`, so testing never touches your working copy, and every recorded
result is committed and pushed at once. Each push also refreshes the Current status section of
the branch's open PR description; `party.py pr <party>` refreshes it by hand, such as after a
case fix makes results stale. Cases are always read from `origin/main`, so a
case fix merged mid-party applies immediately and marks that case's results stale.

A party is open while its branch exists. To end it, open a PR from the branch to
`main` and merge it.

## Configuration

`party.py` reads `.claude/skills/test-party/test-party.json`; `party.py config` prints the
effective settings. Its tests run with
`python3 -m unittest discover -s .claude/skills/test-party`.
