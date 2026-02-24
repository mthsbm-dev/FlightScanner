import argparse
from flightscanner.config import load_config
from flightscanner.search import run_search
from flightscanner.notifier import notify
from flightscanner.storage import load_sent, save_sent, get_match_id
import sys


def format_match(m: dict) -> str:
    price = m.get("price")
    city_to = m.get("cityTo")
    country_to = m.get("countryTo", {}).get("name")
    route = m.get("route", [])
    dep = route[0] if route else {}
    origin = dep.get("cityFrom")
    dur = m.get("duration", {}).get("total", 0)
    hours = dur / 3600
    # airlines involved
    airlines = sorted({seg.get("airline") for seg in route if seg.get("airline")})
    stops = max(0, len([seg for seg in route if seg.get("return") in (0, None)]) - 1)
    deep = m.get("deep_link") or m.get("booking_token") or ""
    line = f"{origin} -> {city_to}, {country_to}: {price} EUR, duration {hours:.1f}h, stops {stops}, airlines: {','.join(airlines)}"
    if deep:
        line += f"\nLink: {deep}"
    return line


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--test-telegram", action="store_true", help="Send a test Telegram message using configured token/chat_id and exit")
    parser.add_argument("--reset-sent", action="store_true", help="Clear persisted sent matches and exit")
    parser.add_argument("--origin-index", type=int, default=None, help="Only search from this origin index (0-based)")
    args = parser.parse_args()

    cfg = load_config()
    
    # If --origin-index is provided, only use that one origin
    if args.origin_index is not None:
        from flightscanner.airports import resolve_origins
        all_origins = resolve_origins(cfg.get("search", "origins", fallback=""))
        if args.origin_index >= len(all_origins):
            print(f"Error: origin_index {args.origin_index} is out of range (max: {len(all_origins)-1})")
            sys.exit(1)
        single_origin = all_origins[args.origin_index]
        cfg.set("search", "origins", single_origin)
        print(f"Running with only origin: {single_origin} (index {args.origin_index})")
    
    if args.test_telegram:
        subject = "FlightScanner: Test message"
        body = "This is a test message from FlightScanner. If you received this, Telegram config is correct."
        tg_ok, mail_ok = notify(cfg, subject, body)
        print("Telegram sent:", tg_ok, "Email sent:", mail_ok)
        return
    if args.reset_sent:
        # clear persisted sent matches
        save_sent(set())
        print("Persisted sent matches cleared.")
        return

    try:
        matches = run_search(cfg, once=args.once)
    except Exception as e:
        print("Search failed:", e)
        sys.exit(1)

    if not matches:
        print("No matches found.")
        return

    sent = load_sent()
    new = []
    for m in matches:
        mid = get_match_id(m)
        if mid in sent:
            continue
        new.append((mid, m))

    if not new:
        print("No new matches to notify.")
        return

    lines = []
    for mid, m in new:
        lines.append(format_match(m))

    body = "Found new matches:\n" + "\n".join(lines)
    subject = "FlightScanner: New Business-Class matches"

    tg_ok, mail_ok = notify(cfg, subject, body)
    print("Telegram sent:", tg_ok, "Email sent:", mail_ok)

    # persist sent ids
    for mid, _ in new:
        sent.add(mid)
    save_sent(sent)


if __name__ == "__main__":
    main()
