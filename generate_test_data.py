#!/usr/bin/env python3
"""Generate realistic AAPL daily OHLCV test data using a random walk with upward drift."""

import random
import datetime

random.seed(42)  # Reproducible data

def is_trading_day(d):
    """Return True if d is a weekday (Mon-Fri)."""
    return d.weekday() < 5

def generate_trading_days(start_date, end_date):
    """Generate all weekday dates between start and end (inclusive)."""
    days = []
    current = start_date
    while current <= end_date:
        if is_trading_day(current):
            days.append(current)
        current += datetime.timedelta(days=1)
    return days

def main():
    start_date = datetime.date(2020, 1, 2)
    end_date = datetime.date(2023, 12, 29)

    trading_days = generate_trading_days(start_date, end_date)

    prev_close = 75.00  # AAPL approximate price early 2020 (pre-split adjusted)

    lines = ["Date,Open,High,Low,Close,Volume"]

    for day in trading_days:
        # Open = previous close * (1 + small random factor between -0.01 and 0.01)
        open_factor = 1.0 + random.uniform(-0.01, 0.01)
        open_price = prev_close * open_factor

        # Close = open * (1 + random factor between -0.02 and 0.02)
        close_factor = 1.0 + random.uniform(-0.02, 0.02)
        close_price = open_price * close_factor

        # High = max(open, close) * (1 + abs(random 0 to 0.01))
        high_factor = 1.0 + abs(random.uniform(0, 0.01))
        high_price = max(open_price, close_price) * high_factor

        # Low = min(open, close) * (1 - abs(random 0 to 0.01))
        low_factor = 1.0 - abs(random.uniform(0, 0.01))
        low_price = min(open_price, close_price) * low_factor

        # Volume = random between 50,000,000 and 150,000,000
        volume = random.randint(50000000, 150000000)

        # Round prices to 2 decimal places
        open_price = round(open_price, 2)
        high_price = round(high_price, 2)
        low_price = round(low_price, 2)
        close_price = round(close_price, 2)

        lines.append(f"{day.strftime('%Y-%m-%d')},{open_price},{high_price},{low_price},{close_price},{volume}")

        prev_close = close_price

    output_path = "/mnt/c/IdeaProjects/BackTesting/test-data/AAPL_daily.csv"
    with open(output_path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Generated {len(trading_days)} trading days of data")
    print(f"Date range: {trading_days[0]} to {trading_days[-1]}")
    print(f"Starting price: 75.00")
    print(f"Ending close: {lines[-1].split(',')[4]}")
    print(f"Output: {output_path}")

if __name__ == "__main__":
    main()
