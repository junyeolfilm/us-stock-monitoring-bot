from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from us_market_bot.config import Settings
from us_market_bot.database import MarketDatabase, normalize_symbol
from us_market_bot.providers.alpaca import MarketDataError
from us_market_bot.report import split_report
from us_market_bot.service import BriefingService
from us_market_bot.calendar import session
from us_market_bot.journal import Journal
from us_market_bot.research import ResearchService


log = logging.getLogger("us-market-bot")
LAST_US_REPORT_DATE = "last_discord_report_date"
LAST_DOMESTIC_REPORT_DATE = "last_domestic_report_date"
LAST_DOMESTIC_ATTEMPT_DATE = "last_domestic_attempt_date"


class USMarketBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned, intents=discord.Intents.default()
        )
        self.settings = settings
        self.database = MarketDatabase(settings.database_path)
        self.service = BriefingService(settings, self.database)
        self.domestic_retry_after: datetime | None = None
        self.generation_lock = asyncio.Lock()
        self.us_retry_after: datetime | None = None

    async def setup_hook(self) -> None:
        self.database.initialize()
        self.journal = Journal(self.settings.database_path)
        await self.tree.sync()
        self.daily_briefing.start()
        self.domestic_briefing.start()

    async def on_ready(self) -> None:
        if (
            self.user
            and self.settings.discord_bot_name
            and self.user.name != self.settings.discord_bot_name
        ):
            try:
                await self.user.edit(username=self.settings.discord_bot_name)
            except discord.DiscordException as exc:
                log.warning("봇 표시 이름을 변경하지 못했습니다: %s", exc)
        if self.user:
            log.info("Discord 로그인 완료: %s", self.user)

    async def close(self) -> None:
        self.daily_briefing.cancel()
        self.domestic_briefing.cancel()
        await super().close()

    async def channel(self) -> discord.abc.Messageable:
        channel = self.get_channel(self.settings.discord_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.settings.discord_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("설정된 Discord 채널에 메시지를 보낼 수 없습니다.")
        return channel

    async def post_briefing(self) -> str:
        async with self.generation_lock:
            edition = await asyncio.to_thread(
                ResearchService(self.settings, self.database).morning
            )
            await self.send_edition(edition)
            self.database.set_state(LAST_US_REPORT_DATE, edition["trade_date"])
            return edition["body"]

    async def post_domestic_briefing(self, *, require_today: bool = False) -> str:
        async with self.generation_lock:
            edition = await asyncio.to_thread(
                ResearchService(self.settings, self.database).close
            )
            await self.send_edition(edition)
            self.database.set_state(LAST_DOMESTIC_REPORT_DATE, edition["trade_date"])
            return edition["body"]

    async def send_edition(self, edition):
        channel = await self.channel()
        for index, chunk in enumerate(split_report(edition["body"])):
            state = self.journal.delivery_state(edition["id"], index)
            if state == "sent":
                continue
            if not self.journal.claim(edition["id"], index):
                raise RuntimeError(
                    "발송 응답 미확정 기록이 있어 자동 재전송하지 않습니다. /상태에서 확인하세요."
                )
            # Persist claim BEFORE HTTP; crashes/timeouts never blindly resend.
            message = await channel.send(
                chunk,
                suppress_embeds=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.journal.delivered(edition["id"], index, message.id)

    @tasks.loop(minutes=1)
    async def daily_briefing(self) -> None:
        now = datetime.now(self.settings.timezone)
        target = session("XKRX", now.date().isoformat())
        if not target or now >= target.open:
            return
        if (now.hour, now.minute) < (
            self.settings.report_hour,
            self.settings.report_minute,
        ):
            return
        if self.us_retry_after and now < self.us_retry_after:
            return
        today = now.date().isoformat()
        if self.database.get_state(LAST_US_REPORT_DATE) == today:
            return
        try:
            await self.post_briefing()
        except (
            MarketDataError,
            discord.DiscordException,
            RuntimeError,
            ValueError,
        ) as exc:
            self.us_retry_after = now + timedelta(minutes=15)
            log.error("아침 브리핑 실패: %s", exc)

    @daily_briefing.before_loop
    async def before_daily_briefing(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=1)
    async def domestic_briefing(self) -> None:
        now = datetime.now(self.settings.timezone)
        target = session("XKRX", now.date().isoformat())
        if not target:
            return
        # Close-relative schedule also follows unusual exchange opening days.
        scheduled = target.close + timedelta(minutes=5)
        if now < scheduled or now >= scheduled + timedelta(hours=2):
            return
        today = now.date().isoformat()
        if self.database.get_state(LAST_DOMESTIC_REPORT_DATE) == today:
            return
        if self.database.get_state(LAST_DOMESTIC_ATTEMPT_DATE) == today:
            return
        if self.domestic_retry_after and now < self.domestic_retry_after:
            return
        try:
            await self.post_domestic_briefing(require_today=True)
            self.domestic_retry_after = None
        except ValueError as exc:
            self.database.set_state(LAST_DOMESTIC_ATTEMPT_DATE, today)
            log.info("국내장 브리핑 생략: %s", exc)
        except (MarketDataError, discord.DiscordException, RuntimeError) as exc:
            self.domestic_retry_after = now + timedelta(minutes=30)
            log.error("국내장 브리핑 실패(30분 뒤 재시도): %s", exc)

    @domestic_briefing.before_loop
    async def before_domestic_briefing(self) -> None:
        await self.wait_until_ready()


settings = Settings.from_env()
bot = USMarketBot(settings)


@bot.tree.command(name="상태", description="한미 주식 동향 봇의 상태를 확인합니다")
async def status(interaction: discord.Interaction) -> None:
    last_us = bot.database.get_state(LAST_US_REPORT_DATE) or "아직 없음"
    last_domestic = bot.database.get_state(LAST_DOMESTIC_REPORT_DATE) or "아직 없음"
    await interaction.response.send_message(
        f"정상 작동 중입니다.\n"
        f"아침 브리핑: 한국 거래일 {bot.settings.report_hour:02d}:{bot.settings.report_minute:02d}\n"
        f"국내장 마감: 정규장 종료 5분 후(통상 15:35, {bot.settings.timezone.key})\n"
        f"최근 미국장 발송: {last_us}\n최근 국내장 발송: {last_domestic}\n"
        f"발송 결과 미확정: {bot.journal.unresolved()}개(자동 재전송 보류)\n"
        "한국 거래일에만 발송 · 특수 개장일은 실제 정규장 종료 5분 후\n"
        "지속 시세 감시 없음 · 주문 API 연결 없음",
        ephemeral=True,
    )


@bot.tree.command(
    name="오늘브리핑", description="가장 최근 생성된 미국 시장 브리핑을 확인합니다"
)
async def latest_briefing(interaction: discord.Interaction) -> None:
    edition = bot.journal.latest("morning")
    body = edition["body"] if edition else bot.database.latest_report()
    if body is None:
        await interaction.response.send_message(
            "아직 생성된 브리핑이 없습니다.", ephemeral=True
        )
        return
    chunks = split_report(body)
    await interaction.response.send_message(
        chunks[0], ephemeral=True, suppress_embeds=True
    )
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=True, suppress_embeds=True)


@bot.tree.command(
    name="국내장브리핑", description="가장 최근 국내장 마감 브리핑을 확인합니다"
)
async def latest_domestic_briefing(interaction: discord.Interaction) -> None:
    edition = bot.journal.latest("close")
    body = edition["body"] if edition else bot.database.latest_domestic_report()
    if body is None:
        await interaction.response.send_message(
            "아직 생성된 국내장 브리핑이 없습니다.", ephemeral=True
        )
        return
    chunks = split_report(body)
    await interaction.response.send_message(
        chunks[0], ephemeral=True, suppress_embeds=True
    )
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=True, suppress_embeds=True)


@bot.tree.command(
    name="지금브리핑", description="미국 시장 브리핑을 지금 생성해 채널에 보냅니다"
)
@app_commands.default_permissions(manage_guild=True)
async def briefing_now(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await bot.post_briefing()
    except (MarketDataError, discord.DiscordException, RuntimeError, ValueError) as exc:
        await interaction.followup.send(
            f"브리핑을 만들지 못했습니다: {exc}", ephemeral=True
        )
        return
    await interaction.followup.send("브리핑을 채널에 전송했습니다.", ephemeral=True)


@bot.tree.command(
    name="지금국내장", description="최근 국내장 마감 브리핑을 생성해 채널에 보냅니다"
)
@app_commands.default_permissions(manage_guild=True)
async def domestic_briefing_now(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await bot.post_domestic_briefing(require_today=False)
    except (MarketDataError, discord.DiscordException, RuntimeError, ValueError) as exc:
        await interaction.followup.send(
            f"국내장 브리핑을 만들지 못했습니다: {exc}", ephemeral=True
        )
        return
    await interaction.followup.send(
        "최근 국내장 마감 브리핑을 채널에 전송했습니다.", ephemeral=True
    )


@bot.tree.command(name="관심종목", description="현재 고정 관심 종목을 확인합니다")
async def watchlist(interaction: discord.Interaction) -> None:
    symbols = bot.database.watchlist()
    await interaction.response.send_message(
        ", ".join(f"`{symbol}`" for symbol in symbols), ephemeral=True
    )


@bot.tree.command(name="종목추가", description="고정 관심 종목을 추가합니다")
@app_commands.default_permissions(manage_guild=True)
async def add_symbol(interaction: discord.Interaction, 종목코드: str) -> None:
    try:
        symbol = normalize_symbol(종목코드)
        added = bot.database.add_symbol(symbol)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    message = (
        f"`{symbol}`을 추가했습니다."
        if added
        else f"`{symbol}`은 이미 등록되어 있습니다."
    )
    await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="종목삭제", description="고정 관심 종목을 삭제합니다")
@app_commands.default_permissions(manage_guild=True)
async def remove_symbol(interaction: discord.Interaction, 종목코드: str) -> None:
    try:
        symbol = normalize_symbol(종목코드)
        removed = bot.database.remove_symbol(symbol)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    message = (
        f"`{symbol}`을 삭제했습니다."
        if removed
        else f"`{symbol}`은 등록되어 있지 않습니다."
    )
    await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(
    name="분석기준", description="화제 종목과 국내 영향 후보 선정 기준을 확인합니다"
)
async def criteria(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "최근 완료된 미국 정규장의 원시 SIP 5분봉을 집계합니다.\n"
        "관심종목과 주요 업종 관찰군 안에서 등락·거래량을 확인합니다.\n"
        "가격안은 25세션 이상 자료·관측 지지/저항·ATR·호가 검증을 통과할 때만 제공합니다.\n"
        "투자조건·보유·주문·비용 미확인 시 개인화 수량 없음.\n"
        "아침 원문·가설·가격안 보존, 마감에는 체결과 구분해 복기.\n"
        "지속 감시·자동매매 없음. /가격안무효로 기존 가격안의 상태를 기록할 수 있습니다.",
        ephemeral=True,
    )


@bot.tree.command(
    name="가격안무효",
    description="가격안을 무효 기록하고 기존 채널에 알립니다(주문 변경 없음)",
)
@app_commands.default_permissions(manage_guild=True)
async def invalidate_card(
    interaction: discord.Interaction, 가격안id: str, 이유: str
) -> None:
    await interaction.response.defer(ephemeral=True)
    editions = bot.journal.recent_mornings(datetime.now(bot.settings.timezone))
    parent = next(
        (
            e
            for e in editions
            if any(c["id"] == 가격안id for c in e["payload"]["cards"])
        ),
        None,
    )
    if not parent:
        await interaction.followup.send(
            "최근 원문에서 가격안 ID를 찾지 못했습니다.", ephemeral=True
        )
        return
    if bot.journal.subject_status(가격안id) == "무효화":
        await interaction.followup.send("이미 무효화된 가격안입니다.", ephemeral=True)
        return
    from us_market_bot.research_data import clean

    reason = clean(이유)
    bot.journal.event(parent["id"], 가격안id, "무효화", reason)
    now = datetime.now(bot.settings.timezone)
    notice = bot.journal.save(
        "invalidation",
        가격안id,
        now,
        f"가격안 {가격안id} 무효화 · 사용자 확인 사유: {reason}\n원문·이력 보존. 실제 주문은 변경하지 않았습니다.",
        {"parent": parent["id"]},
    )
    await bot.send_edition(notice)
    await interaction.followup.send(
        "무효 상태 기록 및 채널 알림 처리 완료.", ephemeral=True
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    missing = settings.missing(include_discord=True)
    if args.check_config:
        if missing:
            print("missing: " + ", ".join(missing))
            return 1
        print("configuration ok")
        return 0
    if missing:
        parser.error("필수 설정이 없습니다: " + ", ".join(missing))
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot.run(settings.discord_token, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
