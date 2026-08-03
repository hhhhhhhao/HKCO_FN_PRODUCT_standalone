from custom.service.HKCO_FN_PRODUCT_evidence import table_refs_from_sources
from custom.service.HKCO_FN_PRODUCT_pipeline import build_default_pipeline


def run(source):
    return build_default_pipeline().run(table_refs_from_sources([source]))


def test_row_identity_period_column_materializes_revenue():
    output, decision = run({
        "page_number": 3,
        "title": "產品收入明細",
        "target_table": [
            ["產品", "2025年收入", "2024年收入"],
            ["軟件", "120", "100"],
            ["服務", "30", "20"],
            ["合計", "150", "120"],
        ],
    })
    assert decision.selected.kind == "row_identity_period_column"
    assert {(row["product_name"], row["mbrevenue"]) for row in output} >= {
        ("軟件", 120.0), ("服務", 30.0), ("合计", 150.0),
    }


def test_column_identity_external_revenue_materializes_segments():
    output, decision = run({
        "page_number": 7,
        "title": "營運分部",
        "target_table": [
            ["截至2025年12月31日止年度", "", "", ""],
            ["航運業務", "碼頭業務", "抵銷", "總計"],
            ["向外部客户銷售", "210", "9", "-", "219"],
        ],
    })
    assert decision.selected.kind == "column_identity_metric_row"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("航運業務", 210.0), ("碼頭業務", 9.0), ("合计", 219.0),
    ]


def test_alternative_revenue_basis_is_rejected():
    output, decision = run({
        "page_number": 8,
        "title": "經調整產品收入",
        "target_table": [["產品", "2025年收入"], ["A", "10"], ["B", "20"]],
    })
    assert output == []
    assert decision.selected is None


def test_cost_and_gross_profit_merge_only_from_explicit_metric_rows():
    output, _decision = run({
        "page_number": 9,
        "title": "Segment revenue, cost of sales and gross profit",
        "target_table": [
            ["Year ended 31 December 2025", "", ""],
            ["Product A", "Product B", "Total"],
            ["External revenue", "10", "20", "30"],
            ["Cost of sales", "6", "12", "18"],
            ["Gross profit", "4", "8", "12"],
        ],
    })
    by_name = {row["product_name"]: row for row in output}
    assert by_name["Product A"]["mbcost"] == 6.0
    assert by_name["Product A"]["gross_profit"] == 4.0
    assert by_name["Product B"]["mbcost"] == 12.0
    assert by_name["Product B"]["gross_profit"] == 8.0
