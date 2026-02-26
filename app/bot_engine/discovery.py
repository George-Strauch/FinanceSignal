"""Bot discovery — scans bots/ directory and registers strategies."""

import importlib
import importlib.util
import inspect
import logging
import sys
from pathlib import Path

from app.bot_engine.base_bot import BaseTradingBot
from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

BOTS_DIR = PROJECT_ROOT / "bots"


def discover_bots() -> dict[str, BaseTradingBot]:
    """
    Scan bots/ directory for bot implementations.

    Each subfolder should contain a bot.py with a class extending BaseTradingBot.
    Returns {bot_id: instance} where bot_id is the folder name.
    """
    bots = {}

    if not BOTS_DIR.is_dir():
        logger.info("No bots/ directory found, skipping bot discovery")
        return bots

    for bot_dir in sorted(BOTS_DIR.iterdir()):
        if not bot_dir.is_dir() or bot_dir.name.startswith(("_", ".")):
            continue

        bot_file = bot_dir / "bot.py"
        if not bot_file.exists():
            continue

        bot_id = bot_dir.name
        try:
            # Dynamic import
            module_name = f"bots.{bot_id}.bot"
            spec = importlib.util.spec_from_file_location(module_name, bot_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Find BaseTradingBot subclass
            bot_class = None
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BaseTradingBot) and obj is not BaseTradingBot:
                    bot_class = obj
                    break

            if bot_class is None:
                logger.warning("No BaseTradingBot subclass found in %s", bot_file)
                continue

            instance = bot_class()
            bots[bot_id] = instance
            logger.info("Discovered bot: %s (%s)", bot_id, instance.name)

        except Exception as exc:
            logger.error("Failed to load bot %s: %s", bot_id, exc)

    logger.info("Discovered %d bot(s)", len(bots))
    return bots


def ensure_bot_strategies(bots: dict[str, BaseTradingBot]):
    """
    For each discovered bot, create or update a strategy in the database.

    Strategy title is prefixed with [Bot] for identification.
    """
    from sentinel.db import RedditDatabase

    with RedditDatabase() as db:
        for bot_id, bot in bots.items():
            existing = db.get_strategy_by_bot_id(bot_id)
            if existing:
                # Update title/description/color if changed
                title = f"[Bot] {bot.name}"
                if (existing["title"] != title or
                        existing["description"] != bot.description or
                        existing["color"] != bot.color):
                    db.update_strategy(
                        existing["id"],
                        title=title,
                        description=bot.description,
                        color=bot.color,
                    )
                    logger.info("Updated strategy for bot %s", bot_id)
            else:
                db.create_bot_strategy(
                    bot_id=bot_id,
                    title=f"[Bot] {bot.name}",
                    description=bot.description,
                    color=bot.color,
                )
                logger.info("Created strategy for bot %s", bot_id)
