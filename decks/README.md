# Decks and documents

| What | For | Repository copy | Live, editable version |
| --- | --- | --- | --- |
| **How the solver decides** (12 slides) | LT and portfolio staff: how the optimizer works, three decision trees, guardrails, the proposal | [PDF](solver-explainer/how-the-solver-decides.pdf) · [source](solver-explainer/source/) | [Slides](https://claude.ai/artifact/XBWGGGDR4PJ9BTXkKGdD4T) |
| **How the solver decides** (explainer doc) | Pre-read with the full explanation, FAQ and technical appendix | [Markdown](../docs/explainer/how-the-solver-decides.md) | [Doc](https://claude.ai/artifact/Vazf49Cec5HAEgPYXadFEd) |
| **Portfolio 2027-H2: input collection kickoff** (8 slides) | Input owners and sponsors: workback calendar, what's due from whom, how to submit, follow-ups | [PDF](stakeholder-input-2027-H2/portfolio-2027-H2-input-collection-kickoff.pdf) · [source](stakeholder-input-2027-H2/source/) | [Slides](https://claude.ai/artifact/8WDCZ9nsSXbNcV61vf1T1M) |
| Stakeholder input deck, generated each cycle | Same content, rebuilt automatically from `cadence/` at every kickoff | [`cycles/2027-H2/stakeholder_input_deck_2027-H2.pptx`](../cycles/2027-H2/stakeholder_input_deck_2027-H2.pptx) | — |
| Executive deck, LOB dashboard, results workbook (sample run) | LT pre-read produced by the optimizer | [`sample_output/`](../sample_output/) | — |

Notes:

- All figures come from the **synthetic sample portfolio**, not real data. Owner names in the 2027-H2 decks are placeholders until `cadence/owners.yaml` is filled in.
- The **live versions are the masters** for the two Slides decks and the explainer doc. You can edit, comment, present and export them (PowerPoint, PDF, Word) from their own menus. The PDFs here are snapshots, rendered from the `source/` files on Nov 21, 2026.
- The `source/` folders hold each deck's slide files (`deck.json`, `deck.css`, `slides/*.html`). Keep them for version history, or to rebuild a deck.
- Each new cycle gets its stakeholder deck automatically as a `.pptx` in `cycles/<cycle>/`. The executive deck comes from each cycle's optimization run, in `cycles/<cycle>/outputs/`.
