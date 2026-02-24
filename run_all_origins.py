#!/usr/bin/env python3
"""
Wrapper script to run FlightScanner for all origins sequentially with progress reporting.
Usage: python run_all_origins.py [--max-origins N] [--reset]
"""
import subprocess
import sys
import time
import configparser
from flightscanner.airports import resolve_origins

def load_config():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    return cfg

def get_origins(cfg):
    raw_origins = cfg.get("search", "origins", fallback="")
    return resolve_origins(raw_origins)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run FlightScanner for all origins")
    parser.add_argument("--max-origins", type=int, default=None, help="Limit number of origins")
    parser.add_argument("--reset", action="store_true", help="Reset sent matches before running")
    args = parser.parse_args()
    
    cfg = load_config()
    origins = get_origins(cfg)
    
    if args.max_origins:
        origins = origins[:args.max_origins]
    
    print(f"=== FlightScanner: Running for {len(origins)} origins ===", flush=True)
    
    if args.reset:
        print("Resetting sent matches...", flush=True)
        subprocess.run([sys.executable, "run.py", "--reset-sent"], check=True)
    
    total_flights = 0
    start_time = time.time()
    
    for i, origin in enumerate(origins):
        print(f"\n[{i+1}/{len(origins)}] Running for {origin}...", flush=True)
        result = subprocess.run(
            [sys.executable, "run.py", "--once", "--origin-index", str(i)],
            capture_output=True,
            text=True
        )
        
        # Parse output for flight count
        output = result.stdout + result.stderr
        if "Found" in output and "flights" in output:
            # Try to extract flight count
            for line in output.split("\n"):
                if "flights so far" in line.lower():
                    print(f"  {line.strip()}", flush=True)
                elif "Telegram sent:" in line:
                    print(f"  {line.strip()}", flush=True)
        
        elapsed = time.time() - start_time
        print(f"  Elapsed: {elapsed/60:.1f} min", flush=True)
    
    total_time = time.time() - start_time
    print(f"\n=== Complete! Total time: {total_time/60:.1f} min ===", flush=True)

if __name__ == "__main__":
    main()