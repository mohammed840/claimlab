# ClaimLab

**An insurance claims demo that connects conversations, visual evidence, and policy-based decisions.**

## Who it’s for

Insurance claims teams and developers building AI tools to help reviewers understand evidence, spot missing information, and evaluate coverage decisions.

## The problem

Reviewing a claim means bringing together a claimant’s account, photographs, and policy terms. Visible damage alone cannot establish coverage: important facts may be missing, exclusions may apply, and uncertainty needs to be explained. Developers also need a way to check whether their claims workflow reaches the expected outcome.

## What it does

ClaimLab demonstrates this workflow through 15 synthetic auto and property claims, including four non-car image cases involving damaged belongings, a laptop, smoke, and a theft scene.

- A claimant agent describes the incident, while GPT-6 Astra asks policy-relevant follow-up questions. The dashboard preserves the transcript and generated voice recordings.
- Astra examines available images and highlights visible damage, with labels and uncertainty notes shown beside the original picture.
- OpenAI’s Decisions API, using GPT-6 Luna, receives the policy excerpts, interview, original image, and Astra’s visual findings. It selects **covered**, **excluded**, or **requires further review**.
- The dashboard compares each decision with a fixed expected outcome and shows Astra’s separate explanation, policy citations, confidence, latency, and media.
- Six document cases use OpenAI embeddings to retrieve policy passages for an Astra document review before Decisions classifies the claim.
- Harbor replays the saved Decisions API verdicts and checks them against the expected labels. The current run matches **10 of 15 labels (66.7%)**; the six document cases match **2 of 6**. These failures remain visible for evaluation.

The demo uses synthetic images and fictional claims with selected provisions from public California policy forms and samples. Expected labels are illustrative and have not been independently reviewed by insurance experts. The score describes this small, authored workflow evaluation; it is not a production accuracy estimate. ClaimLab is a prototype for supporting human review, not an automated system for issuing binding claim decisions.
