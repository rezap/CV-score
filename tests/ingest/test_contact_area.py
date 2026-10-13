from cvscore.ingest.document import mark_contact_area
from cvscore.models import BlockKind, TextBlock


def blocks() -> list[TextBlock]:
    return [
        TextBlock(kind=BlockKind.HEADING, text="Kari Nordmann"),
        TextBlock(kind=BlockKind.PARAGRAPH, text="Senior Data Scientist"),
        TextBlock(
            kind=BlockKind.PARAGRAPH,
            text="Oslo, Norway  kari.nordmann@example.no  98 76 54 32  kari-n",
        ),
        TextBlock(kind=BlockKind.HEADING, text="Summary"),
        TextBlock(
            kind=BlockKind.PARAGRAPH,
            text="Data scientist with ten years of experience; built the support mailbox "
            "triage that routes help@example.no requests automatically across five teams.",
        ),
        TextBlock(kind=BlockKind.PARAGRAPH, text="Experience 01.2019 - 05.2021"),
    ]


def test_long_contact_line_at_top_of_cv_is_flagged() -> None:
    bs = blocks()
    mark_contact_area(bs, top_of_cv=True)
    assert [b.is_contact for b in bs] == [True, True, True, False, False, False]


def test_only_short_contact_lines_outside_cvs() -> None:
    bs = blocks()
    mark_contact_area(bs, top_of_cv=False)
    assert not any(b.is_contact for b in bs)


def test_name_with_initial_at_top_of_cv_is_flagged() -> None:
    bs = [
        TextBlock(kind=BlockKind.HEADING, text="Claud D. Park"),
        TextBlock(kind=BlockKind.HEADING, text="Experience"),
    ]
    mark_contact_area(bs, top_of_cv=True)
    assert bs[0].is_contact
