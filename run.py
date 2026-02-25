import argparse
from flightscanner.config import load_config
from flightscanner.search import run_search
from flightscanner.notifier import notify
from flightscanner.storage import load_sent, save_sent, get_match_id, init_storage, save_flight_results
import sys
from datetime import datetime
from pathlib import Path


def format_match(m: dict, verbose: bool = True) -> str:
    """Format a flight match with all details."""
    price = m.get("price")
    city_to = m.get("cityTo")
    country_to = m.get("countryTo", {}).get("name", "")
    route = m.get("route", [])
    
    # Get origin - try different field names (Amadeus uses cityFrom, Tequila might use cityFrom from segment)
    origin = m.get("cityFrom", "?")
    
    # Get airport codes
    dep_airport = m.get("cityFrom", "?")
    if not dep_airport or dep_airport == "?":
        if route:
            dep_airport = route[0].get("cityFrom", route[0].get("flyFrom", "?"))
    arr_airport = m.get("cityTo", "?")
    if not arr_airport or arr_airport == "?":
        if route:
            arr_airport = route[-1].get("cityTo", route[-1].get("flyTo", "?"))
    
    # Get departure date
    dep_date = m.get("dep_date", "")
    if not dep_date and route:
        # Try to get from first segment
        first_seg = route[0]
        dep_date = first_seg.get("dTime")
        if dep_date:
            dep_date = datetime.fromtimestamp(dep_date).strftime("%Y-%m-%d")
    if not dep_date:
        dep_date = "?"
    elif isinstance(dep_date, str) and dep_date:
        # Format ISO date
        try:
            dt = datetime.fromisoformat(dep_date.replace("Z", "+00:00"))
            dep_date = dt.strftime("%Y-%m-%d")
        except:
            pass
    
    # Get airlines and flight numbers
    airlines_info = []
    for seg in route:
        airline = seg.get("airline", "")
        flight_num = seg.get("flightNo", seg.get("flight", ""))
        if airline and flight_num:
            airlines_info.append(f"{airline}{flight_num}")
        elif airline:
            airlines_info.append(airline)
    airlines_str = ", ".join(sorted(set(airlines_info))) if airlines_info else "?"
    
    # Duration
    dur = m.get("duration", {}).get("total", 0)
    hours = dur / 3600
    
    # Stops
    outbound = [seg for seg in route if seg.get("return") in (0, None)]
    stops = max(0, len(outbound) - 1) if outbound else 0
    
    # Build output
    if verbose:
        lines = [
            f"✈️ {origin} ({dep_airport}) → {city_to} ({arr_airport})",
            f"   Datum: {dep_date}",
            f"   Preis: {price} EUR",
            f"   Airline: {airlines_str}",
            f"   Dauer: {hours:.1f}h, Stopps: {stops}",
        ]
        deep = m.get("deep_link") or m.get("booking_token") or ""
        if deep:
            lines.append(f"   Buchung: {deep[:80]}...")
        return "\n".join(lines)
    else:
        return f"{origin} -> {city_to}: {price} EUR, {airlines_str}"


def log_results(matches: list, log_file: Path):
    """Log results to a file with full details."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(log_file, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"FlightScanner Results - {timestamp}\n")
        f.write(f"Total matches: {len(matches)}\n")
        f.write(f"{'='*60}\n\n")
        
        for i, m in enumerate(matches, 1):
            f.write(f"[{i}] " + format_match(m) + "\n\n")
    
    print(f"Results logged to {log_file}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--test-telegram", action="store_true", help="Send a test Telegram message using configured token/chat_id and exit")
    parser.add_argument("--reset-sent", action="store_true", help="Clear persisted sent matches and exit")
    parser.add_argument("--origin-index", type=int, default=None, help="Only search from this origin index (0-based)")
    args = parser.parse_args()

    cfg = load_config()
    init_storage(cfg)

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

    # Persist results via the active storage backend
    save_flight_results(matches)

    # Log all matches to file
    log_file = Path("output/flights.log")
    log_results(matches, log_file)
    
    # Print verbose output to stdout
    print("\n=== Flight Results ===", flush=True)
    for i, m in enumerate(matches, 1):
        print(f"\n[{i}] " + format_match(m), flush=True)
    print(f"\n=== Total: {len(matches)} flights ===\n", flush=True)

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
