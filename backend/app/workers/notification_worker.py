"""Standalone Background Worker for Ledgerly Notifications."""

import asyncio
import logging
import signal
import sys
import os

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.notification_service import NotificationService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("notification_worker")


async def run_worker_loop(poll_interval: float = 3.0):
    """Continuous polling loop for pending outbox notification jobs."""
    logger.info("Starting Ledgerly Notification Worker Daemon...")
    stop_event = asyncio.Event()

    def handle_stop():
        logger.info("Stop signal received. Gracefully shutting down worker...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_stop)
        except NotImplementedError:
            # Signal handling on Windows
            pass

    while not stop_event.is_set():
        try:
            batch = await NotificationService.process_pending_jobs(batch_size=10)
            if batch.claimed > 0:
                logger.info(
                    f"Processed batch: {batch.claimed} claimed, {batch.succeeded} succeeded, {batch.failed} failed."
                )
                for detail in batch.details:
                    logger.info(f" - {detail}")
            else:
                # Idle backoff
                await asyncio.sleep(poll_interval)
        except Exception as ex:
            logger.error(f"Worker exception in polling loop: {ex}")
            await asyncio.sleep(poll_interval)

    logger.info("Notification Worker shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")
