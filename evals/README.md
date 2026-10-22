# Evaluation sets

Fictional candidates with known answers, to check that the ranking behaves as intended
and to measure how much it moves between runs. Every person, company and CV here is
made up.

Each set is a folder:

- `job.yaml`: the hiring profile.
- `candidates/`: one CV per candidate (plain text, so changes are easy to review).
- `expected.yaml`: `tiers` (1 = strong fit, 2 = adjacent, 3 = unrelated), `better`
  pairs (the first must score higher), `same` pairs (must score within `tolerance`).

Run one against your configured model (costs real model calls; the cache is not used,
so each run is a fresh sample):

```bash
cvscore eval evals/cloud-architect --repeats 3 --out eval-out/
```

The report shows the share of cross-tier pairs in the right order, Kendall's tau against
the tiers, each controlled pair, and the spread of every candidate's total across runs.
The live CI job runs it too (`tests/live`).

## cloud-architect

A senior cloud solution architect role, with Terraform-or-equivalent IaC as a must-have
(evidence: applied). The controlled pairs:

| Pair | What it checks |
|---|---|
| Kofi Mensah > Tomasz Nowak | Identical CVs except that Kofi used Terraform in a role; Tomasz only lists it |
| Mateo Ruiz > Elif Kaya | An architect with equivalent IaC (Bicep) beats an engineer without architecture work |
| Elif Kaya > Nora Haugen | Real Terraform work beats a project manager who lists cloud words in her skills |
| Ingrid Solheim > Kofi Mensah | An architect beats a developer with some IaC |
| Ingrid Solheim = Hanna Lindqvist | The same CV under another name scores the same |
