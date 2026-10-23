"""Grouping files into candidates: the fast merge finds the same groups as the plain one
(fictional names and addresses)."""

import random
import time
from pathlib import Path

from cvscore.ingest.discovery import (
    CandidateFile,
    _conflict,
    _different_named_folders,
    _Group,
    _merge_all,
    discover_folder,
)
from cvscore.ingest.identify import file_tokens, filename_key, same_person
from cvscore.models import DocType


def reference_merge(groups: list[_Group]) -> list[_Group]:
    """The plain algorithm: merge the first mergeable pair, then start over."""

    def keys(g: _Group) -> set[tuple[str, str]]:
        found = {("email", e) for e in g.emails()}
        for f in g.files:
            key = filename_key(f.path)
            if len(key) >= 2 and key != " ".join(file_tokens(f.path.stem)):
                found.add(("file name", key))
        return found

    merged = True
    while merged:
        merged = False
        for i, a in enumerate(groups):
            for j in range(i + 1, len(groups)):
                b = groups[j]
                if _conflict(a, b) or _different_named_folders(a, b):
                    continue
                why = {kind for kind, _ in keys(a) & keys(b)}
                if not why and any(same_person(x, y) for x in a.names() for y in b.names()):
                    why = {"name"}
                if why:
                    a.files.extend(b.files)
                    a.reasons |= why
                    a.folder = a.folder or b.folder
                    del groups[j]
                    merged = True
                    break
            if merged:
                break
    return groups


FIRST = ["Kari", "Ola", "Jan", "Mia", "Sam"]
LAST = ["Example", "Sample", "Testesen", "Demo"]


def random_groups(rng: random.Random, n: int) -> list[_Group]:
    """Files whose names, mailboxes, file names and folders overlap at random: shared
    mailboxes, people with the same name, conflicting CVs and chains of merges."""

    def person() -> str:
        return f"{rng.choice(FIRST[:2])} {rng.choice(LAST[:2])}"

    groups = []
    for i in range(n):
        doc_type = rng.choice([DocType.CV, DocType.CV, DocType.COVER_LETTER, DocType.OTHER])
        names = [(person(), "top of the CV")] if rng.random() < 0.6 else []
        emails = {rng.choice(["a@example.com", "b@example.com", "c@example.com"])}
        emails = emails if rng.random() < 0.5 else set()
        stem = rng.choice([person().replace(" ", "_") + "_cv", f"file{i}", "cv"])
        path = Path(f"/apps/{i:03d}_{stem}.pdf")
        file = CandidateFile(path, doc_type, names=names, emails=emails)
        # A folder named after a person is kept apart from other people's folders; one
        # named otherwise ("batch 2") is not, so merging into it can unblock earlier pairs.
        folder = rng.choice([Path(f"/apps/{person()}"), Path("/apps/batch 2"), None, None])
        groups.append(_Group(files=[file], folder=folder))
    return groups


def snapshot(groups: list[_Group]) -> list[tuple[list[str], set[str], Path | None]]:
    return [(sorted(str(f.path) for f in g.files), g.reasons, g.folder) for g in groups]


def test_fast_merge_matches_the_plain_algorithm() -> None:
    for seed in range(2000):
        n = random.Random(seed).randint(2, 10)
        fast = _merge_all(random_groups(random.Random(seed), n))
        plain = reference_merge(random_groups(random.Random(seed), n))
        assert snapshot(fast) == snapshot(plain), f"seed {seed}"


def test_a_large_flat_folder_groups_quickly(tmp_path: Path) -> None:
    """300 applicants in one folder, a CV and often a letter each: grouping them used to
    rescan every pair after every merge (minutes of CPU); now it is a fraction of that."""
    for i in range(300):
        first, last = FIRST[i % 5] + "x" * (i // 25), LAST[i % 4] + chr(97 + i % 23)
        mail = f"{first}.{last}@example.com".lower()
        cv = f"{first} {last}\n{mail}\n\nExperience\nEngineer, Example AS, 2019 - 2024\n"
        (tmp_path / f"{first}_{last}_CV.txt").write_text(cv)
        if i % 3:
            letter = f"Dear hiring manager,\n\nI am applying.\n\nKind regards,\n{first} {last}\n"
            (tmp_path / f"{first}_{last}_cover_letter.txt").write_text(letter)
    start = time.process_time()
    sources = discover_folder(tmp_path)
    assert len(sources) == 300
    assert sum(len(s.files) for s in sources) == 500
    assert time.process_time() - start < 20  # was over a minute before the fix
