"""CLI WorkBot."""

from __future__ import annotations

import argparse
import logging

from workbot.config import WorkBotConfig
from workbot.excel import export_reports
from workbot.runner import identify_sender, run_polling
from workbot.storage import WorkBotStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkBot для российского мессенджера MAX")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "identify", "check", "export", "backfill"),
        default="run",
    )
    parser.add_argument("chat_id", nargs="?", type=int)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "identify":
        identify_sender(WorkBotConfig.from_environment(require_owner=False))
        return
    if args.command == "export":
        config = WorkBotConfig.from_environment(require_token=False, require_owner=False)
        storage = WorkBotStorage(config.database_path)
        storage.initialize()
        path = export_reports(storage.reports(), config.export_dir)
        print(path)
        return
    if args.command == "backfill":
        if args.chat_id is None:
            parser.error("для backfill требуется числовой chat_id")
        from workbot.backfill import import_chat_history
        from workbot.max_client import MaxClient
        from workbot.service import WorkBotService

        config = WorkBotConfig.from_environment()
        storage = WorkBotStorage(config.database_path)
        storage.initialize()
        client = MaxClient(config.token, config.api_base, timeout=config.poll_timeout + 10)
        result = import_chat_history(
            client,
            storage,
            WorkBotService(config, storage, client),
            args.chat_id,
        )
        print(
            f"Чат: {result.title or result.chat_id}; получено: {result.fetched_messages}; "
            f"новых сообщений: {result.new_messages}; новых отчётов: {result.new_reports}"
        )
        return
    if args.command == "check":
        from workbot.max_client import MaxClient

        config = WorkBotConfig.from_environment(require_owner=False)
        bot = MaxClient(config.token, config.api_base).get_me()
        print(f"Подключение успешно: {bot.get('first_name', 'WorkBot')} (@{bot.get('username', '')})")
        return
    config = WorkBotConfig.from_environment()
    run_polling(config)


if __name__ == "__main__":
    main()
