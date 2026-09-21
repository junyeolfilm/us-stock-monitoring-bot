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


log = logging.getLogger("us-market-bot")
LAST_US_REPORT_DATE = "last_discord_report_date"
LAST_DOMESTIC_REPORT_DATE = "last_domestic_report_date"
LAST_DOMESTIC_ATTEMPT_DATE = "last_domestic_attempt_date"


class USMarketBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.settings = settings
        self.database = MarketDatabase(settings.database_path)
        self.service = BriefingService(settings, self.database)
        self.domestic_retry_after: datetime | None = None

    async def setup_hook(self) -> None:
        self.database.initialize()
        await self.tree.sync()
        self.daily_briefing.start()
        self.domestic_briefing.start()

    async def on_ready(self) -> None:
        if self.user and self.settings.discord_bot_name and self.user.name != self.settings.discord_bot_name:
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
        body = await asyncio.to_thread(self.service.generate)
        channel = await self.channel()
        for chunk in split_report(body):
            await channel.send(chunk, suppress_embeds=True)
        today = datetime.now(self.settings.timezone).date().isoformat()
        self.database.set_state(LAST_US_REPORT_DATE, today)
        return body

    async def post_domestic_briefing(self, *, require_today: bool = False) -> str:
        market_date, body = await asyncio.to_thread(
            self.service.generate_domestic, require_today=require_today
        )
        channel = await self.channel()
        for chunk in split_report(body):
            await channel.send(chunk, suppress_embeds=True)
        self.database.set_state(LAST_DOMESTIC_REPORT_DATE, market_date)
        return body

    @tasks.loop(minutes=1)
    async def daily_briefing(self) -> None:
        now = datetime.now(self.settings.timezone)
        if now.hour != self.settings.report_hour or now.minute < self.settings.report_minute:
            return
        today = now.date().isoformat()
        if self.database.get_state(LAST_US_REPORT_DATE) == today:
            return
        try:
            await self.post_briefing()
        except (MarketDataError, discord.DiscordException, RuntimeError, ValueError) as exc:
            log.error("아침 브리핑 실패: %s", exc)

    @daily_briefing.before_loop
    async def before_daily_briefing(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=1)
    async def domestic_briefing(self) -> None:
        now = datetime.now(self.settings.timezone)
        if now.weekday() >= 5:
            return
        scheduled = (self.settings.domestic_report_hour, self.settings.domestic_report_minute)
        if (now.hour, now.minute) < scheduled:
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
        f"미국장 브리핑: 매일 {bot.settings.report_hour:02d}:{bot.settings.report_minute:02d}\n"
        f"국내장 마감 브리핑: 평일 {bot.settings.domestic_report_hour:02d}:"
        f"{bot.settings.domestic_report_minute:02d} ({bot.settings.timezone.key})\n"
        f"최근 미국장 발송: {last_us}\n최근 국내장 발송: {last_domestic}",
        ephemeral=True,
    )


@bot.tree.command(name="오늘브리핑", description="가장 최근 생성된 미국 시장 브리핑을 확인합니다")
async def latest_briefing(interaction: discord.Interaction) -> None:
    body = bot.database.latest_report()
    if body is None:
        await interaction.response.send_message("아직 생성된 브리핑이 없습니다.", ephemeral=True)
        return
    chunks = split_report(body)
    await interaction.response.send_message(chunks[0], ephemeral=True, suppress_embeds=True)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=True, suppress_embeds=True)


@bot.tree.command(name="국내장브리핑", description="가장 최근 국내장 마감 브리핑을 확인합니다")
async def latest_domestic_briefing(interaction: discord.Interaction) -> None:
    body = bot.database.latest_domestic_report()
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


@bot.tree.command(name="지금브리핑", description="미국 시장 브리핑을 지금 생성해 채널에 보냅니다")
@app_commands.default_permissions(manage_guild=True)
async def briefing_now(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await bot.post_briefing()
    except (MarketDataError, discord.DiscordException, RuntimeError, ValueError) as exc:
        await interaction.followup.send(f"브리핑을 만들지 못했습니다: {exc}", ephemeral=True)
        return
    await interaction.followup.send("브리핑을 채널에 전송했습니다.", ephemeral=True)


@bot.tree.command(name="지금국내장", description="최근 국내장 마감 브리핑을 생성해 채널에 보냅니다")
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
    message = f"`{symbol}`을 추가했습니다." if added else f"`{symbol}`은 이미 등록되어 있습니다."
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
    message = f"`{symbol}`을 삭제했습니다." if removed else f"`{symbol}`은 등록되어 있지 않습니다."
    await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="분석기준", description="화제 종목과 국내 영향 후보 선정 기준을 확인합니다")
async def criteria(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "화제 종목은 등락률·거래량 순위·뉴스 수·관심 종목 여부를 합산해 선정합니다.\n"
        "국내 영향 후보는 미국 업종 신호와 뉴스 키워드가 사전 검토된 연결표에 일치할 때만 표시합니다.\n"
        "국내장 예상 시나리오는 미국 주요 ETF의 가중 신호와 전일 국내장 지수·거래대금 상위를 함께 참고합니다.\n"
        "결과는 매수·매도 추천이나 확정적인 가격 예측이 아닙니다.",
        ephemeral=True,
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
