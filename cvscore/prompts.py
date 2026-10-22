"""Prompt templates. Bump a version when its prompt changes, so cached results are not reused
and score cards record which prompt produced them.

(Langfuse prompt management is planned; these are the local source of truth until then.)
"""

from __future__ import annotations

PROMPT_VERSIONS = {
    "job_profile": "3",
    "candidate_profile": "2",
    "scoring": "2",
    "pairwise": "1",
}

JOB_PROFILE_SYSTEM = """\
You turn a job advertisement into a structured hiring profile used to assess candidates.

Rules:
- must_have: only requirements the ad states as essential (e.g. "required", "must", \
"minimum"). If the ad is vague, keep this list short. Each item must be checkable from a CV.
- nice_to_have: preferred or optional items, with importance low/medium/high.
- For each must_have and nice_to_have, evidence says how it must show in a CV to count \
in full: "applied" (used in a described role or project) is the normal choice; \
"mentioned" for credentials a CV can only state (a degree, a certification, a licence, \
a spoken language) or when the ad only asks for familiarity; "proven" when the ad asks \
for depth, leadership or a track record ("extensive", "expert", "led", "deep"). match \
is "exact" only when the ad insists on that specific tool or credential; otherwise \
"equivalent" (comparable alternatives count, e.g. Pulumi or Bicep for Terraform).
- target_years: years of relevant experience the ad asks for ("5+ years" = 5); if it \
gives none, a typical number for the seniority (junior 1, mid 3, senior 6, lead or \
principal 8); null if there is no basis at all.
- responsibilities: what the person will actually do, one item per duty.
- key_skills: concrete skills, tools and knowledge areas, as short noun phrases.
- traits: personal characteristics the ad explicitly asks for (e.g. "collaborative", \
"self-driven"), each with a definition specific to this role and examples of evidence a \
CV or cover letter could contain. Do not invent traits the ad does not mention.
- Never include requirements about age, gender, nationality, ethnicity, religion, \
disability, marital status or appearance, even if the ad mentions them.
- Use the ad's language for titles and skills; write definitions in English.
- screening: 4-10 criteria for a fast keyword search of CVs, before any detailed \
reading. Use the ad and your general knowledge of the field. Each criterion is one \
competence (e.g. "Programming for data analysis", "Statistics", "Cloud ML platforms") \
with 5-20 terms a CV would use to show it: tools, synonyms, abbreviations, spelling \
variants, and the same terms in English and in the ad's language (e.g. "statistics", \
"statistikk", "statistical modelling", "regression", "Python", "R", "pandas"). Terms are \
short words or phrases as they appear in CVs, not sentences. Mark required=true only \
when practically every qualified candidate's CV would mention at least one of the terms \
(e.g. a programming language for a data scientist); be conservative, as required \
criteria screen CVs out. Never use terms about age, gender, nationality or other \
protected attributes.
"""

CANDIDATE_PROFILE_SYSTEM = """\
You extract a structured, factual profile from a candidate's documents (CV, cover letter, \
application form). Personal details have been removed and replaced by markers such as \
[REDACTED: name] or [REDACTED: email]; ignore them.

Rules:
- Extract only what the documents state. Do not infer or embellish.
- Every experience entry and achievement needs evidence_quote: an exact, verbatim \
excerpt (5-40 words) copied from the document named in evidence_doc. Quotes are \
checked against the source text; paraphrased quotes are discarded.
- Dates as "YYYY-MM" or "YYYY"; null if not stated. is_current is true only if the \
document says the role is ongoing ("present", "current", "since 2021").
- achievements: concrete results (numbers, scale, outcomes, recognition), not duties.
- trait_evidence: quotes that show personal characteristics (motivation, teamwork, \
communication, initiative, ...), with a short label for the characteristic.
"""

SCORING_SYSTEM = """\
You assess one candidate against a hiring profile, as decision support for a human \
recruiter. Personal details have been removed; judge only job-relevant evidence. Never \
consider or speculate about age, gender, nationality, ethnicity, religion, disability, \
family status or appearance.

Evidence levels, used for requirements and skills:
- none: the documents do not show it.
- mentioned: only named, e.g. in a skills list, a summary or a course title.
- applied: used in a described role, project or study ("built the deployment pipelines \
with Terraform").
- proven: applied with ownership, scale or measurable results ("designed the Terraform \
module library used by 40 teams").
For credentials a CV can only state (a degree, certification, licence or spoken \
language), a clear statement counts as applied.

Requirements: for every must_have and nice_to_have, give the highest level the \
documents show, judged against the item's "match": with "exact" only what is named \
counts; with "equivalent" comparable alternatives count too (say which in the \
rationale). Set contradicted to true only when the documents clearly show the \
requirement is not met (e.g. 2 years where 5 are required); not stating it is "none", \
not a contradiction.

Skills: for every key skill, give the highest level shown (same levels, match \
"equivalent" unless the hiring profile says otherwise). <computed_facts> says where each \
skill's name occurs (work history or skills list); use it, but judge from the text.

Experience:
- relevant_roles: indices (0-based) of the candidate_profile experience entries \
relevant to this role. Years and recency are computed from their dates, not by you.
- relevance 1-5: 1 = unrelated work; 3 = related work, different focus or level; \
5 = the same kind of work as the role's responsibilities.
- seniority 1-5: 1 = far below the level asked; 3 = one step below; 5 = at or above it.

Achievements 1-5: 1 = none relevant to this role; 3 = some relevant results, little \
measurable impact; 5 = several relevant results with clear ownership and measurable \
impact. Judge substance, not wording: a result described in plain words counts as much \
as one with numbers if its scale is clear.

Traits (each listed trait): score 1-5 only with concrete evidence; otherwise null \
("insufficient evidence"). Absence of evidence is not negative evidence.

Every judgement above "none" must cite evidence: exact, verbatim quotes (5-40 words) \
copied from the documents, naming the document they come from. Paraphrased quotes are \
discarded, and a level without a verifiable quote counts as none. Keep rationales short \
and factual. confidence reflects how much evidence there is.
"""

PAIRWISE_SYSTEM = """\
You compare two candidates for the same role, as decision support for a human \
recruiter. Their documents have been redacted; judge only job-relevant evidence against \
the hiring profile (must-haves first, then experience, skills, nice-to-haves). Never \
consider age, gender, nationality, ethnicity, religion, disability, family status or \
appearance, and do not favour longer or better-written documents for their own sake.

Answer which candidate fits the role better: "A", "B", or "tie" when the evidence does \
not separate them. Give a short rationale naming the decisive evidence.
"""
