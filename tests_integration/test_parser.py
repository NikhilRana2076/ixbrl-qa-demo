"""Parser behaviour on iXBRL edge cases (real src/, fictional fixture).

Each test names one rule from the iXBRL spec or a quirk seen in real UK filings.
When a real filing breaks the parser, add a minimal case to edge_cases.xhtml and
a test here before fixing it.
"""
import pytest
from conftest import FIXTURES


def fact(facts, concept, ctx="FY25"):
    return facts[(concept, ctx)]


def test_counts(edge_filing):
    assert len(edge_filing.numeric_facts) == 11
    assert len(edge_filing.narrative_facts) == 2


def test_scale_applied(facts):
    assert fact(facts, "core:Turnover").value == 1_234_000


def test_sign_attribute_is_authoritative(facts):
    assert fact(facts, "core:ProfitLoss").value == -56_789


def test_brackets_outside_tag_without_sign_stay_positive(facts):
    # Printed as "(12,000)" but tagged without sign="-": expenses are positive in XBRL.
    assert fact(facts, "core:AdministrativeExpenses").value == 12_000


def test_xsi_nil(facts):
    f = fact(facts, "core:DividendsPaid")
    assert f.is_nil is True


def test_fixed_zero_dash_reads_as_zero(facts):
    assert fact(facts, "core:CashBankOnHand", "END25").value == 0


def test_digits_split_by_whitespace(facts):
    assert fact(facts, "core:NetAssetsLiabilities", "END25").value == 32_238


def test_ix_exclude_removed_from_numeric(facts):
    f = fact(facts, "core:Creditors", "END25")
    assert f.value == 1_000
    assert "*" not in f.raw_text


def test_dimensional_context(edge_filing, facts):
    uk = fact(facts, "core:Turnover", "FY25_UK")
    assert uk.value == 900_000
    ctx = edge_filing.contexts["FY25_UK"]
    assert ctx.dimensions == {"core:GeographicalAreaDimension": "core:UnitedKingdom"}
    assert edge_filing.contexts["FY25"].dimensions == {}


def test_periods(edge_filing):
    fy = edge_filing.contexts["FY25"]
    assert (fy.period_type, fy.start_date, fy.end_date) == ("duration", "2024-04-01", "2025-03-31")
    end = edge_filing.contexts["END25"]
    assert (end.period_type, end.instant) == ("instant", "2025-03-31")


def test_divide_unit_and_negative_scale(edge_filing, facts):
    eps = fact(facts, "core:BasicEarningsPerShare")
    assert eps.value == pytest.approx(0.236)
    assert edge_filing.units["GBPperShare"].label() == "iso4217:GBP/xbrli:shares"


def test_extension_concepts_detected(facts):
    assert fact(facts, "ext:FranchiseIncome").is_extension is True
    assert fact(facts, "core:Turnover").is_extension is False


@pytest.mark.xfail(
    strict=True,
    reason="Known gap: clean_number_text always strips commas, so "
    "ixt:num-comma-decimal '1.234,5' parses as 1.2345. Rare in UK filings; "
    "fix by honouring the format attribute, then remove this marker.",
)
def test_comma_decimal_format(facts):
    assert fact(facts, "core:AverageNumberEmployeesDuringPeriod").value == pytest.approx(1234.5)


def test_continuation_chain_and_exclude(edge_filing):
    gc = next(n for n in edge_filing.narrative_facts if n.local_name == "GoingConcernDisclosure")
    assert gc.continuation_count == 2
    assert gc.text == (
        "The directors have assessed going concern over twelve months "
        "and consider the company able to meet its liabilities as they fall due."
    )
    assert "[1]" not in gc.text


def test_external_entities_are_not_resolved(tmp_path):
    from src.ingestion.parser import parse_filing

    evil = tmp_path / "xxe.xhtml"
    evil.write_text(
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
        '<body><ix:nonNumeric name="a:b" contextRef="c">&e;</ix:nonNumeric></body></html>'
    )
    filing = parse_filing(evil)
    assert all("root:" not in n.text for n in filing.narrative_facts)


def test_fixture_is_in_place():
    assert (FIXTURES / "edge_cases.xhtml").exists()
