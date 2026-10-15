---
name: test-party
description: Run manual QA of docs/test-plans cases against T-Dui (terminal todo app) on local build as a Test Party, recording Passed/Failed/Blocked results in git. Use when asked to start or continue a test party, run or QA test cases, or end a party.
---

# Test Party

You run a QA session with a human tester. The tester drives **T-Dui (terminal todo app)** on
**local build** by hand; you pick the cases, walk them through each one step by step,
judge each Expected Result from what they report, and record a result for each. You do not
fix code.
Read [`docs/test-parties/README.md`](../../../docs/test-parties/README.md) first — it defines Test Party,
Build, Result, stale, and remaining.

`party.py` in this folder does all bookkeeping — joining, reading cases, recording,
committing, pushing. Run `python3 .claude/skills/test-party/party.py <command> --help` for
arguments. Below, `party.py` means that path.

**A session runs at most 5 cases yourself.** The tester's time and focus are the constraint; the
party tracks progress, so the next session continues where this one stopped.
The skill takes no arguments — ask for anything you need.

## 1. Join a party

`party.py parties` lists open parties. Ask which to join, or offer to start one: ask for a
name (`<yyyy-mm-dd>-<kebab-name>`) and the features in scope (the `docs/test-plans/*.csv`
slugs; default all). `party.py join <party> [--features a,b]` joins it, starting it first if
it doesn't exist.

If the user wants to **end** the party instead, run `status`, show what's still remaining,
and on confirmation open a PR from `test-party/<party>` to `main`
titled `docs: end test party <party>` with the `status` summary in the body — or, if the
branch already has an open PR, run `party.py pr <party>` and mark it ready for review. Stop
there.

Ask for the tester name — one lowercase word; it names their results log. `tips.local.md` in
this folder (gitignored) holds the tester's personal tips; read it if it exists, and add a tip
there when the tester asks you to remember one.

## 2. Pin the build

Run `party.py build`. It prints the `app_version` and `deploy_sha` that local build is
running (the version in Cargo.toml and the commit checked out locally). Show the user any warnings; `gh unavailable` means `gh` isn't installed or
signed in to this repo's GitHub, which the tester can fix. If it says a deploy is in progress, wait
until it lands and run `build` again before starting. If it can't tell (`unknown`), ask the
tester what local build runs, or record `unknown`. Every result this session records this
build.

Then ask the tester to confirm the **baseline** every case starts from: the tester runs `cargo run` from a checkout of main in a terminal at least 120x40, with ~/.local/share/tdui/todos.json moved aside so the app starts with an empty list.

## 3. Pick the work

Run `party.py status <party> --app-version <v> --deploy-sha <sha>` and show each feature as
`<title> · <n> remaining`, with its failed and blocked counts. Ask which
feature to run; the user may instead name a scenario or Case IDs (the only way to rerun a
`Blocked` case).

Your queue is the next remaining cases in the chosen scope, in `status` order, up to
**5 cases**.
Read each with `party.py show <case-id>`.

## 4. Run each case

1. **Cast.** Say which account or role the tester plays before starting, e.g.
   `AB-004: tester signs in as the record owner`. If the case needs an account or role the
   tester doesn't have, record `Blocked` with that as the note and move on.
2. **Walk it.** Give the tester the setup and the steps, a few at a time. After each step
   that has an Expected Result, ask what they see and compare it, word for word where the
   case quotes text. Ask for a screenshot when the description leaves doubt. Don't accept "it
   worked" for a result with specifics: ask for the specifics.
3. **Record** it — `record` commits, pushes, and refreshes the Current status section of
   the party's PR description; tell the user if it says the description wasn't updated.
   For `Failed`, the note is the step and what the tester observed; for `Blocked`, the reason.

   ```bash
   party.py record <party> <case-id> <result> --method human --tester <name> \
     --app-version <v> --deploy-sha <sha> --note "<one line>"
   ```

4. **Restore.** If the case changed the baseline (uninstalled, signed out, revoked access,
   changed a setting), ask the tester to put it back before the next case.

## 5. Report

End the session with:

- Each case you ran: result, and for failures the step that failed, expected vs observed.
- Blocked cases and why.
- Anything in a case's text that's wrong against the shipped product — the user decides
  whether the case or the product is wrong. A case fix is an ordinary branch off
  `main` using `/write-test-plan`; once merged, it marks that case's results stale.
- A draft bug ticket per distinct bug for GitHub Issues on EttienneM/T-Dui, through the gh CLI: the case ID,
  the build, the failing step, expected vs observed, and steps to reproduce.
  **Create a ticket only after the user approves that draft.** Triage first: a failure caused
  by stale test copy, environment setup, or a known limitation is not a bug.
- `status` for the feature, with its remaining counts — the next session continues from there.

## Tips

- Back up your real data first: `mv ~/.local/share/tdui/todos.json ~/.local/share/tdui/todos.json.bak`, and restore it after the party
- Inspect saved state with `cat ~/.local/share/tdui/todos.json` to confirm completed and deleted tasks persist
