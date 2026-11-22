"""24/7 Cloud Worker entrypoint: APScheduler (24 posts/day) + HTTP health/status server."""

import argparse
import asyncio
import logging
import sys
from zoneinfo import ZoneInfo

from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import CHANNELS, Settings
from src.database import FactoryDatabase
from src.pipeline import ShortsPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("science-shorts-cloud")


async def start_worker() -> None:
    settings = Settings()
    db = FactoryDatabase(settings.data_dir / "factory.db")
    pipeline = ShortsPipeline(settings=settings, db=db)

    tz = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)

    for channel_id, channel in CHANNELS.items():
        hours_expr = ",".join(str(h) for h in channel.schedule_hours)
        trigger = CronTrigger(hour=hours_expr, minute=channel.schedule_minute, timezone=tz)
        scheduler.add_job(
            pipeline.run_channel_job,
            trigger=trigger,
            args=[channel_id],
            id=f"job_{channel_id}",
            name=f"{channel.display_name} Shorts (6/day)",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
        )
        logger.info(
            "Scheduled [%s] at hours (%s):%02d (%s)",
            channel.display_name,
            hours_expr,
            channel.schedule_minute,
            settings.timezone,
        )

    scheduler.start()

    # Lightweight HTTP server for Railway/Render health checks & monitoring
    app = web.Application()

    async def handle_health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "dry_run": settings.dry_run})

    async def handle_status(_request: web.Request) -> web.Response:
        jobs = [
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in scheduler.get_jobs()
        ]
        summary = db.get_status_summary()
        return web.json_response({"scheduled_jobs": jobs, **summary})

    async def handle_trigger(request: web.Request) -> web.Response:
        channel_id = request.match_info["channel_id"]
        if channel_id not in CHANNELS:
            return web.json_response({"error": f"Unknown channel: {channel_id}"}, status=404)
        asyncio.create_task(pipeline.run_channel_job(channel_id, force=True))
        return web.json_response({"queued": channel_id})

    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/trigger/{channel_id}", handle_trigger)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    logger.info("Cloud worker listening on 0.0.0.0:%d (timezone=%s)", settings.port, settings.timezone)

    # Keep running forever
    await asyncio.Event().wait()


async def run_once(target: str, force: bool = False) -> None:
    """Run one Shorts cycle for a single channel or 'all' channels, then exit (for GitHub Actions / Colab)."""
    settings = Settings()
    db = FactoryDatabase(settings.data_dir / "factory.db")
    pipeline = ShortsPipeline(settings=settings, db=db)

    targets = list(CHANNELS.keys()) if target == "all" else [target]
    for channel_id in targets:
        if channel_id not in CHANNELS:
            raise ValueError(f"Unknown channel '{channel_id}'. Choose from: {', '.join(CHANNELS)} or 'all'")
        await pipeline.run_channel_job(channel_id, force=force, raise_on_error=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Science Shorts Cloud Factory")
    parser.add_argument(
        "--once",
        type=str,
        default="",
        help="Run once for a specific channel (physics, chemistry, biology, general_science) or 'all', then exit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the 3.5h spacing / 6-per-24h quota guard (for manual workflow_dispatch runs).",
    )
    args = parser.parse_args()

    if args.once:
        asyncio.run(run_once(args.once, force=args.force))
    else:
        asyncio.run(start_worker())
