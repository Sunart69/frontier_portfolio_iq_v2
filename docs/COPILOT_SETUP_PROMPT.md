# Copilot setup prompt: Frontier Portfolio IQ v2 (decision intelligence) and Portfolio Cycle agent

Use this with GitHub Copilot in **agent mode** (VS Code, or the Copilot CLI), opened on a local clone
of this repository. Agent mode can read files, edit them and run terminal commands, which this setup
needs. Copilot Chat on github.com can answer questions about the repo but cannot run the steps.

**How to use it**

1. Clone the repository and open it in VS Code: `git clone https://github.com/Sunart69/frontier_portfolio_iq_v2`
2. Fill in the **Your organization** block below. Anything you leave as `TBD`, Copilot will ask you
   about at the right step.
3. In Copilot Chat, switch to **Agent** mode, and paste everything from **PROMPT START** to
   **PROMPT END**. Or type: *"Follow docs/COPILOT_SETUP_PROMPT.md, starting at phase 0."*
4. Copilot stops at each **checkpoint** for your confirmation. On the free Copilot plan, run one or
   two phases per session to stay within the monthly allowance: *"Continue with phase 4."*

---

## PROMPT START

You are setting up **Frontier Portfolio IQ** in this repository for my organization. It has two parts
that already exist and are tested:

1. **The optimizer**: a Python package (`frontier_portfolio_iq/`) that prioritizes IT initiatives
   across lines of business (LOBs) with a time-indexed mixed-integer model (PuLP + HiGHS). It respects
   ring-fenced, shared and conditionally transferable capacity, LOB budgets and funding rules, and it
   attributes each benefit to LOBs exactly once. It produces three portfolio tiers (Committed,
   Conditional, Unconstrained), twelve what-if scenarios, a results workbook, an HTML LOB dashboard
   and a 12-slide executive PowerPoint deck.
2. **The Portfolio Cycle agent**: a semi-annual cadence engine (`frontier_portfolio_iq/cadence/`)
   driven by a GitHub Actions workflow (`.github/workflows/portfolio-cycle.yml`), plus a Copilot
   custom agent (`.github/agents/portfolio-cycle.agent.md`). Every half-year it:
   - plans the cycle backwards from the leadership team (LT) decision meeting;
   - issues pre-filled input templates, a calendar and a stakeholder deck;
   - opens one GitHub issue per input owner, and posts reminders and escalations on a published policy;
   - merges the validated inputs and runs the optimizer, then opens an LT pre-read pull request;
   - after the LT meeting, re-solves with the recorded decisions and publishes the approved-portfolio
     memo (Markdown and Word), register and summary.

Your job is to **configure and verify** this for my organization, not to rebuild it. Work through the
phases in order. After each phase, report what you did and what you checked. At each **CHECKPOINT**,
stop and wait for my confirmation.

### Your organization (fill in; `TBD` means ask me)

```yaml
organization: TBD                 # e.g. "Acme Financial"
repository: TBD                   # owner/name on GitHub, e.g. Sunart69/frontier_portfolio_iq_v2
repository_visibility: TBD        # public or private (private uses GitHub Actions minutes)
timezone: TBD                     # IANA name, e.g. America/Los_Angeles
first_cycle: TBD                  # e.g. 2027-H2 (H1 = Jan–Jun, H2 = Jul–Dec)
lt_meeting_weekday: TBD           # e.g. TUE
lt_meeting_weeks_before_half: 3   # the LT decides about this many weeks before the half starts
first_lt_meeting_date: TBD        # optional exact date for the first cycle, overrides the rule
daily_run_local_time: "08:00"     # when the weekday run should happen, local time
company_holidays: TBD             # list of YYYY-MM-DD for the next two years
email_reminders: false            # true only if SMTP secrets will be added

portfolio_lead:   {name: TBD, github: TBD, email: ""}
escalation:       {name: TBD, github: TBD, email: ""}     # e.g. CIO or LT sponsor
leadership_team:  {name: "Leadership team", github: "", email: ""}

lobs:                             # one entry per LOB; ids must match the intake workbook
  - id: TBD                       # e.g. LOB-A
    name: TBD                     # e.g. Retail Banking
    executive_sponsor: {name: TBD, github: TBD}
    owners:
      strategy:    {name: TBD, github: TBD}
      budget:      {name: TBD, github: TBD}
      capacity:    {name: TBD, github: TBD}
      initiatives: {name: TBD, github: TBD}
  # repeat for each LOB, including a shared/enterprise IT LOB if you have one

baseline_intake: TBD              # path to our current portfolio workbook, or "use sample" for now
```

### Rules for the whole setup

- **Do not change the model.** That means `optimizer.py`, `scenarios.py`, `metrics.py`, `analysis.py`,
  the decision logic in `cadence/decision.py`, the reminder policy, or the tests. Only change them if I
  ask. If something fails, diagnose it and tell me; do not edit a test to make it pass.
- **Never invent people, GitHub handles, dates, budgets or portfolio numbers.** If a value is `TBD`
  or missing, ask me.
- **Post nothing to GitHub issues** during setup. Use `--dry-run` on every `cycle` command that could
  post. Never set or print secrets, and never commit `.env` files or tokens.
- **Rehearse in a scratch folder** outside the repo, never in `cycles/`. A pushed file under
  `cycles/*/inputs/` or `cycles/*/decision/` triggers the live workflow.
- **Work on a branch** named `setup/<today's date>`. Commit only configuration and documentation
  changes. Open one pull request at the end.
- **Dates come from the tool.** After any cadence change, run
  `python -m frontier_portfolio_iq cycle schedule` and quote dates from its output.
- **Keep it short.** Lead each phase report with the result. Quote exact command output when
  something fails.

### Phase 0: Read before touching anything

Read these files:
- `README.md`
- `docs/CADENCE.md`
- `.github/agents/portfolio-cycle.agent.md`
- `.github/copilot-instructions.md`
- `cadence/cadence.yaml`
- `cadence/owners.yaml`
- `pyproject.toml`
- `config/decision.yaml`
- `docs/METHODS.md`, `docs/ASSUMPTIONS.md`, `docs/DEPENDENCIES.md`, `docs/PERFORMANCE.md`
- the files in `.github/workflows/`

Then summarize in at most 10 bullets:
- how a cycle flows, from kickoff to publish;
- which files I must configure;
- the number of input packages: 4 per LOB, plus 1 enterprise package;
- any `TBD` in the organization block that blocks later phases.

### Phase 1: Environment and tests

1. Check Python with `python --version` (3.10 or newer; CI uses 3.11).
2. Create and activate a virtual environment (`python -m venv .venv`), then run
   `pip install -e ".[test]"`.
3. Confirm every engine loads: `python -c "from frontier_portfolio_iq.solvers import engine_inventory; print(engine_inventory())"`.
   HiGHS, SCIP, CP-SAT and CBC must show as available; IPOPT and OSQP are optional.
4. Run the fast tests: `pytest -m "not integration" -q`.
5. Run the full suite: `pytest -q`. 166 tests are expected to pass; it took about 15 minutes on a
   2-vCPU machine (`docs/TEST_REPORT.md`).

Report the pass/fail counts. If anything fails, stop and show me the first failure in full.

### Phase 2: Optimizer smoke test

1. Run the sample portfolio: `python -m frontier_portfolio_iq run --out out`.
2. Confirm that `out/` contains these files:
   - `portfolio_results.xlsx`
   - `lob_dashboard.html`
   - `executive_deck.pptx`
   - `analysis_summary.json`
   - `deck_figures_manifest.json`
3. From `out/analysis_summary.json`, report:
   - committed net value and number of initiatives;
   - conditional net value, and the approvals it requires;
   - unconstrained net value;
   - how many initiatives fall in each class (Approved, Conditional, Deferred, Rejected).
4. Tell me to open `out/lob_dashboard.html` in a browser. Make sure `out/` is in `.gitignore`.
5. Run the v2 decision engine quickly: `python -m frontier_portfolio_iq decide --out out/v2 --quick`.
   Confirm `out/v2` contains `decision_dashboard.html`, `ceo_decision_deck.pptx`,
   `ceo_deck_manifest.json`, `decision_intelligence.json`, `decision_results.xlsx` and
   `solver_confidence.md`. From `decision_intelligence.json`, report the solver confidence level, the
   recommended plan (profile, initiatives, expected NPV, P10–P90, probability within budget) and the four
   strategic choices. Say plainly that the probabilities rest on default, uncalibrated assumptions.
6. Optional: run one benchmark case (`python -m frontier_portfolio_iq.benchmark --one 100 24`) and
   compare it with `docs/PERFORMANCE.md`. Do not report numbers you did not observe.

### Phase 3: Cadence

1. Edit `cadence/cadence.yaml` from the organization block:
   - `first_cycle` and `timezone`;
   - `decision_meeting.weekday` and `decision_meeting.weeks_before_half_start`;
   - `cycle_overrides` for `first_lt_meeting_date`, if given;
   - `reminders.email`;
   - `holidays`, replacing the sample US list with ours.

   Keep the milestone offsets and the follow-up policy unless I ask to change them.
2. Set the weekday cron in `.github/workflows/portfolio-cycle.yml` to `daily_run_local_time` in
   our timezone, converted to UTC. GitHub cron runs in UTC and does not follow daylight saving time,
   so say which half of the year will be an hour off.
3. Run `python -m frontier_portfolio_iq cycle schedule --cycle <first_cycle>`, and the same for the
   following cycle.
4. Show both workback schedules as a table: milestone, date, weekday, owner.

**CHECKPOINT 1**: I confirm the dates, especially the kickoff and the LT meeting.

### Phase 4: Owners

1. Rewrite `cadence/owners.yaml` from the organization block. Keep the file's structure and comments.
   - Enter GitHub handles without `@`.
   - Leave email blank unless `email_reminders` is true.
   - `enterprise.owner` stays `portfolio_lead` unless I say otherwise.
2. Validate the file:
   - Every person must have a real name and a GitHub handle; flag any `[bracketed]` placeholder or
     empty handle.
   - LOB ids must be unique.
   - Count the input issues the kickoff will open: 4 × LOBs + 1.
3. If the repository is reachable with `gh`, check that each handle exists with `gh api users/<handle>`.
   Also list the handles that are not yet repository collaborators, because they cannot be assigned
   issues. If `gh` is not signed in, give me the list to check by hand.

**CHECKPOINT 2**: I confirm the owners. I invite any missing collaborators myself.

### Phase 5: Baseline portfolio data

If `baseline_intake` is a workbook path:

1. Validate it: `python -m frontier_portfolio_iq validate --input <path>`. Report every error with its
   sheet and row, and do not fix my numbers. If it is valid, copy it to `cadence/baseline_intake.xlsx`.
2. Check that the LOB ids in the workbook's LOB sheet match `cadence/owners.yaml` exactly. List any
   that exist in only one of the two.
3. Run the optimizer on it: `python -m frontier_portfolio_iq run --input cadence/baseline_intake.xlsx --out out/baseline`.
4. Report the same figures as in phase 2. If the committed portfolio is infeasible, show the solver's
   diagnostics and stop.

If `baseline_intake` is "use sample", keep the existing file, and remind me to replace it before
the kickoff date.

**CHECKPOINT 3**: I confirm the baseline results look plausible.

### Phase 6: Plan the first cycle (preview)

1. If `cycles/<first_cycle>/templates/` exists from the sample setup and `inputs/` holds only
   `.gitkeep`, delete `templates/` so no stale LOB templates remain.
2. Run `python -m frontier_portfolio_iq cycle plan --cycle <first_cycle>`.
3. Check the result:
   - the number of templates equals 4 × LOBs + 1, named `<package>__<LOB>.xlsx` (the enterprise
     one is `enterprise__ALL.xlsx`);
   - each template's README sheet names the right owner and due date;
   - `calendar.ics`, `SCHEDULE.md` and `stakeholder_input_deck_<cycle>.pptx` exist.

Tell me which files to share at the kickoff. On the kickoff date the daily run plans the cycle again
with the latest owners and baseline, so this is a preview.

### Phase 7: Full-cycle rehearsal (scratch folder only)

Simulate a whole cycle without touching GitHub or this repo's `cycles/` folder:

1. Copy `cadence/` to a new temporary folder, for example `$TMPDIR/fpiq-rehearsal/cadence`.
2. Before kickoff:
   - run `cycle tick --root <scratch> --dry-run --today <kickoff - 1 day>`;
   - expect "Waiting".
3. At kickoff:
   - run `cycle tick --root <scratch> --dry-run --today <kickoff date>`;
   - expect the plan, and an "opened N input issues" action against the in-memory tracker.
4. Owners submit:
   - copy every file from `<scratch>/cycles/<cycle>/templates/` to `<scratch>/cycles/<cycle>/inputs/`;
   - run `cycle status --root <scratch> --today <a date before the first due date>`;
   - expect every input to be received and valid.
5. Optimization run:
   - run `cycle run --root <scratch> --cycle <cycle> --no-release-scan`;
   - expect `"ran": true`, the pre-read files in `outputs/`, and a pre-filled `decision/lt_decision.yaml`.
6. LT decision:
   - in the scratch copy only, set `decided_on` to the LT meeting date;
   - set `approve` to one of the pending approvals listed in the file's comments;
   - set `exclude` to one recommended initiative;
   - add one entry to `notes` and one to `actions`.
7. Publish:
   - run `cycle publish --root <scratch> --cycle <cycle>`;
   - expect `"published": true`, and the memo `.md`/`.docx`, register `.xlsx` and
     `decision_summary.json` in `published/`.
8. Show me the memo's headline and its "Changes from the pre-read" table. Then delete the scratch folder.

### Phase 8: GitHub configuration (I do these; you give me the checklist)

Give me a numbered checklist with the exact settings paths:

1. Settings → Actions → General → Workflow permissions: **Read and write permissions**.
2. On the same page, tick **Allow GitHub Actions to create and approve pull requests**.
3. Settings → Collaborators: invite every owner who is not yet a collaborator (list them from phase 4).
4. Settings → Secrets and variables → Actions: add `FPIQ_SMTP_HOST`, `FPIQ_SMTP_PORT`,
   `FPIQ_SMTP_USER`, `FPIQ_SMTP_PASSWORD` and `FPIQ_SMTP_FROM`. Only if `email_reminders` is true.
5. After merging the setup pull request, test the workflow: Actions → **Portfolio cycle** → Run
   workflow, with action `status` and dry run ticked. Then run the **Tests** workflow.
6. If the repository is private: the expected Actions usage is about 40 minutes a month. Confirm that
   fits our plan's included minutes.

### Phase 9: The agent

1. Check `.github/agents/portfolio-cycle.agent.md`. Its YAML front matter needs `name` and
   `description`. Replace the example organization names in the body with ours, if any appear.
   Keep its rules and commands unchanged.
2. Tell me how to select the agent in VS Code: open the Chat view, open the agent picker, and choose
   **Portfolio Cycle Orchestrator**. If it does not appear, check that the file is on the branch I have
   open and that custom agents are enabled in my VS Code version's settings.
3. Give me five example requests to try, each with what the agent should do:
   - "Where are we on <first_cycle>, and who is late?"
   - "The LT meeting moved to <date>: update the schedule"
   - "Commercial's budget file came by email: add and check it"
   - "What if Retail's budget is cut 10%?"
   - "LT approved TR-02 and dropped E05: record it and publish"
4. State plainly what works without a paid Copilot plan. The scheduled workflow, issues, reminders,
   solver runs and publishing need no Copilot at all; only the chat front end does.

### Phase 10: Wrap up

1. Run `pytest -m "not integration" -q` again.
2. Run `git status` and confirm that only intended files changed. No `out/`, no scratch files, no secrets.
3. Commit on `setup/<date>` with a clear message, push, and open a pull request titled
   "Configure Frontier Portfolio IQ for <organization>". The body should list:
   - the configured cadence (first cycle, kickoff, LT meeting and publish dates);
   - the LOBs and the number of input packages;
   - the test results;
   - the phase 8 checklist as task-list items.
4. Give me a final summary:
   - what is configured;
   - what I still need to do, with dates;
   - the date the first automatic action will happen (kickoff), and what owners will receive then.

## PROMPT END
