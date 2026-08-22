# Hybrid Paper Scanner V3, S&P 500 Fixed

This version eliminates the Wikipedia runtime dependency. The workflow refreshes `universe/sp500_constituents.csv` from a non-Wikipedia CSV source, validates at least 400 symbols, gives S&P 500 labels priority, filters Nasdaq warrants/preferred/depositary/rights/units/ETFs/funds, and fails fast when the universe is incomplete.

Run **Actions > Hybrid Paper Scanner V3 SP500 Fixed > Run workflow**.

Research and paper tracking only. No live orders are placed.
