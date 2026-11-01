# ClaimLab — benchmarking multimodal insurance decisions

ClaimLab is an evaluation workspace for developers and claims reviewers testing whether an AI workflow can turn a claimant interview, visual evidence and policy documents into a defensible coverage classification. Its main deliverable is an inspectable benchmark: fixed expected outcomes, recorded model inputs and outputs, per-case comparisons, and a reproducible Harbor evaluation harness.

## What we benchmark

The main model under evaluation is **GPT-6 Luna through OpenAI’s Decisions API**. It classifies 15 synthetic auto and property claims as `covered`, `excluded` or `needs_review`.

Nine cases include generated images, including four non-car property cases. Astra examines those images first and supplies damage locations, observations and uncertainty. Six document cases use OpenAI embeddings to retrieve policy passages, which Astra reviews before classification. Astra also conducts the claimant interview and provides its own separately displayed assessment.

Decisions receives the collected evidence. Expected labels, private claimant fact sheets and Astra’s final coverage verdict are excluded from its input. This evaluates the complete evidence-collection and decision workflow, including its upstream weaknesses—not just an isolated classification model.

## Codex and the Harbor evaluation harness

**We used Codex to build and test the application and its Harbor evaluation integration.** The repository’s `eval_harbor.py` exports a task for each saved Decisions prediction, checks that the prediction still matches its source evidence, and runs Harbor’s Oracle replay agent in Docker. Each verifier compares the saved verdict with the fixed expected label and returns a reward of 1 or 0.

The implemented Harbor run uses **`--agent oracle`**. Codex is the development agent that helped construct and verify this harness; it is not the agent being scored in these trials. Harbor does not make fresh Decisions API calls during replay. This distinction keeps the benchmark target and the tooling used to build it explicit.

The dashboard is the place to inspect the evaluation: expected versus actual verdicts, confidence, inference latency, policy evidence, missing information, original images beside Astra’s overlays, and the preserved conversation with AI voice recordings and captioned video. Harbor supplies the reproducible label-scoring layer behind that view.

## Recorded evaluation results

| Evaluation subset | Correct / total | Label accuracy |
| --- | ---: | ---: |
| Complete benchmark | 10 / 15 | 66.7% |
| Image cases | 8 / 9 | 88.9% |
| Document-retrieval cases | 2 / 6 | 33.3% |

A clean-copy Harbor replay completed all 15 trials with **zero execution errors**, reproducing the saved 10/15 score. All six document cases returned `needs_review`; four disagreed with their expected labels. These failures remain visible rather than being removed or relabeled to improve the score.

The evaluation uses authored synthetic cases and illustrative expected labels, not expert-adjudicated claims. There is no held-out test set or calibrated confidence. Harbor measures label agreement; it does not validate legal correctness, explanation quality, visual localization or voice realism. ClaimBench’s evidence-based framing inspired the design, but ClaimBench evaluates financial filings and its scores are not comparable to this insurance demo.

## One concrete example of how Codex helped

**Codex helped make the Harbor score trustworthy by fixing a task-identity bug.** The earlier evaluation set contained both `auto-flood` and `auto-flood-declarations-missing`. Prefix-based matching could associate a trial result with the wrong case. Codex changed the reward reader to match exact task IDs, added a focused regression check, and rebuilt the summary from the actual Harbor trial outputs.

That improvement matters for benchmarking: a dashboard score must reflect the correct model output for each case. The fix corrected result attribution without changing model predictions or expected labels. The current release also passes nine automated checks, including this task-identity check and checks for stale evidence and input leakage.

## Reproduce the evaluation

Score the included saved results locally, without an API key, Docker or network access:

```sh
python3 eval_harbor.py --score-only
python3 -m unittest -v test_pipeline.py
```

Run the full Harbor replay with Docker running and `uv` installed:

```sh
uv tool install harbor==0.3.0
python3 eval_harbor.py --build-only
python3 eval_harbor.py
```

Inspect the saved demo:

```sh
python3 server.py
# Open http://127.0.0.1:8765/
```

See [README.md](README.md) for setup and optional paid inference, [the per-case results](docs/RESULTS.md) for successes and failures, and [the dataset card](docs/DATASET.md) for provenance and limitations.
