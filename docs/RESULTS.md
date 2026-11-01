# Recorded results — November 1, 2026

Main target: `gpt-6-luna` through the Decisions API. The latest saved run scores **10/15 (66.7%)**, verified by Harbor saved-verdict replay. Realtime speech was refreshed afterward without changing decision evidence or labels.

| Case | Evidence | Expected | Actual | Match | Latency (s) |
| --- | --- | --- | --- | --- | ---: |
| auto-collision | Image | covered | covered | Yes | 2.43 |
| auto-flood | Image | covered | needs_review | No | 2.75 |
| auto-vandalism | Image | needs_review | needs_review | Yes | 3.35 |
| home-stolen-laptop | Documents | needs_review | needs_review | Yes | 0.76 |
| home-delayed-claim | Documents | needs_review | needs_review | Yes | 0.68 |
| auto-animal-contact | Image | covered | covered | Yes | 2.77 |
| auto-animal-swerve | Documents | excluded | needs_review | No | 0.61 |
| auto-owner-intent | Documents | excluded | needs_review | No | 0.85 |
| auto-disputed-actor | Image | needs_review | needs_review | Yes | 2.97 |
| auto-business-no-surcharge | Documents | excluded | needs_review | No | 0.89 |
| auto-business-surcharge | Documents | covered | needs_review | No | 0.72 |
| property-laptop-surge | Image | excluded | excluded | Yes | 3.36 |
| property-burst-pipe-belongings | Image | covered | covered | Yes | 3.01 |
| property-smoke-uncertain | Image | needs_review | needs_review | Yes | 2.66 |
| property-forced-entry-report-unknown | Image | needs_review | needs_review | Yes | 2.71 |

## What these results mean

- All six document cases returned `needs_review`; two match the frozen labels. Retrieval omitted important language in some cases, and the reviewers identified missing exclusions or endorsements. The score does not isolate retrieval quality from model behavior.
- Flood changed to `needs_review` on the same broader rerun despite retaining the image workflow, so the overall score drop cannot be attributed solely to RAG.
- The disputed-actor interview omitted conflicting-witness facts. Matching the expected label does not prove the model used the intended evidence.
- The Decisions choice response contains no coverage rationale; Astra’s separately displayed explanation is not a verified explanation of Luna’s choice.
- Harbor checks labels, not citation semantics, damage-box accuracy, audio quality, confidence calibration or legal correctness.
- The expected labels are illustrative, not independently reviewed. One rollout over 15 authored cases is insufficient to estimate production reliability.

## Reproduce the saved score

```sh
python3 eval_harbor.py --score-only
python3 -m unittest -v test_pipeline.py
```

For the container replay, install Harbor 0.3.0 and start Docker, then run `python3 eval_harbor.py`. Fresh OpenAI inference is separate, paid, and can produce different results.

The historical five-case baseline in the dashboard has different fixtures and is not a directly comparable evaluation.

## Combined workflow rubric (retrospective)

The original Luna verdict metric remains **10/15 (66.7%)**. A separate assistant-authored interview audit rates Astra **13/15 (86.7%)** for obtaining material information or eliciting uncertainty. Equal weighting gives a **76.7% workflow index**. This is not a new verdict-accuracy result or an independently validated score.

Interview failures: `auto-disputed-actor` (no question seeking available witness accounts) and `property-smoke-uncertain` (no targeted source-context question). Criteria, policy references and supporting turns are recorded in [interview_rubric.json](../interview_rubric.json). Criteria were written after viewing the run; new runs require fresh review, and independent expert validation remains outstanding.

Run `python3 eval_harbor.py` followed by `python3 workflow_eval.py`. Harbor replays Luna labels; the second command attaches the transparent interview audit and combined arithmetic to the summary. No voice-quality or semantic-explanation score is claimed.
