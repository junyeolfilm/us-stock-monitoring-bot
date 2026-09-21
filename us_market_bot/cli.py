from __future__ import annotations

import argparse

from us_market_bot.config import Settings
from us_market_bot.database import MarketDatabase
from us_market_bot.service import BriefingService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    missing = settings.missing(include_discord=False)
    if args.check_config:
        if missing:
            print("missing: " + ", ".join(missing))
            return 1
        print("market data configuration ok")
        return 0
    if missing:
        parser.error("필수 설정이 없습니다: " + ", ".join(missing))
    if not args.once:
        parser.error("--once를 지정하세요.")
    database = MarketDatabase(settings.database_path)
    database.initialize()
    print(BriefingService(settings, database).generate())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
