"""Case P10: line cleaning rules N1-N5 (docs/specs/04-policies.md, P-01).

Inputs and expected outputs are hand-written strings. No PDF, no file.
"""

from emporio_agent.data.policy_index import clean_page_lines


def test_p10_n1_drops_blank_lines_and_returns_tuples() -> None:
    result = clean_page_lines(["a\n\n   \n\t\nb\r\n"])

    assert result == (("a", "b"),)
    assert type(result) is tuple
    assert type(result[0]) is tuple


def test_p10_empty_input_and_empty_page() -> None:
    assert clean_page_lines([]) == ()
    assert clean_page_lines(["", " \n "]) == ((), ())


def test_p10_n2_compacts_whitespace() -> None:
    assert clean_page_lines(["   a    b  \nc\t\td"]) == (("a b", "c d"),)


def test_p10_n3_composes_unicode() -> None:
    decomposed = "Sintético café"

    assert clean_page_lines([decomposed]) == (("Sintético café",),)


def test_p10_n4_header_removed_only_when_first_on_two_or_more_pages() -> None:
    pages = ["H\nx", "H\ny", "z\nH"]

    assert clean_page_lines(pages) == (("x",), ("y",), ("z", "H"))


def test_p10_n4_header_first_on_one_page_only_is_kept() -> None:
    assert clean_page_lines(["H\nx", "y\nH"]) == (("H", "x"), ("y", "H"))


def test_p10_n4_single_page_keeps_its_first_line() -> None:
    assert clean_page_lines(["H\nx"]) == (("H", "x"),)


def test_p10_n4_compares_after_compaction_and_nfc() -> None:
    pages = ["  Manual   Sintético \nx", "Manual Sintético\ny"]

    assert clean_page_lines(pages) == (("x",), ("y",))


def test_p10_n4_page_reduced_to_nothing() -> None:
    assert clean_page_lines(["H", "H\nx"]) == ((), ("x",))


def test_p10_n5_footer_with_other_number_kept() -> None:
    assert clean_page_lines(["a", "b\nPágina 3"]) == (("a",), ("b", "Página 3"))


def test_p10_n5_footer_text_in_the_middle_kept() -> None:
    assert clean_page_lines(["a", "b\nPágina 2\nc"]) == (("a",), ("b", "Página 2", "c"))


def test_p10_n5_last_line_with_own_page_number_removed() -> None:
    assert clean_page_lines(["a", "b\nPágina 2"]) == (("a",), ("b",))
    assert clean_page_lines(["a\nPágina 1"]) == (("a",),)


def test_p10_n5_matches_after_compaction_and_only_exact_form() -> None:
    assert clean_page_lines(["a", "b\n  Página    2  "]) == (("a",), ("b",))
    assert clean_page_lines(["a", "b\nPágina 2."]) == (("a",), ("b", "Página 2."))
    assert clean_page_lines(["a", "b\npágina 2"]) == (("a",), ("b", "página 2"))


def test_p10_header_and_footer_together() -> None:
    pages = ["Capa", "H\nx\nPágina 2", "H\ny\nPágina 3"]

    assert clean_page_lines(pages) == (("Capa",), ("x",), ("y",))


def test_p10_text_is_otherwise_unchanged() -> None:
    line = "• Frete grátis acima de R$ 500,00 — (11) 4000-1000; e-mail “x” 1º"

    assert clean_page_lines([line]) == ((line,),)
