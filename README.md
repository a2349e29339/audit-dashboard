# Personal Audit Dashboard

A local, private financial audit dashboard: every transaction across your accounts,
auto-categorized, with a fraud-desk-style review queue, a money-flow Sankey, trip
detection, a 30-day cash forecast, and bankroll-aware gambling accounting — so you
can see exactly where your money goes. **Everything runs and stays on your machine**;
all data lives in a local SQLite file (`data/audit.db`) that is never committed or
sent anywhere.

## Quick start

Requires Python 3.10+ with `flask` and `requests`:

```bash
python3 -m pip install flask requests
./run.sh
```

Open http://localhost:5177 — then in **Settings → Seed demo data** to explore with
sample data, or connect your real accounts:

## Connect your accounts (SimpleFIN)

1. Create an account at **SimpleFIN Bridge** — https://beta-bridge.simplefin.org
   (~$1.50/mo). Connect your banks, cards, and brokerages there (most major
   institutions work, including many 401(k)/IRA custodians).
2. Generate a **setup token** and paste it into **Settings → SimpleFIN connection →
   Connect**. The token is exchanged once for an access URL stored only in your
   local database — it is never displayed again or sent anywhere else.
3. Click **Sync now**. Note: SimpleFIN serves ~90 days of history per institution;
   for deeper history use **Settings → Import a statement file** (CSV/OFX/QFX from
   your bank's website — re-imports skip duplicates and card payments auto-pair).
4. On the **Accounts** tab, confirm each account's type (checking / savings /
   credit / retirement / investment) — the type controls whether an account feeds
   cash-flow math or is tracked as net-worth only.
5. Optional: `./install_autosync.sh` (macOS) syncs automatically every 4 hours.

## Make it yours

- **Settings → Personalization** — your home state (used to detect trips), your
  401(k) deferral/match percentages (splits combined contribution lines into your
  money vs. employer match), and friendly names for recurring charges ("acme
  prop = Rent") used in the cash forecast.
- **Settings → Categorization rules** — the app ships with a large generic vendor
  library (rules match a description substring; lowest priority wins). Your own
  merchants accumulate as you add rules; manual category edits are never
  overwritten. Anything undecipherable sweeps to *Miscellaneous* after 30 days
  (and is reclaimed if a matching rule appears later).
- **Income tab** — define income sources (paycheck amount + cadence; monthly
  estimates for irregular gigs) to activate the late/short "income watch".
- **Gambling tab** — platform deposits/withdrawals are treated as bankroll
  transfers, not spending; enter a bankroll snapshot and optionally import a
  Pikkit CSV export for true P/L, ROI, and per-book stats.

## How the accounting works

- Transfers between your own accounts, card payoffs, and investment contributions
  are pair-matched automatically (same amount, opposite sign, within 7 days) and
  never count as income or spending. Refunds net against their category; credits
  on a credit card are never income.
- The Flow tab groups categories into Essentials / Lifestyle / Saved & invested;
  months where you outspend income show a red *overspend* notch rather than a
  fake income source. Click any node for the transactions behind it.
- The Review tab flags only genuine anomalies: first-ever merchants ≥$50 that
  never recurred, same-day duplicate charges, and outflows large relative to
  your own history (learned recurring bills excluded).
- Averages only use calendar months where **all** your major accounts have data,
  so a deep statement import can't skew "monthly average" numbers.

## Files

- `app.py` — Flask server + API (port 5177, or `PORT` env)
- `db.py` — schema, default categories, generic vendor rule library
- `simplefin.py` — SimpleFIN protocol client
- `sync.py` — headless sync (used by autosync)
- `demo_data.py` — demo seed/clear
- `static/` — dashboard UI (vanilla JS, no external dependencies, works offline)
- `data/audit.db` — **your data**; gitignored. Back it up; delete it to start over.

## Privacy

No telemetry, no external calls except to your own SimpleFIN bridge. The
`data/` and `memos/` directories are gitignored so a clone/fork never contains
anyone's financial information.
