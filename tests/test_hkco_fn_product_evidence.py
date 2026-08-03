from custom.service.HKCO_FN_PRODUCT_evidence import (
    EvidenceHypothesisBuilder,
    RegexTableEvidenceScanner,
)
from custom.service.HKCO_FN_PRODUCT_fact_model import TableRef


def test_scans_every_table_but_builds_only_revenue_hypotheses():
    tables = [
        TableRef("p1:0", 1, "收入按業務分部", [
            ["", "航運業務", "碼頭業務", "總計"],
            ["向外部客户銷售", "10", "2", "12"],
        ]),
        TableRef("p2:0", 2, "資產負債表", [["現金", "99"]]),
    ]
    evidence = RegexTableEvidenceScanner().scan(tables)
    hypotheses = EvidenceHypothesisBuilder().build(evidence)
    assert len(evidence) == 2
    assert {item.table.table_id for item in evidence} == {"p1:0", "p2:0"}
    assert {item.table_ids for item in hypotheses} == {("p1:0",)}
    assert hypotheses[0].kind == "column_identity_metric_row"
    assert hypotheses[0].revenue_basis == "external"


def test_hierarchy_evidence_creates_parent_and_leaf_alternatives():
    table = TableRef("p3:0", 3, "產品收入明細", [
        ["產品", "二零二五年收入"],
        ["汽車服務", "12"],
        ["其中:輪胎", "7"],
        ["-保養", "5"],
        ["總計", "12"],
    ])
    evidence = RegexTableEvidenceScanner().scan([table])
    hypotheses = EvidenceHypothesisBuilder().build(evidence)
    assert {item.kind for item in hypotheses} >= {
        "hierarchy_parent", "hierarchy_leaf",
    }
    assert all(not item.facts for item in hypotheses)


def test_evidence_contains_no_amount_facts():
    table = TableRef("p4:0", 4, "收入", [["產品A", "123456"]])
    item = RegexTableEvidenceScanner().scan([table])[0]
    assert not hasattr(item, "facts")
    assert "123456" not in item.period_tokens
