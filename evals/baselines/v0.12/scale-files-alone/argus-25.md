# The 25 Argus questions on the scale corpus, beside v0.11

This is the files-alone baseline for v0.12. The command was:

```sh
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet \
  --judge-model sonnet --corpus scale --configuration files-alone --runs 3
```

It ran in the VM at commit 1a8ba03 on 2026-10-17.

- **Ingest:** 1,244 episodes, none quarantined.
- **All 40 questions:** correctness 0.94 (0.93 to 0.95), evidence recall 0.90 (0.89 to 0.92), citation validity 0.98 (0.98 to 0.99).
- **Cost:** $12.30 for the 3 runs; per answer, a median of $0.094, 5 turns, and 11.7 s.

Below, the 25 Argus questions are scored on their own, beside v0.11's single run on the Argus corpus (`evals/baselines/v0.11`).

These scores aren't strictly comparable with v0.11's. In v0.12 the eval system prompt no longer says "search L0 before you answer", for every configuration (#49). The corpus is also 19 times larger.

| | Correctness | Evidence recall | Citation validity |
|---|---|---|---|
| v0.11 (Argus corpus, 1 run) | 0.96 | 0.98 | 1.00 |
| Scale corpus, run 1 | 0.96 | 0.93 | 0.99 |
| Scale corpus, run 2 | 0.92 | 0.90 | 0.97 |
| Scale corpus, run 3 | 0.92 | 0.91 | 0.98 |
| Scale corpus, mean (lowest to highest run) | 0.93 (0.92 to 0.96) | 0.91 (0.90 to 0.93) | 0.98 (0.97 to 0.99) |

| Question | Category | v0.11 | Scale corpus, runs 1 to 3 |
|---|---|---|---|
| flight-confirmation | single_fact_recall | yes | yes, yes, yes |
| swim-deadline | single_fact_recall | yes | yes, yes, yes |
| wedding-gift | single_fact_recall | yes | yes, yes, yes |
| knee-referral | attribution | yes | yes, yes, yes |
| island-sink | attribution | yes | yes, yes, yes |
| reading-request | attribution | yes | yes, yes, yes |
| kitchen-timeline | timeline_narrative | yes | yes, yes, yes |
| blood-work-timeline | timeline_narrative | yes | yes, yes, yes |
| mara-swim-preference | stance_change | yes | yes, yes, yes |
| contractor-leaning | stance_change | yes | yes, yes, yes |
| my-commitments | open_loops | yes | yes, yes, yes |
| waiting-on | open_loops | yes | yes, no, no |
| this-saturday | cross_channel_synthesis | yes | yes, yes, yes |
| duluth-friday | cross_channel_synthesis | yes | yes, yes, yes |
| saturday-swim-conflicts | cross_channel_synthesis | yes | yes, yes, yes |
| dave-asks | entity_resolution | no | no, no, no |
| dave-addresses | entity_resolution | yes | yes, yes, yes |
| texas-flight-times | paraphrase | yes | yes, yes, yes |
| cabinet-guy-update | paraphrase | yes | yes, yes, yes |
| leg-specialist | paraphrase | yes | yes, yes, yes |
| hitched-lodging | paraphrase | yes | yes, yes, yes |
| swim-instructor | abstention | yes | yes, yes, yes |
| cholesterol | abstention | yes | yes, yes, yes |
| day-of-week | canary | yes | yes, yes, yes |
| saturday-date | canary | yes | yes, yes, yes |

`waiting-on` got Rick's quote and Dave's reading right in every run, but missed the blood-work results in runs 2 and 3. `dave-asks` failed in v0.11 too.
