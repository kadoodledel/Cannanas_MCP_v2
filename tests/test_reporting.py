from __future__ import annotations

import unittest

from cannanas_mcp.reporting import (
    build_category_breakdown,
    build_dispensed_amounts,
    build_revenue_summary,
    build_strain_performance,
    build_weekly_metrics,
)


class ReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.carts = [
            {
                "id": "cart-1",
                "status": "fulfilled",
                "items": [
                    {
                        "quantity": 5,
                        "unit": "grams",
                        "product_id": "product-1",
                        "strain_name": "Alpha",
                        "category": "flower",
                    }
                ],
            }
        ]
        self.charges = [
            {"id": "charge-1", "status": "paid", "payment_method": "CARD", "total": 45.5},
            {"id": "charge-2", "status": "pending", "payment_method": "CASH", "total": 10},
        ]
        self.products = [{"id": "product-1", "type": "flower", "strain_name": "Alpha", "strain_id": "strain-1"}]
        self.strains = [{"id": "strain-1", "name": "Alpha"}]

    def test_revenue_summary_totals(self) -> None:
        summary = build_revenue_summary(self.charges, period_start="2026-05-01", period_end="2026-05-07")
        self.assertEqual(summary["totals"]["charge_count"], 2)
        self.assertEqual(summary["totals"]["amount_total"], 55.5)

    def test_dispensed_amounts_totals(self) -> None:
        summary = build_dispensed_amounts(self.carts, period_start="2026-05-01", period_end="2026-05-07")
        self.assertEqual(summary["totals"]["total_by_unit"]["grams"], 5.0)

    def test_strain_performance_groups_by_strain(self) -> None:
        summary = build_strain_performance(
            self.carts,
            self.products,
            self.strains,
            period_start="2026-05-01",
            period_end="2026-05-07",
        )
        self.assertEqual(summary["breakdowns"]["by_strain"][0]["key"], "Alpha")

    def test_category_breakdown_groups_items(self) -> None:
        summary = build_category_breakdown(
            self.carts,
            self.products,
            period_start="2026-05-01",
            period_end="2026-05-07",
        )
        self.assertEqual(summary["breakdowns"]["by_category"][0]["key"], "flower")

    def test_weekly_metrics_combines_sections(self) -> None:
        revenue_summary = build_revenue_summary(self.charges, period_start="2026-05-01", period_end="2026-05-07")
        dispensed_amounts = build_dispensed_amounts(self.carts, period_start="2026-05-01", period_end="2026-05-07")
        strain_performance = build_strain_performance(
            self.carts,
            self.products,
            self.strains,
            period_start="2026-05-01",
            period_end="2026-05-07",
        )
        category_breakdown = build_category_breakdown(
            self.carts,
            self.products,
            period_start="2026-05-01",
            period_end="2026-05-07",
        )
        summary = build_weekly_metrics(
            period_start="2026-05-01",
            period_end="2026-05-07",
            revenue_summary=revenue_summary,
            dispensed_amounts=dispensed_amounts,
            strain_performance=strain_performance,
            category_breakdown=category_breakdown,
            member_statistics={"active": 10, "pending": 2},
            member_journals=[{"id": "journal-1"}],
        )
        self.assertEqual(summary["totals"]["charge_count"], 2)
        self.assertEqual(summary["totals"]["journal_entry_count"], 1)
