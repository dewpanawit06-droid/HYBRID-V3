#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
import requests

SOURCES = [
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
]
BAD_KEYWORDS = (
    "WARRANT|PREFERRED|DEPOSITARY|RIGHTS?|UNITS?|ETF|FUND|NOTES DUE|BOND|DEBENTURE"
)

def normalize_symbol(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper().str.replace(".", "-", regex=False)

def refresh_sp500(csv_path: Path, issues: list[str] | None = None) -> pd.DataFrame:
    issues = issues if issues is not None else []
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    for url in SOURCES:
        try:
            response = requests.get(url, timeout=30, headers={"User-Agent": "HybridPaperScanner/3.0"})
            response.raise_for_status()
            temp = csv_path.with_suffix(".download.csv")
            temp.write_bytes(response.content)
            frame = pd.read_csv(temp)
            temp.unlink(missing_ok=True)
            symbol_col = next((c for c in frame.columns if c.lower() in {"symbol", "ticker"}), None)
            if symbol_col is None:
                raise ValueError(f"No Symbol/Ticker column in {list(frame.columns)}")
            out = pd.DataFrame({"Symbol": normalize_symbol(frame[symbol_col])})
            out["Company"] = frame.get("Security", frame.get("Name", out["Symbol"]))
            out["Sector"] = frame.get("GICS Sector", frame.get("Sector", "Unclassified"))
            out = out.dropna(subset=["Symbol"]).drop_duplicates("Symbol")
            if len(out) < 400:
                raise ValueError(f"downloaded only {len(out)} S&P 500 symbols")
            out.to_csv(csv_path, index=False)
            issues.append(f"S&P 500 CSV refreshed from {url}; rows={len(out)}")
            return out
        except Exception as exc:
            issues.append(f"S&P 500 refresh source failed: {url}: {type(exc).__name__}: {exc}")
    raise RuntimeError("All non-Wikipedia S&P 500 refresh sources failed")

def load_sp500(csv_path: Path, minimum: int = 400) -> pd.DataFrame:
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing {csv_path}")
    frame = pd.read_csv(csv_path)
    if "Symbol" not in frame.columns:
        raise ValueError("sp500_constituents.csv must contain Symbol column")
    frame["Symbol"] = normalize_symbol(frame["Symbol"])
    if "Company" not in frame.columns:
        frame["Company"] = frame["Symbol"]
    if "Sector" not in frame.columns:
        frame["Sector"] = "Unclassified"
    frame = frame.dropna(subset=["Symbol"]).drop_duplicates("Symbol")
    if len(frame) < minimum:
        raise RuntimeError(f"S&P 500 fail-fast: loaded {len(frame)} symbols; minimum is {minimum}")
    frame["Universe"] = "S&P 500"
    frame["Market Cap"] = pd.NA
    return frame[["Symbol", "Company", "Sector", "Universe", "Market Cap"]]

def load_nasdaq(min_market_cap: float, issues: list[str]) -> tuple[pd.DataFrame, int]:
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange=nasdaq&download=true"
    try:
        payload = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).json()
        rows = payload["data"]["rows"]
        raw = pd.DataFrame(rows)
        raw["Market Cap"] = pd.to_numeric(raw.get("marketCap"), errors="coerce")
        company = raw.get("name", pd.Series("", index=raw.index)).fillna("").astype(str)
        common_mask = ~company.str.upper().str.contains(BAD_KEYWORDS, regex=True, na=False)
        excluded = int((~common_mask).sum())
        raw = raw[common_mask & (raw["Market Cap"] >= min_market_cap)].copy()
        out = pd.DataFrame({
            "Symbol": normalize_symbol(raw["symbol"]),
            "Company": raw.get("name", raw["symbol"]),
            "Sector": raw.get("sector", "Unclassified"),
            "Universe": "Nasdaq >= $1B",
            "Market Cap": raw["Market Cap"],
        }).drop_duplicates("Symbol")
        return out, excluded
    except Exception as exc:
        issues.append(f"Nasdaq loader failed: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["Symbol","Company","Sector","Universe","Market Cap"]), 0

def build_universe(root: Path, config: dict, refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    issues: list[str] = []
    csv_path = root / config["sp500_csv"]
    if refresh or not csv_path.exists() or csv_path.stat().st_size < 100:
        refresh_sp500(csv_path, issues)
    sp500 = load_sp500(csv_path, int(config["sp500_minimum_count"]))
    nasdaq, non_common_excluded = load_nasdaq(float(config["nasdaq_min_market_cap_usd"]), issues)
    include_path = root / "universe/manual_include.csv"
    exclude_path = root / "universe/manual_exclude.csv"
    include = pd.read_csv(include_path) if include_path.exists() else pd.DataFrame(columns=["Symbol","Company","Sector"])
    if not include.empty:
        include["Symbol"] = normalize_symbol(include["Symbol"])
        include["Universe"] = "Manual Include"
        include["Market Cap"] = pd.NA
    exclude = pd.read_csv(exclude_path) if exclude_path.exists() else pd.DataFrame(columns=["Symbol"])
    excluded_symbols = set(normalize_symbol(exclude["Symbol"])) if not exclude.empty else set()
    # S&P 500 rows first, so the S&P label wins for overlapping Nasdaq names.
    combined = pd.concat([sp500, nasdaq, include], ignore_index=True)
    before = len(combined)
    combined = combined[~combined["Symbol"].isin(excluded_symbols)]
    combined = combined.drop_duplicates("Symbol", keep="first").head(int(config["max_symbols"])).reset_index(drop=True)
    audit = pd.DataFrame([
        {"Source": "S&P 500 static CSV", "Loaded": len(sp500), "Excluded": 0, "Final": len(sp500), "Status": "PASS" if len(sp500) >= int(config["sp500_minimum_count"]) else "FAIL"},
        {"Source": "Nasdaq >= $1B", "Loaded": len(nasdaq) + non_common_excluded, "Excluded": non_common_excluded, "Final": len(nasdaq), "Status": "PASS" if len(nasdaq) else "WARNING"},
        {"Source": "Manual Include", "Loaded": len(include), "Excluded": 0, "Final": len(include), "Status": "PASS"},
        {"Source": "Manual Exclude", "Loaded": len(exclude), "Excluded": len(excluded_symbols), "Final": 0, "Status": "PASS"},
        {"Source": "Duplicate consolidation", "Loaded": before, "Excluded": before - len(combined) - len(excluded_symbols), "Final": len(combined), "Status": "PASS"},
        {"Source": "Final Universe", "Loaded": len(combined), "Excluded": 0, "Final": len(combined), "Status": "PASS" if len(combined) >= 700 else "FAIL"},
    ])
    if len(sp500) < int(config["sp500_minimum_count"]):
        raise RuntimeError("S&P 500 universe validation failed")
    if len(combined) < 700:
        raise RuntimeError(f"Final universe unexpectedly small: {len(combined)}")
    return combined, audit, issues

def self_test() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sp500_constituents.csv"
        pd.DataFrame({"Symbol": [f"T{i:03d}" for i in range(507)]}).to_csv(path, index=False)
        frame = load_sp500(path, 400)
        assert len(frame) == 507 and set(frame["Universe"]) == {"S&P 500"}
    print("UNIVERSE LOADER SELF-TEST PASSED")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-sp500", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--config", default="strategy_config.json")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        import json
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))["universe"]
        result, audit, issues = build_universe(Path.cwd(), config, refresh=args.refresh_sp500)
        print(audit.to_string(index=False))
        print(f"Final universe: {len(result)}")
        for issue in issues:
            print(issue)
