from __future__ import annotations

import argparse
import time
from datetime import datetime

from .app import run_demo
from .config import AppConfig
from .runtime import TradingRuntime
from .telegram_command_bot import TelegramCommandBot


def main() -> None:
    parser = argparse.ArgumentParser(description="AiTrader entrypoint")
    parser.add_argument("command", nargs="?", default="demo", choices=["demo", "cycle", "scan", "tg-once", "tg-loop", "serve"])
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--tf", default="auto", choices=["15m", "1h", "hybrid", "1h_primary", "auto"])
    parser.add_argument("--budget", type=float, default=0.0, help="Total USDT budget for split sizing in advisory output.")
    parser.add_argument("--poll-timeout", type=int, default=25, help="Telegram long-poll timeout seconds.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Optional pause between poll cycles.")
    parser.add_argument("--scan-seconds", type=int, default=0, help="Auto-scan interval seconds for serve mode. 0 means use config.")
    args = parser.parse_args()

    if args.command == "demo":
        run_demo(config_path=args.config)
        return

    cfg = AppConfig.load(args.config)
    runtime = TradingRuntime.from_config(cfg)
    if args.command == "cycle":
        result = runtime.run_cycle()
        print(
            f"processed={result.processed_symbols} signals={result.signals} "
            f"approved={result.approved} rejected={result.rejected} "
            f"advisories_generated={result.advisories_generated} advisories_sent={result.advisories_sent}"
        )
        return

    if args.command == "scan":
        symbols = [s.strip().upper() for s in args.symbols.replace(" ", ",").split(",") if s.strip()]
        targets = symbols if symbols else cfg.trading.symbols
        analyses = runtime.analyze_symbols(
            targets,
            push_to_telegram=False,
            timeframe_mode=args.tf,
            manual_total_usdt=args.budget if args.budget > 0 else None,
        )
        for idx, analysis in enumerate(analyses):
            if idx > 0:
                print("\n" + ("-" * 48))
            print(analysis.message)
        return

    if args.command == "tg-once":
        bot = TelegramCommandBot.from_runtime(runtime)
        status = bot.run_once(timeout_seconds=max(1, args.poll_timeout))
        print(f"telegram_poll={status}")
        return

    if args.command == "tg-loop":
        bot = TelegramCommandBot.from_runtime(runtime)
        timeout_seconds = max(1, args.poll_timeout)
        sleep_seconds = max(0.0, args.sleep_seconds)
        print(f"telegram_loop=started timeout={timeout_seconds}s sleep={sleep_seconds:.1f}s")
        try:
            while True:
                status = bot.run_once(timeout_seconds=timeout_seconds)
                now = datetime.now().isoformat(timespec="seconds")
                print(f"[{now}] telegram_poll={status}")
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            print("telegram_loop=stopped")
        return

    if args.command == "serve":
        bot = TelegramCommandBot.from_runtime(runtime)
        timeout_seconds = max(1, args.poll_timeout)
        sleep_seconds = max(0.0, args.sleep_seconds)
        scan_seconds = max(5, args.scan_seconds if args.scan_seconds > 0 else cfg.runtime.loop_interval_seconds)
        print(
            f"serve=started scan={scan_seconds}s poll_timeout={timeout_seconds}s "
            f"cooldown={cfg.runtime.advisory_cooldown_minutes}m"
        )
        next_scan = time.monotonic()
        try:
            while True:
                now_mono = time.monotonic()
                if now_mono >= next_scan:
                    result = runtime.run_cycle()
                    now = datetime.now().isoformat(timespec="seconds")
                    print(
                        f"[{now}] scan processed={result.processed_symbols} signals={result.signals} "
                        f"approved={result.approved} rejected={result.rejected} sent={result.advisories_sent}"
                    )
                    next_scan = time.monotonic() + float(scan_seconds)

                remain = max(0.0, next_scan - time.monotonic())
                dynamic_timeout = max(1, min(timeout_seconds, int(remain) if remain >= 1 else 1))
                poll_status = bot.run_once(timeout_seconds=dynamic_timeout)
                if poll_status != "no_updates":
                    now = datetime.now().isoformat(timespec="seconds")
                    print(f"[{now}] telegram_poll={poll_status}")
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            print("serve=stopped")
        return


if __name__ == "__main__":
    main()
