"""Generate current evidence samples, without posting or inventing historical forecasts."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from us_market_bot.env import load_env
from us_market_bot.config import Settings
from us_market_bot.database import MarketDatabase
from us_market_bot.research import ResearchService
from us_market_bot.research_data import KST


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("samples"))
    args = parser.parse_args()
    load_env()
    settings = Settings.from_env()
    args.output.mkdir(parents=True, exist_ok=True)
    database = MarketDatabase(args.output / "sample_journal.db")
    now = datetime.now(KST)
    for kind in ("morning", "close"):
        research = ResearchService(settings, database)
        edition = getattr(research, kind)(now, preview=True)
        (args.output / f"{kind}.md").write_text(edition["body"], encoding="utf-8")
        (args.output / f"{kind}.evidence.json").write_text(
            json.dumps(edition, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"{kind}: {edition['trade_date']}, {len(edition['body'])} chars")


if __name__ == "__main__":
    main()
