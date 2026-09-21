"""Explicit project-scoped investment settings. Missing is not zero/none."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path


@dataclass
class InvestmentProfile:
    markets: list[str] = field(default_factory=list)
    broker: str | None = None
    currency: str | None = None
    available_cash: float | None = None
    per_symbol_limit: float | None = None
    loss_budget: float | None = None
    sector_limit: float | None = None
    horizon: str | None = None
    holdings: list[dict] | None = None
    open_orders: list[dict] | None = None
    account_asof: str | None = None
    costs: dict | None = None
    broker_rules: dict | None = None

    def missing(self) -> list[str]:
        return [
            name
            for name in (
                "markets",
                "broker",
                "currency",
                "available_cash",
                "per_symbol_limit",
                "loss_budget",
                "sector_limit",
                "horizon",
                "holdings",
                "open_orders",
                "account_asof",
                "costs",
                "broker_rules",
            )
            if getattr(self, name) is None or (name == "markets" and not self.markets)
        ]

    def can_size(self, now: datetime, currency: str) -> bool:
        if self.missing() or self.currency != currency:
            return False
        try:
            asof = datetime.fromisoformat(self.account_asof)
            rules_asof = datetime.fromisoformat(
                self.broker_rules.get("verified_at", "")
            )
            rules_ok = (
                self.broker_rules.get("official_url", "").startswith("https://")
                and self.broker_rules.get("broker") == self.broker
                and self.broker_rules.get("cash_equity_only") is True
                and rules_asof.tzinfo is not None
                and timedelta(0) <= now - rules_asof <= timedelta(days=30)
            )
            return bool(
                rules_ok
                and asof.tzinfo is not None
                and timedelta(0) <= now - asof <= timedelta(hours=24)
                and all(
                    float(v) > 0
                    for v in (
                        self.available_cash,
                        self.per_symbol_limit,
                        self.loss_budget,
                        self.sector_limit,
                    )
                )
                and all(
                    key in self.costs
                    for key in (
                        "buy_rate",
                        "sell_rate",
                        "slippage_per_share",
                        "fx_cost_per_share",
                    )
                )
            )
        except (ValueError, TypeError):
            return False


def load_profile(path: str = "investment_profile.json") -> InvestmentProfile:
    if not Path(path).exists():
        return InvestmentProfile()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return InvestmentProfile(
        **{
            key: value
            for key, value in payload.items()
            if key in InvestmentProfile.__dataclass_fields__
        }
    )
