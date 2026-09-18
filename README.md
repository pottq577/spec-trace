# How do I run the first spec-trace MVP?

This guide gets the first operational MVP running against one Notion planning document. It covers collection, review tracking, Notion projection, recovery, and version snapshots. DevFlow handoff stays outside this first validation path.

## What this MVP validates

The first validation path checks these behaviors:

- Collect a Notion root page and composed child pages into an immutable snapshot
- Detect a later source change and create a change set
- Export structured analysis packets and import agent proposals
- Record developer decisions, planner questions, blockers, and planner answers
- Project the current review state under the Notion root page
- Reconcile failed projections before the next polling cycle
- Create a final specification revision after blocking findings are resolved

## Install the CLI

Use Python 3.12 or newer. Run the commands from the repository root.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

The test command uses the fake Notion adapter. It does not require a Notion token.

## Run the live smoke test

Create a Notion integration and share one test database row with it. Use a disposable row because the write smoke creates a `개발 검토` child page.

```bash
export NOTION_TOKEN=your_notion_token_here
export SPEC_TRACE_LIVE_DATABASE_ID=your_database_id_here
export SPEC_TRACE_LIVE_PAGE_ID=your_test_page_id_here
```

Run the read check first:

```bash
spec-trace live-smoke
```

Then verify collection and projection writes in an isolated local workspace:

```bash
spec-trace --workspace /tmp/spec-trace-smoke live-smoke --allow-write
```

The write smoke passes when the second projection is idempotent and its own review page does not create a new source snapshot.

## Register a real planning document

Initialize the workspace, then register one Notion database row as the root document.

```bash
spec-trace init
spec-trace document register \
  --database-id your_database_id_here \
  --page-id your_page_id_here
```

Copy the returned `planning_document_id`. Use it for later commands.

## Collect and inspect the first snapshot

Collect the document and inspect its lifecycle state:

```bash
spec-trace collect --document your_planning_document_id_here
spec-trace status --document your_planning_document_id_here
```

Collect every registered document with one command:

```bash
spec-trace collect --all
```

A second collection without source changes returns `UNCHANGED`. A changed source creates a new snapshot and change set.

## Verify version tracking

Change one sentence in the registered Notion test document. Then collect it again:

```bash
spec-trace collect --document your_planning_document_id_here
spec-trace status --document your_planning_document_id_here
```

Confirm that `current_snapshot_id` changed and `change_sets` contains a new item. Revert the Notion sentence only after you record the result you want to inspect.

Export the detected change when you want to verify semantic version tracking:

```bash
spec-trace analysis export \
  --type source-diff \
  --change-set your_change_set_id_here
```

The generated packet contains the baseline snapshot, target snapshot, and physical page changes. An external agent can propose semantic `ChangeItem` candidates from that evidence.

## Export the first review packet

Export the current source and development context for an external agent:

```bash
spec-trace analysis export \
  --type review \
  --document your_planning_document_id_here
```

The command returns a JSON file under `.spec-trace/analysis/requests`. Give that file to ChatGPT or another review agent. Fill the packet’s `expected_output` structure and save the response as JSON.

Import the response and inspect its candidates:

```bash
spec-trace analysis import path/to/review-response.json
spec-trace proposal show your_analysis_proposal_id_here
```

Adopt each candidate only after checking its source evidence:

```bash
spec-trace proposal review \
  --proposal your_analysis_proposal_id_here \
  --candidate your_candidate_key_here \
  --action adopt
```

## Project questions and decisions to Notion

Use the existing `decision`, `question`, and `blocker` commands to resolve adopted findings. Then reconcile the Notion review page:

```bash
spec-trace sync --document your_planning_document_id_here
```

When a planner writes an answer in the generated answer slot, run `sync` again. Verify the new `PlannerAnswer` with `answer verify` after checking the response.

## Run continuous polling

Run one recovery and collection cycle to inspect its result:

```bash
spec-trace watch --once
```

Start continuous polling after that check:

```bash
spec-trace watch --interval 300
```

Each cycle reconciles failed `PROJECT_DOCUMENT` operations before collection. An unstable source collection retries after `5s`, `15s`, and `30s`.

## Create the first final specification revision

Write the reviewed implementation basis to a Markdown file. Create a revision after all findings and active blockers are resolved.

```bash
spec-trace final-spec create \
  --document your_planning_document_id_here \
  --content path/to/final-spec.md
```

Run `status` again and confirm `final_spec_revision` points to the new revision. At this point, the first documentation and version-tracking MVP is ready for real workflow testing.
