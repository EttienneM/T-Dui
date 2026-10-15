---
name: write-test-plan
description: Write manual test cases for a QA engineer as a CSV in docs/test-plans/. Use when asked to write or extend a test plan, produce manual/QA test cases, or turn a story's user stories and acceptance criteria into tests a human runs by hand.
---

# Write a Test Plan

The unit of a test plan is a **journey** — one uninterrupted sitting at the keyboard that
carries a user from a starting state to an outcome, asserting at every point along the way.
One journey is one CSV row.

The failure mode this skill exists to prevent is the **assertion list**: fifty rows that each
re-open the same screen to check one thing. It is long, it is slow to run, and it never
exercises a real path through the product. A journey checks more with less: the QA engineer
sets up once, then walks the flow, and every checkpoint on the way is a test.

## Process

### 1. Pin the feature

Resolve what the user pointed at to a single feature in `docs/test-plans/`:

- A ticket key → fetch it from GitHub Issues on EttienneM/T-Dui, through the gh CLI. A sub-task's parent story is the spec;
  read the story, not just the sub-task.
- A feature name → the matching `docs/test-plans/<feature>.csv`.
- Nothing given → the current branch's ticket, from the branch name or recent commits.

Then decide the file. `docs/test-plans/` is **one file per feature, not one per ticket** — a
ticket almost always amends an existing plan. Read that file before writing to it: match its
Scenario names and its vocabulary, and replace the rows the change made stale rather than
appending a second account of the same behaviour.

### 2. Read the spec

From the story: its **user stories** (`As a… I want… so that…`) and what it puts **out of
scope**. From each sub-task: its **acceptance criteria**. Those three are the source of every
row you write.

Also read, and let them constrain the writing:

- The implementation, for exact strings. An expected result quoting a button label or an
  error message must quote the one that ships.

Skip anything in the story about automated tests at code seams. This plan asserts only what a
human at the keyboard can see.

### 3. Build the coverage ledger

List every user story and every acceptance criterion as a line to be covered. Mark each one:

- **Testable** — a human can drive it and observe the result.
- **Engineer-facing** — stories about module boundaries, file layout, or test structure
  (`As an engineer, I want…`). No manual test exists. Excluded.
- **Blocked** — a limitation or missing environment stops it. Excluded.

The ledger is the completion criterion for the whole skill: every testable line lands in at
least one journey, and every journey traces back to a ledger line. Report the exclusions to
the user by name — a silently dropped criterion reads as covered.

### 4. Cut the ledger into journeys

Group ledger lines that a single sitting can cover, then order them into a flow.

A journey ends where a real user would stop. It ends **early** — split it — when continuing
would need a different account, a different role or permission, a fresh install, or a logout.
Those are setup costs, and paying one twice inside a row is what makes a plan slow to run.

Fold negatives into the flow they belong to. An error path that the user recovers from is
one journey — trigger the failure, see the honest error, retry, succeed — and it tests the
error, the retry, and the success together. Give an error its own row only when it is
terminal and the flow cannot continue past it.

Bound: roughly 4–10 steps. Under four, it is an assertion pretending to be a journey; over
ten, the QA engineer loses their place and cannot report which step failed.

### 5. Write the CSV

Line 1 is the bare feature title. Line 2 is `ID,Scenario,Action,Steps,Expected Result`. Then
one row per journey. Match the quoting and multi-line cell style of the existing files in
`docs/test-plans/`.

- **ID** — `<prefix>-<nnn>`, using the file's existing prefix (a new file picks a short
  prefix no other file uses). A new row takes the highest number in the file + 1. **Never
  change or reuse an existing row's ID**, even when you rewrite its steps: Test Party results
  point at cases by ID, and a rewrite already marks their results stale
  (`docs/test-parties/README.md`). A replaced row that tests something different gets a new ID.
- **Scenario** — the group the journey belongs to, repeated across its sibling rows
  (`Sharing`, `Error Handling`). Reuse the file's existing groups.
- **Action** — the journey in one line, led by the role: `Owner changes access, then shares
  the link`.
- **Steps** — numbered, one action each. Every case starts from the Test Party baseline —
  the tester runs `cargo run` from a checkout of main in a terminal at least 120x40, with ~/.local/share/tdui/todos.json moved aside so the app starts with an empty list — so step 1 is the setup beyond it, if any (`1. Create a record you own`).
  Written for someone who has not read the ticket.
- **Expected Result** — numbered **to the step that produces it**, so a failure is reported
  against a step. This is what lets one row hold many assertions:

```
"2. The access step shows the access and permission dropdowns
4. The confirmation names the chosen recipient and the record title
5. The dialog closes and the record page shows the new collaborator"
```

Every assertion is something visible on screen: a page, a label, a dropdown, an error, a
state the tester can open and confirm. Nothing about a request, a mock, or a module.

### 6. Verify

Parse the file before handing it over — QA opens these in a spreadsheet and a broken quote
silently eats a column:

```bash
python3 .claude/skills/test-party/party.py check
```

CI runs the same check: it fails on a wrong column count or an ID used twice.

Then report: rows written, rows replaced, and every ledger line excluded with its reason.
