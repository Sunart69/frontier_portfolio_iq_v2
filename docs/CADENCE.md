# Semi-annual portfolio prioritization: operating guide

Every half-year the portfolio is re-prioritized for the next half. The cadence is published in
`cadence/cadence.yaml`; everything below — dates, reminders, decks, calendars — is generated from it.

## The next two cycles

| Milestone | 2027-H2 (Jul–Dec 2027) | 2028-H1 (Jan–Jun 2028) | Owner |
| --- | --- | --- | --- |
| Kickoff and stakeholder briefing | Tue Mar 30, 2027 | Tue Sep 28, 2027 | Portfolio lead |
| Templates issued | Tue Apr 6 | Tue Oct 5 | Portfolio lead |
| Due: LOB strategy, KPI targets, benefit split | Fri Apr 23 | Fri Oct 22 | LOB outcome owners |
| Due: LOB budgets | Fri Apr 30 | Fri Oct 29 | LOB budget owners |
| Due: capacity pools, eligibility, transfer rules | Fri Apr 30 | Fri Oct 29 | LOB resource managers |
| Due: initiative proposals | Fri May 7 | Fri Nov 5 | LOB PMO leads |
| Due: enterprise settings | Fri May 14 | Fri Nov 12 | Portfolio lead |
| Validation complete | Tue May 18 | Tue Nov 16 | Portfolio lead |
| Transfer and release approvals decided | Fri May 21 | Fri Nov 19 | LOB executive sponsors |
| Optimization run and scenarios | Tue May 25 | Tue Nov 23 | Portfolio lead (automatic) |
| LT pre-read published | Tue Jun 1 | Tue Nov 30 | Portfolio lead |
| **LT decision meeting** | **Tue Jun 8** | **Tue Dec 7** | Leadership team |
| Committed portfolio published | Tue Jun 15 | Tue Dec 14 | Portfolio lead |

Rule: the LT decision is the Tuesday about three weeks before the half starts; every other date is
worked back from it and moved off weekends and company holidays. Change the rule, a single cycle
(`cycle_overrides`), the milestones or the holidays in `cadence/cadence.yaml`.

## One-time setup

1. **Owners.** Replace every bracketed placeholder in `cadence/owners.yaml` with a name and GitHub
   username. Owners need access to this repository to be assigned and notified; people without
   GitHub can be listed by name and email (set `reminders.email: true`).
2. **Baseline data.** Replace `cadence/baseline_intake.xlsx` with your current portfolio in the
   intake format (`python -m frontier_portfolio_iq sample --out x.xlsx` shows the format). It
   pre-fills the first cycle's templates; later cycles pre-fill from the previous cycle.
3. **Repository settings → Actions → General.** Workflow permissions: *Read and write*; tick
   *Allow GitHub Actions to create and approve pull requests* (for the LT pre-read pull request).
   If `main` is protected, allow the workflow to push or change the commit step to a pull request.
4. **Optional email.** Add secrets `FPIQ_SMTP_HOST`, `FPIQ_SMTP_PORT`, `FPIQ_SMTP_USER`,
   `FPIQ_SMTP_PASSWORD`, `FPIQ_SMTP_FROM` and set `reminders.email: true`.
5. **Copilot.** With Copilot cloud agent enabled for the repository, the **Portfolio Cycle
   Orchestrator** appears in the agent picker (Copilot app, GitHub.com agents tab, VS Code, CLI:
   `copilot --agent portfolio-cycle`). `copilot-setup-steps.yml` installs its tools.

## What happens automatically

The **Portfolio cycle** workflow runs every weekday at 08:00 Pacific and does only what is due:

| When | What it does |
| --- | --- |
| Before kickoff | Nothing |
| Kickoff date | Writes `cycles/<cycle>/` (templates, `calendar.ics`, `SCHEDULE.md`, stakeholder deck); opens one input issue per owner (17) and a status issue |
| Daily | Checks returned files; posts each follow-up stage once; closes an owner's issue when their valid file is merged; updates the status issue |
| Optimization-run date | If all 17 inputs are valid, merges them and runs the optimizer; outputs arrive as an *LT pre-read* pull request, and a pre-filled decision record is written to `cycles/<cycle>/decision/lt_decision.yaml`. If not, the status issue says what blocks the run, and escalations continue |
| After the LT meeting | As soon as the decision record is filled in and merged, re-runs the portfolio with LT's decisions and publishes the approved-portfolio memo to `cycles/<cycle>/published/` |
| Publish date | If the decision record is still empty, the status issue shows *Publish overdue* and the portfolio lead is reminded once. A record filled in later is still published |

A pull request that adds or changes a file in `cycles/*/inputs/` is checked automatically and the
result is posted on the pull request.

Run any step by hand: **Actions → Portfolio cycle → Run workflow** (`tick`, `status`, `followup`,
`kickoff`, `run`, `publish`; `force` runs the optimizer with last cycle's data for missing inputs).

## The LT pre-read (v2)

On the optimization-run date the workflow runs the v1 analysis (tiers, scenarios, capacity, LOB view)
**and** the v2 decision-intelligence pipeline, and opens the pre-read pull request with
`cycles/<cycle>/outputs/`:

| File | For |
|---|---|
| `ceo_decision_deck_<cycle>.pptx` | the LT meeting: decision, opportunity, constraints, frontier, four strategic choices, radar, risk, execution, LOB impact, recommendation, what-ifs, decisions required, appendices |
| `decision_dashboard.html` | exploring the trade-offs before and during the meeting |
| `decision_results.xlsx`, `decision_intelligence.json`, `solver_confidence.md` | analysts and audit |
| `executive_deck_<cycle>.pptx`, `lob_dashboard.html`, `portfolio_results.xlsx` | the v1 views (tiers, capacity, LOB reconciliation) |

The pre-read recommends the **risk-adjusted** plan (balanced profile by default; see
`config/decision.yaml`). Monte Carlo inputs come from the optional uncertainty sheets in the intake
templates; where owners leave them blank, documented defaults are used and the outputs say so.

## After the LT decision

To publish what LT actually decided:

1. Open `cycles/<cycle>/decision/lt_decision.yaml`. Its comments list the recommended initiatives,
   the ones not recommended, the pending approvals, and the **evaluated alternatives** (aggressive,
   balanced, conservative, accelerated, and the other simulated plans).
2. Record what LT decided and set `decided_on`:

   ```yaml
   decided_on: 2027-06-08
   alternative: conservative        # optional: adopt an evaluated plan instead of the recommendation
   approve: [TR-02]                 # approvals granted (transfer rules or REL-<pool>-<pct> releases)
   include: [A04]                   # initiatives LT added
   exclude: [E05]                   # initiatives LT removed
   start_by: {A03: 2027-09}         # latest start month
   notes: {E05: "Deferred to 2028-H1 pending vendor selection"}
   actions:
     - {action: "Re-submit E05 with a vendor quote", owner: "Enterprise PMO lead", due: 2027-11-15}
   ```

3. Merge it to `main`. The workflow publishes to `cycles/<cycle>/published/`:
   - `approved_portfolio_<cycle>.md` and `.docx`: headline, approved initiatives with start and finish
     months, totals by LOB, **risk profile (simulated)**, changes from the pre-read and why, approvals
     granted, what was not funded, follow-up actions.
   - `approved_portfolio_<cycle>.xlsx`: the register PMOs work from.
   - `decision_summary.json`: the same figures, for audit.

How the plan is fixed (v2 pre-reads): the recommended — or adopted — plan is **preserved**. Only LT's
explicit changes alter it: included initiatives are fitted in; a recommended initiative is moved, then
dropped, only if those changes leave no room for it; approvals provide capacity but do not add
initiatives on their own. Pending approvals are never assumed. The published plan is re-validated, and
its risk profile is simulated (5,000 futures by default). If LT's decisions cannot all fit the capacity
and budgets, nothing is published: the status issue explains which commitment breaks.

(v1-only cycles, with `decision_intelligence.enabled: false`, keep the v1 behaviour: the committed
portfolio is re-optimized under LT's decisions, keeping pre-read start months within the 1% tolerance.)

## Follow-up and escalation policy

| When | Who is notified |
| --- | --- |
| 7 days before due | Owner (heads-up) |
| 2 days before due | Owner (reminder) |
| Due date | Owner |
| 1 business day late | Owner |
| 2 business days late | Owner and the LOB executive sponsor |
| 5 business days late | Owner, sponsor, escalation contact and portfolio lead; the optimization run is flagged at risk |

A valid submission stops all reminders immediately.

## For input owners

1. Open your issue (titled `[Portfolio 2027-H2] <input> — <LOB> — due <date>`).
2. Download your pre-filled template from `cycles/<cycle>/templates/<input>__<LOB>.xlsx`. Its
   README sheet says what is needed.
3. Update it for the coming half. Keep sheet and column names; only your LOB's rows; money in $K.
4. Return it as `cycles/<cycle>/inputs/<input>__<LOB>.xlsx` through a pull request (or **Add file →
   Upload files**). Fix anything the automatic check reports. No GitHub access? Email it to the
   portfolio lead.

## Asking the agent

- "Where are we on 2027-H2, and who is late?"
- "Kick off the 2027-H2 cycle" (it checks owners.yaml for placeholders first)
- "The LT meeting moved to June 15 — update the schedule and tell me what changed"
- "Commercial's budget file came by email; add it and check it"
- "Run the prioritization" / "What if Retail's budget is cut 10%?"
- "Prepare the LT pre-read summary"
- "LT approved TR-02 and dropped E05 — record it and publish the approved portfolio"
