"""Slice 09-05-A characterization: questions with two policy topics (service level).

docs/plans/09-05-policy-retrieval-fix.md, Test Checklist; Spec 04 P-10 (coverage filter,
half-score threshold, order by score, level and position). These cases pin the current,
approved ranking on the hand-built MINI index (tests/conftest.py, invented values) to
support the premise of the 09-05-A guidance: one search per topic reaches each topic, and
a combined query can lose a topic. No service code changes in 09-05-A, so every case is
expected to pass before and after the guidance (born green). They say nothing about what
a model sends. Expected values are literals derived by hand from MINI and P-10.
"""

from typing import Any

from emporio_agent.services.policies import PolicySearchResult, search_policies

HOURS_QUERY = "horário de funcionamento"
ADDRESS_QUERY = "endereço"
COMBINED_QUERY = "Qual o horário de funcionamento da loja e qual é o endereço?"


def _search(kit: Any, query: str) -> PolicySearchResult:
    return search_policies(kit.index(), query, notes=kit.notes())


def _ids(result: PolicySearchResult) -> list[tuple[str, str, tuple[int, ...]]]:
    return [(s.section_id, s.selected_by, s.pages) for s in result.sections]


def _codes(result: PolicySearchResult) -> list[str]:
    return [notice.code for notice in result.notices]


# PM01: single topic, hours. Section 2 has the horario concept in its title (weight 3);
# no other MINI section has it.
def test_pm01_hours_query_returns_the_hours_section(policy_retrieval):
    result = _search(policy_retrieval, HOURS_QUERY)
    assert result.status == "ok"
    assert _ids(result) == [("2", "match", (2,))]
    assert result.omitted_section_ids == ()
    assert _codes(result) == ["no_holiday_calendar"]


# PM02: single topic, address. 1.1 (body word) and front_matter (street word) both weigh 1;
# the deeper level comes first.
def test_pm02_address_query_returns_the_address_sections(policy_retrieval):
    result = _search(policy_retrieval, ADDRESS_QUERY)
    assert result.status == "ok"
    assert _ids(result) == [("1.1", "match", (2,)), ("front_matter", "match", (1,))]
    assert result.omitted_section_ids == ()
    assert _codes(result) == ["contact_numbers_differ", "email_spelling_differs"]


# PM03: combined hours + address. All candidates cover one concept; 2 scores 3, 1.1 and
# front_matter score 1, and 2 * 1 < 3 drops them. They are not in omitted_section_ids.
def test_pm03_combined_query_loses_the_address_topic(policy_retrieval):
    result = _search(policy_retrieval, COMBINED_QUERY)
    assert result.status == "ok"
    assert _ids(result) == [("2", "match", (2,))]
    assert result.omitted_section_ids == ()
    assert _codes(result) == ["no_holiday_calendar"]
    assert "more_sections_matched" not in _codes(result)


# PM04: separate searches reach both topics with the same sections, pages, text and
# notices as the single-topic searches; the hours section of the combined query is the
# same section as in the hours query.
def test_pm04_separate_searches_reach_both_topics(policy_retrieval):
    hours = _search(policy_retrieval, HOURS_QUERY)
    address = _search(policy_retrieval, ADDRESS_QUERY)
    combined = _search(policy_retrieval, COMBINED_QUERY)

    found = {s.section_id for s in (*hours.sections, *address.sections)}
    assert found == {"2", "1.1", "front_matter"}
    assert {s.section_id for s in combined.sections} == {"2"}

    hours_section = hours.sections[0]
    combined_section = combined.sections[0]
    for field in ("section_id", "title", "heading_path", "pages", "text", "selected_by"):
        assert getattr(combined_section, field) == getattr(hours_section, field), field
    assert hours.source == address.source == combined.source


# PM05: a combined query is not always reduced to one topic. 5.1 has the frete concept in
# its title ("Entregas") and 2 the horario concept in its title: both weigh 3, both kept;
# 5.1 first by level.
def test_pm05_combined_query_whose_topics_score_alike_returns_both(policy_retrieval):
    result = _search(policy_retrieval, "horário e frete")
    assert result.status == "ok"
    assert _ids(result) == [("5.1", "match", (4,)), ("2", "match", (2,))]
    assert result.omitted_section_ids == ()
    assert _codes(result) == ["free_shipping_threshold_gap", "no_holiday_calendar"]
