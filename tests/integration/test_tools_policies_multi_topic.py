"""Slice 09-05-A characterization: two policy topics through execute_tool (envelope level).

docs/plans/09-05-policy-retrieval-fix.md, Test Checklist. A real index prepared from the
synthetic policy_kit PDF in tmp_path and loaded by the real load_policy_index. These cases
pin that the search_policies envelope, keys, sections and internal references (section ID
and pages, Review 10-06) are the same whatever the query shape, so the 09-05-A guidance
(separate searches) changes no tool output. Born green: no tool or service code changes
in 09-05-A. The index hash does not match the curated notes, so every envelope carries
curated_notes_unavailable (as fixed guidance since Review 10-06).
Expected values are literals from the MINI oracle; no production serializer is the oracle.
"""

import json

import pytest

TOOL = "search_policies"
ENVELOPE_KEYS = ["tool", "status", "data", "error"]
# Review 10-06: public payload; citations (section ID and pages) are internal references.
DATA_KEYS = ["sections", "guidance"]
SECTION_KEYS = ["title", "heading_path", "text"]
CURATED_UNAVAILABLE = (
    "Differences between these texts were not reviewed. If two texts give different"
    " values, do not choose one; say that the detail needs confirmation."
)
HOURS_QUERY = "horário de funcionamento"
ADDRESS_QUERY = "endereço"
COMBINED_QUERY = "Qual o horário de funcionamento da loja e qual é o endereço?"


@pytest.fixture
def ctx(tmp_path, policy_kit, tools_kit):
    return tools_kit.context(policy_index_path=tools_kit.policy_index(policy_kit, tmp_path))


def search(tools_kit, context, query):
    execution, body = tools_kit.run(TOOL, json.dumps({"query": query}), context)
    assert execution.status == "ok"
    assert list(body) == ENVELOPE_KEYS
    assert (body["tool"], body["status"], body["error"]) == (TOOL, "ok", None)
    assert list(body["data"]) == DATA_KEYS
    for section in body["data"]["sections"]:
        assert list(section) == SECTION_KEYS
    return execution, body["data"]


def test_pm10_separate_and_combined_envelopes(ctx, tools_kit):
    hours_run, hours = search(tools_kit, ctx, HOURS_QUERY)
    address_run, address = search(tools_kit, ctx, ADDRESS_QUERY)
    combined_run, combined = search(tools_kit, ctx, COMBINED_QUERY)

    assert hours_run.references == ("policy:2@p2",)
    assert address_run.references == ("policy:1.1@p2", "policy:front_matter@p1")
    assert combined_run.references == ("policy:2@p2",)
    for data in (hours, address, combined):
        assert data["guidance"] == [CURATED_UNAVAILABLE]

    # The hours section is the same object in the single-topic and the combined envelope.
    assert combined["sections"][0] == hours["sections"][0]
    assert hours["sections"][0]["title"] == "Horário de Funcionamento"


def test_pm11_payload_carries_no_query_text(ctx, tools_kit):
    execution, _ = search(tools_kit, ctx, COMBINED_QUERY)
    assert "Qual o" not in execution.payload
    assert "matched_terms" not in execution.payload
    assert '"query"' not in execution.payload
