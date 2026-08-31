"""Personal financial audit dashboard — local Flask server.

Everything runs and stays on this machine. The SimpleFIN access URL (the only
credential involved) is stored in the local SQLite database and is never
returned by any API endpoint.
"""
import csv
import hashlib
import io
import re
import statistics
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Flask, jsonify, request, send_from_directory

import demo_data
import simplefin
from db import connect, init_db, categorize_transaction

app = Flask(__name__, static_folder="static")
init_db()

SPEND_TYPES = ("checking", "savings", "credit", "unknown")  # accounts that feed cash-flow math


# ---------- helpers ----------

def get_setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def parse_range():
    """Read ?start=YYYY-MM-DD&end=YYYY-MM-DD, defaulting to last 6 full months."""
    today = date.today()
    end_s = request.args.get("end")
    start_s = request.args.get("start")
    end_d = date.fromisoformat(end_s) if end_s else today
    if start_s:
        start_d = date.fromisoformat(start_s)
    else:
        start_d = (today.replace(day=1) - timedelta(days=150)).replace(day=1)
    start = int(datetime(start_d.year, start_d.month, start_d.day).timestamp())
    end = int(datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59).timestamp())
    return start, end, start_d, end_d


def txn_dict(r):
    return {
        "id": r["id"], "account_id": r["account_id"], "posted": r["posted"],
        "date": datetime.fromtimestamp(r["posted"]).strftime("%Y-%m-%d"),
        "amount": r["amount"], "description": r["description"], "payee": r["payee"],
        "memo": r["memo"], "pending": r["pending"], "category_id": r["category_id"],
        "category": r["cat_name"] if "cat_name" in r.keys() else None,
        "category_kind": r["cat_kind"] if "cat_kind" in r.keys() else None,
        "category_source": r["category_source"], "reviewed": r["reviewed"],
        "account": r["acct_name"] if "acct_name" in r.keys() else None,
        "account_type": r["acct_type"] if "acct_type" in r.keys() else None,
    }


TXN_SELECT = """
SELECT t.*, c.name AS cat_name, c.kind AS cat_kind, a.name AS acct_name, a.type AS acct_type
FROM transactions t
LEFT JOIN categories c ON c.id = t.category_id
JOIN accounts a ON a.id = t.account_id
"""


# ---------- static ----------

@app.get("/")
def index():
    return send_from_directory("static", "index.html")


# ---------- accounts ----------

@app.get("/api/accounts")
def api_accounts():
    conn = connect()
    rows = conn.execute("SELECT * FROM accounts ORDER BY type, name").fetchall()
    out = []
    for r in rows:
        out.append({k: r[k] for k in r.keys()})
    hist = {}
    for h in conn.execute("SELECT account_id, date, balance FROM balance_history ORDER BY date"):
        hist.setdefault(h["account_id"], []).append({"date": h["date"], "balance": h["balance"]})
    hold = {}
    for h in conn.execute("SELECT * FROM holdings ORDER BY market_value DESC"):
        hold.setdefault(h["account_id"], []).append(
            {k: h[k] for k in ("symbol", "description", "shares", "market_value", "purchase_price")})
    for a in out:
        a["history"] = hist.get(a["id"], [])
        a["holdings"] = hold.get(a["id"], [])
    conn.close()
    return jsonify(out)


@app.post("/api/accounts/<path:acct_id>")
def api_account_update(acct_id):
    body = request.get_json(force=True)
    conn = connect()
    if "type" in body:
        if body["type"] not in ("checking", "savings", "credit", "retirement", "investment", "unknown"):
            return jsonify({"error": "bad type"}), 400
        conn.execute("UPDATE accounts SET type=? WHERE id=?", (body["type"], acct_id))
    if "hidden" in body:
        conn.execute("UPDATE accounts SET hidden=? WHERE id=?", (1 if body["hidden"] else 0, acct_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------- transactions ----------

@app.get("/api/transactions")
def api_transactions():
    start, end, _, _ = parse_range()
    q = TXN_SELECT + " WHERE t.posted BETWEEN ? AND ? AND a.hidden=0"
    params = [start, end]
    if request.args.get("account"):
        q += " AND t.account_id=?"
        params.append(request.args["account"])
    elif request.args.get("investment") != "1":
        # Brokerage/crypto activity (trades, coin moves) is excluded by default —
        # it's portfolio churn, not money in or out
        q += " AND a.type IN ('checking','savings','credit','unknown')"
    if request.args.get("category"):
        if request.args["category"] == "uncategorized":
            q += " AND t.category_id IS NULL"
        else:
            q += " AND t.category_id=?"
            params.append(int(request.args["category"]))
    if request.args.get("search"):
        q += " AND (LOWER(t.description) LIKE ? OR LOWER(t.payee) LIKE ? OR LOWER(t.memo) LIKE ?)"
        s = f"%{request.args['search'].lower()}%"
        params += [s, s, s]
    q += " ORDER BY t.posted DESC, t.id LIMIT 2000"
    conn = connect()
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify([txn_dict(r) for r in rows])


@app.post("/api/transactions/<path:txn_id>")
def api_txn_update(txn_id):
    body = request.get_json(force=True)
    conn = connect()
    if "category_id" in body:
        conn.execute("UPDATE transactions SET category_id=?, category_source='manual' WHERE id=?",
                     (body["category_id"], txn_id))
    if "reviewed" in body:
        conn.execute("UPDATE transactions SET reviewed=? WHERE id=?",
                     (1 if body["reviewed"] else 0, txn_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------- categories & rules ----------

@app.get("/api/categories")
def api_categories():
    conn = connect()
    rows = conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM transactions t WHERE t.category_id=c.id) AS txn_count "
        "FROM categories c ORDER BY c.kind, c.name").fetchall()
    conn.close()
    return jsonify([{k: r[k] for k in r.keys()} for r in rows])


@app.post("/api/categories")
def api_category_create():
    body = request.get_json(force=True)
    name, kind = body.get("name", "").strip(), body.get("kind", "expense")
    if not name or kind not in ("expense", "income", "transfer"):
        return jsonify({"error": "name required; kind must be expense|income|transfer"}), 400
    conn = connect()
    try:
        cur = conn.execute("INSERT INTO categories (name, kind) VALUES (?, ?)", (name, kind))
        conn.commit()
        return jsonify({"ok": True, "id": cur.lastrowid})
    except Exception:
        return jsonify({"error": "category already exists"}), 400
    finally:
        conn.close()


@app.get("/api/rules")
def api_rules():
    conn = connect()
    rows = conn.execute(
        "SELECT r.*, c.name AS category FROM rules r JOIN categories c ON c.id=r.category_id "
        "ORDER BY r.priority, r.id").fetchall()
    conn.close()
    return jsonify([{k: r[k] for k in r.keys()} for r in rows])


@app.post("/api/rules")
def api_rule_create():
    body = request.get_json(force=True)
    pattern = body.get("pattern", "").strip()
    if not pattern or not body.get("category_id"):
        return jsonify({"error": "pattern and category_id required"}), 400
    conn = connect()
    conn.execute("INSERT INTO rules (pattern, category_id, priority) VALUES (?,?,?)",
                 (pattern, int(body["category_id"]), int(body.get("priority", 100))))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.delete("/api/rules/<int:rule_id>")
def api_rule_delete(rule_id):
    conn = connect()
    conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/rules/apply")
def api_rules_apply():
    """Re-run rules over transactions that were never manually categorized."""
    conn = connect()
    rows = conn.execute("SELECT * FROM transactions "
                        "WHERE category_source IS NULL OR category_source IN ('rule','fallback')").fetchall()
    changed = 0
    for r in rows:
        cat = categorize_transaction(conn, r)
        if cat != r["category_id"]:
            conn.execute("UPDATE transactions SET category_id=?, category_source=? WHERE id=?",
                         (cat, "rule" if cat else None, r["id"]))
            changed += 1
    changed += detect_transfers(conn)
    swept = sweep_to_misc(conn)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "changed": changed, "swept_to_misc": swept})


# ---------- transfer detection ----------

def detect_transfers(conn, window_days=7):
    """Pair-match uncategorized transactions that offset each other across accounts
    (card payments, transfers, contributions) so they never count as income/spending.

    A pair = opposite amounts to the cent, different accounts, within window_days.
    Eligible for re-labeling: uncategorized transactions, plus rule-assigned
    "Unlinked Card Spending" (so linking or importing a card upgrades its old
    payments to true transfers). Manual edits are untouched."""
    cats = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
    kinds = {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM categories")}
    acct_type = {r["id"]: r["type"] for r in conn.execute("SELECT id, type FROM accounts")}
    unlinked_id = cats.get("Unlinked Card Spending")
    txns = conn.execute("SELECT id, account_id, posted, amount, category_id, category_source "
                        "FROM transactions WHERE pending=0").fetchall()

    def eligible(t):
        return t["category_id"] is None or (
            t["category_id"] == unlinked_id and t["category_source"] == "rule")

    by_amt = defaultdict(list)
    for t in txns:
        by_amt[round(t["amount"], 2)].append(t)
    uncat = sorted((t for t in txns if eligible(t) and abs(t["amount"]) >= 0.01),
                   key=lambda t: t["posted"])
    matched, changed = set(), 0
    window = window_days * 86400
    for t in uncat:
        if t["id"] in matched:
            continue
        candidates = [p for p in by_amt.get(round(-t["amount"], 2), [])
                      if p["id"] not in matched and p["account_id"] != t["account_id"]
                      and abs(p["posted"] - t["posted"]) <= window
                      and (eligible(p) or kinds.get(p["category_id"]) == "transfer")]
        if not candidates:
            continue
        p = min(candidates, key=lambda c: abs(c["posted"] - t["posted"]))
        types = {acct_type.get(t["account_id"]), acct_type.get(p["account_id"])}
        if "credit" in types:
            cat = cats["Credit Card Payment"]
        elif types & {"retirement", "investment"}:
            cat = cats["Investment Contribution"]
        else:
            cat = cats["Account Transfer"]
        for side in (t, p):
            if eligible(side):
                conn.execute("UPDATE transactions SET category_id=?, category_source='transfer-match' "
                             "WHERE id=?", (cat, side["id"]))
                changed += 1
        matched.add(t["id"])
        matched.add(p["id"])
    return changed


def sweep_to_misc(conn, days=30):
    """Anything still uncategorized after `days` sweeps into Miscellaneous.

    Recent transactions stay visibly uncategorized so they prompt a look; older
    ones stop nagging. Swept rows keep source='fallback', so a future rule that
    matches them will still claim them on the next re-apply."""
    misc = conn.execute("SELECT id FROM categories WHERE name='Miscellaneous'").fetchone()
    if not misc:
        return 0
    cutoff = int(time.time()) - days * 86400
    return conn.execute("""
        UPDATE transactions SET category_id=?, category_source='fallback'
        WHERE category_id IS NULL AND posted < ?
          AND account_id IN (SELECT id FROM accounts
                             WHERE type IN ('checking','savings','credit','unknown'))
    """, (misc["id"], cutoff)).rowcount


@app.post("/api/transfers/detect")
def api_transfers_detect():
    conn = connect()
    changed = detect_transfers(conn)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "changed": changed})


# ---------- overview ----------

@app.get("/api/overview")
def api_overview():
    start, end, start_d, end_d = parse_range()
    conn = connect()

    # Net worth from current balances (all visible accounts; credit is negative)
    accounts = conn.execute("SELECT * FROM accounts WHERE hidden=0").fetchall()
    net_worth = sum(a["balance"] for a in accounts)
    retirement = sum(a["balance"] for a in accounts if a["type"] in ("retirement", "investment"))
    cash = sum(a["balance"] for a in accounts if a["type"] in ("checking", "savings"))
    debt = sum(a["balance"] for a in accounts if a["type"] == "credit")
    bankroll = bankroll_estimate_now(conn)
    if bankroll is not None:
        net_worth += bankroll

    # Cash flow from spending accounts, transfers excluded. Rent paid in the last
    # days of a month counts toward the month it pays for (see effective date).
    rows = conn.execute(TXN_SELECT + """
        WHERE t.posted BETWEEN ? AND ? AND a.hidden=0 AND a.type IN ('checking','savings','credit','unknown')
        AND (c.kind IS NULL OR c.kind != 'transfer')
    """, (start - 5 * 86400, end)).fetchall()

    def _eff_ts(r):
        if r["cat_name"] == "Housing" and r["amount"] < 0:
            d = date.fromtimestamp(r["posted"])
            if d.day >= 28:
                nxt = (d.replace(day=28) + timedelta(days=8)).replace(day=1)
                return int(datetime(nxt.year, nxt.month, 1, 12).timestamp())
        return r["posted"]

    rows = [r for r in rows if start <= _eff_ts(r) <= end]

    monthly = {}
    cat_spend = {}
    income_total = spend_total = 0.0
    for r in rows:
        mk = datetime.fromtimestamp(_eff_ts(r)).strftime("%Y-%m")
        m = monthly.setdefault(mk, {"income": 0.0, "spending": 0.0})
        name = r["cat_name"] or "Uncategorized"
        if r["amount"] > 0:
            # Inflows: only real income counts as income. Credits on a credit card
            # (refunds, uncategorized payment credits) net against spending instead —
            # they are never income.
            if r["cat_kind"] == "income":
                m["income"] += r["amount"]
                income_total += r["amount"]
            elif r["cat_kind"] == "expense" or r["acct_type"] == "credit":
                m["spending"] -= r["amount"]
                spend_total -= r["amount"]
                cat_spend[name] = cat_spend.get(name, 0.0) - r["amount"]
            else:
                m["income"] += r["amount"]
                income_total += r["amount"]
        elif r["amount"] < 0:
            m["spending"] += -r["amount"]
            spend_total += -r["amount"]
            cat_spend[name] = cat_spend.get(name, 0.0) + -r["amount"]

    # Contributions to investments: from cash accounts + payroll-direct (401k, Roth splits)
    _dest, payroll_direct, cash_contrib, _pbd = investment_flows(conn, start, end)
    saved = cash_contrib + payroll_direct
    # Leftover = cash income that was neither spent nor explicitly moved to
    # investments or the betting bankroll — it simply stayed in checking/savings.
    bankroll_flow = conn.execute("""
        SELECT COALESCE(SUM(-t.amount), 0) AS s FROM transactions t
        JOIN accounts a ON a.id=t.account_id JOIN categories c ON c.id=t.category_id
        WHERE t.posted BETWEEN ? AND ? AND a.hidden=0
          AND a.type IN ('checking','savings','credit','unknown')
          AND c.name = 'Gambling & Betting'
    """, (start, end)).fetchone()["s"]
    leftover = income_total - spend_total - cash_contrib - bankroll_flow
    # Wealth rate: fraction of TOTAL compensation (cash income + payroll deferrals
    # incl. employer match) that became assets (investments + leftover cash)
    total_comp = income_total + payroll_direct
    wealth_rate = round(100 * (saved + leftover) / total_comp, 1) if total_comp > 0 else None

    months_series = sorted(monthly.keys())
    savings_rate = round(100 * (income_total - spend_total) / income_total, 1) if income_total > 0 else None

    # Net worth history (sum of balance_history snapshots per date)
    nw_hist = conn.execute("""
        SELECT bh.date, SUM(bh.balance) AS total
        FROM balance_history bh JOIN accounts a ON a.id = bh.account_id
        WHERE a.hidden = 0
        GROUP BY bh.date ORDER BY bh.date
    """).fetchall()

    uncategorized = conn.execute("""
        SELECT COUNT(*) AS n FROM transactions t JOIN accounts a ON a.id=t.account_id
        WHERE t.category_id IS NULL AND a.hidden=0 AND t.posted BETWEEN ? AND ?
          AND a.type IN ('checking','savings','credit','unknown')
    """, (start, end)).fetchone()["n"]

    last_sync = conn.execute("SELECT MAX(ts) AS t FROM sync_log WHERE ok=1").fetchone()["t"]
    has_simplefin = get_setting(conn, "simplefin_access_url") is not None
    has_demo = conn.execute("SELECT COUNT(*) FROM accounts WHERE is_demo=1").fetchone()[0] > 0
    conn.close()

    return jsonify({
        "range": {"start": start_d.isoformat(), "end": end_d.isoformat()},
        "net_worth": round(net_worth, 2),
        "cash": round(cash, 2), "debt": round(debt, 2), "retirement": round(retirement, 2),
        "bankroll": bankroll,
        "income_total": round(income_total, 2), "spend_total": round(spend_total, 2),
        "saved_to_investments": round(saved, 2),
        "saved_from_bank": round(cash_contrib, 2),
        "saved_payroll": round(payroll_direct, 2),
        "leftover": round(leftover, 2),
        "bankroll_flow": round(bankroll_flow, 2),
        "wealth_rate": wealth_rate,
        "savings_rate": savings_rate,
        "monthly": [{"month": m, **{k: round(v, 2) for k, v in monthly[m].items()}} for m in months_series],
        "by_category": sorted(
            [{"category": k, "amount": round(v, 2)} for k, v in cat_spend.items() if v > 0.5],
            key=lambda x: -x["amount"]),
        "net_worth_history": [{"date": r["date"], "total": round(r["total"], 2)} for r in nw_hist],
        "uncategorized": uncategorized,
        "last_sync": last_sync,
        "has_simplefin": has_simplefin,
        "has_demo": has_demo,
    })


def data_coverage_start(conn):
    """Timestamp from which ALL major spending accounts have data.

    A statement import can extend one account's history years back; averaging
    'income vs spending' over months where the paycheck account has no data
    would be nonsense. Coverage = the latest first-transaction among non-hidden
    spending accounts with a meaningful history (≥5 transactions)."""
    # Group by institution, not account row: a live-linked card and its imported
    # statement history are the same entity, so the entity's coverage starts at
    # the EARLIER of the two.
    row = conn.execute("""
        SELECT MAX(f) AS start FROM (
            SELECT MIN(t.posted) AS f FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE a.hidden=0 AND a.type IN ('checking','savings','credit','unknown')
            GROUP BY LOWER(COALESCE(NULLIF(a.org_name,''), a.name)) HAVING COUNT(*) >= 5
        )""").fetchone()
    return row["start"]


# ---------- spend / net-worth time series ----------

@app.get("/api/timeseries")
def api_timeseries():
    """Spending and net worth over time at daily/weekly/monthly granularity.

    Net worth is reconstructed per bucket: cash/credit accounts are rolled back
    from the current balance using transactions; investment/retirement accounts
    use their sync-day snapshots (stepped, flat before the first snapshot)."""
    gran = request.args.get("granularity", "weekly")
    if gran not in ("daily", "weekly", "monthly"):
        gran = "weekly"
    mode = request.args.get("spend", "variable")
    if mode not in ("variable", "all"):
        mode = "variable"
    start, end, start_d, end_d = parse_range()
    conn = connect()
    # Clamp to full data coverage so long ranges don't show a partial-data prefix
    first = data_coverage_start(conn)
    if first:
        first_d = date.fromtimestamp(first)
        if first_d > start_d:
            start_d, start = first_d, int(datetime(first_d.year, first_d.month, first_d.day).timestamp())
    accounts = conn.execute("SELECT * FROM accounts WHERE hidden=0").fetchall()
    cash_types = ("checking", "savings", "credit", "unknown")
    cash_ids = {a["id"] for a in accounts if a["type"] in cash_types}
    cash_now = sum(a["balance"] for a in accounts if a["id"] in cash_ids)

    def bstart(d):
        if gran == "daily":
            return d
        if gran == "weekly":
            return d - timedelta(days=d.weekday())
        return d.replace(day=1)

    def bnext(d):
        if gran == "daily":
            return d + timedelta(days=1)
        if gran == "weekly":
            return d + timedelta(days=7)
        return (d.replace(day=28) + timedelta(days=8)).replace(day=1)

    buckets = []
    d = bstart(start_d)
    while d <= end_d:
        nd = bnext(d)
        buckets.append((d, min(nd - timedelta(days=1), end_d)))
        d = nd

    # Spending per bucket (same rules as the overview: transfers excluded,
    # refunds and credit-account inflows net against spending)
    rows = conn.execute(TXN_SELECT + """
        WHERE t.posted BETWEEN ? AND ? AND a.hidden=0
          AND a.type IN ('checking','savings','credit','unknown')
          AND (c.kind IS NULL OR c.kind != 'transfer')
    """, (start, end)).fetchall()
    # 'variable' mode answers "what do I spend day to day that isn't fixed?":
    # fixed/non-elastic categories are excluded outright, and payments to
    # unlinked cards (a month of variable spending in one lump) are spread over
    # 30 days. 'all' mode includes everything, with rent spread over its month.
    # Gambling is excluded from 'variable' too: it's bankroll funding, not
    # consumption (see the Gambling tab), so its lumpy in/outs would distort
    # the day-to-day picture. It still counts everywhere else.
    FIXED_CATS = {"Housing", "Utilities", "Insurance", "Subscriptions", "Commuter Benefit",
                  "Gambling & Betting", "AI Spending"}
    spend_by = defaultdict(float)
    for r in rows:
        cat = r["cat_name"]
        if mode == "variable" and (cat in FIXED_CATS or cat == "Unlinked Card Spending"):
            continue
        if mode == "all" and cat == "Housing" and r["amount"] < 0:
            continue  # spread below
        day = date.fromtimestamp(r["posted"])
        key = bstart(day)
        if r["amount"] < 0:
            spend_by[key] += -r["amount"]
        elif r["cat_kind"] == "expense" or r["acct_type"] == "credit":
            spend_by[key] -= r["amount"]

    spread_cat = "Housing" if mode == "all" else "Unlinked Card Spending"
    spread_rows = conn.execute(TXN_SELECT + """
        WHERE t.posted BETWEEN ? AND ? AND a.hidden=0 AND c.name = ? AND t.amount < 0
    """, (start - 45 * 86400, end, spread_cat)).fetchall()
    for h in spread_rows:
        d0 = date.fromtimestamp(h["posted"])
        daily = -h["amount"] / 30.0
        for i in range(30):
            day = d0 + timedelta(days=i)
            if start_d <= day <= end_d:
                spend_by[bstart(day)] += daily

    # Net worth always carries rent as a declining prepaid asset (accrual view)
    housing = spread_rows if spread_cat == "Housing" else conn.execute(TXN_SELECT + """
        WHERE t.posted BETWEEN ? AND ? AND a.hidden=0 AND c.name = 'Housing' AND t.amount < 0
    """, (start - 45 * 86400, end)).fetchall()

    def prepaid_at(day):
        total = 0.0
        for h in housing:
            d0 = date.fromtimestamp(h["posted"])
            elapsed = (day - d0).days
            if 0 <= elapsed < 30:
                total += -h["amount"] * (30 - elapsed) / 30.0
        return total

    # Cash balance rollback: balance at time T = now - txns posted after T
    cash_txns = sorted(conn.execute(
        "SELECT posted, amount FROM transactions WHERE account_id IN (%s) AND posted > ?"
        % ",".join("?" * len(cash_ids)), (*cash_ids, start - 86400)).fetchall(),
        key=lambda r: r["posted"])
    # suffix sums over the sorted list
    suffix = [0.0] * (len(cash_txns) + 1)
    for i in range(len(cash_txns) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + cash_txns[i]["amount"]
    posted_list = [r["posted"] for r in cash_txns]

    import bisect
    def cash_at(ts):
        i = bisect.bisect_right(posted_list, ts)
        return cash_now - suffix[i]

    hist = defaultdict(list)
    for h in conn.execute("""SELECT bh.account_id, bh.date, bh.balance FROM balance_history bh
        JOIN accounts a ON a.id=bh.account_id WHERE a.hidden=0 ORDER BY bh.date"""):
        if h["account_id"] not in cash_ids:
            hist[h["account_id"]].append((h["date"], h["balance"]))

    def invest_at(day_iso):
        total = 0.0
        for snaps in hist.values():
            val = snaps[0][1]
            for dt, bal in snaps:
                if dt <= day_iso:
                    val = bal
                else:
                    break
            total += val
        return total

    snaps = conn.execute("SELECT date, amount FROM bankroll_snapshots ORDER BY date").fetchall()
    gtx = [(t["posted"], -t["amount"]) for t in gambling_cash_txns(conn)]
    conn.close()

    def bankroll_at(te):
        if not snaps:
            return 0.0
        te_iso = date.fromtimestamp(te).isoformat()
        ref = None
        for s in snaps:
            if s["date"] <= te_iso:
                ref = s
            else:
                break
        if ref:
            ref_ts = int(datetime.strptime(ref["date"], "%Y-%m-%d").timestamp()) + 86399
            return max(ref["amount"] + sum(a for p, a in gtx if ref_ts < p <= te), 0)
        first_s = snaps[0]
        f_ts = int(datetime.strptime(first_s["date"], "%Y-%m-%d").timestamp()) + 86399
        return max(first_s["amount"] - sum(a for p, a in gtx if te < p <= f_ts), 0)

    points = []
    for b0, b1 in buckets:
        te = int(datetime(b1.year, b1.month, b1.day, 23, 59, 59).timestamp())
        points.append({
            "date": b0.isoformat(),
            "spend": round(spend_by.get(b0, 0.0), 2),
            "net_worth": round(cash_at(te) + invest_at(b1.isoformat()) + prepaid_at(b1)
                               + bankroll_at(te), 2),
        })
    return jsonify({"granularity": gran, "spend_mode": mode, "points": points})


# ---------- human-readable names for recurring entities ----------

# Generic, product-level names only. Personal labels (e.g. "Rent" for a specific
# landlord's ACH descriptor) live in the database — settings key 'display_names',
# editable in the Settings tab — so they never ship with the code.
GENERIC_NAMES = [
    ("gpc", "Georgia Power"), ("georgia power", "Georgia Power"),
    ("american express ach", "Amex card payment"),
    ("discover e-payment", "Discover card payment"), ("robinhood card", "Robinhood card payment"),
    ("verizon", "Verizon"), ("moneylink", "Schwab transfer"),
    ("robinhood securities", "Robinhood deposit"), ("instant bank deposit", "Robinhood deposit"),
    ("betterment", "Betterment transfer"), ("headway", "Headway"),
    ("anthropic", "Claude"), ("claude.ai", "Claude"),
    ("epay", "Card payment"), ("payment thank you", "Card payment"),
]


def display_names(conn):
    """User's personal label map (from settings) followed by the generic ones."""
    import json as _json
    try:
        personal = _json.loads(get_setting(conn, "display_names") or "[]")
        personal = [(str(p).lower(), str(n)) for p, n in personal]
    except (ValueError, TypeError):
        personal = []
    return personal + GENERIC_NAMES


def friendly(desc, names=GENERIC_NAMES):
    dl = (desc or "").lower()
    for pat, name in names:
        if pat in dl:
            return name
    return " ".join((desc or "").split()[:3]).title()


# ---------- 30-day cash forecast ----------

@app.get("/api/forecast")
def api_forecast():
    """Project checking balance 30 days ahead using learned recurring events.

    Recurring checking transactions (>= $20 median, >= 2 occurrences over a
    25+ day span) are classified as weekly / semimonthly / monthly by observed
    frequency and re-projected forward. Card autopays use median amounts; days
    of month >= 28 are treated as end-of-month. Day-to-day card swipes live on
    the credit cards, so this covers the bills that actually hit checking."""
    import statistics as st
    from collections import Counter as C
    conn = connect()
    chks = conn.execute("SELECT id, balance FROM accounts WHERE type='checking' AND hidden=0").fetchall()
    if not chks:
        conn.close()
        return jsonify({"points": [], "events": [], "low": None})
    start_bal = sum(a["balance"] for a in chks)
    ids = [a["id"] for a in chks]
    since = int(time.time()) - 130 * 86400
    rows = conn.execute(
        "SELECT t.posted, t.amount, t.description FROM transactions t "
        "LEFT JOIN categories c ON c.id=t.category_id "
        "WHERE t.account_id IN (%s) AND t.posted >= ? AND t.pending=0 "
        "AND (c.name IS NULL OR c.name NOT IN ('Gambling & Betting','Venmo'))"
        % ",".join("?" * len(ids)), (*ids, since)).fetchall()
    names = display_names(conn)
    conn.close()

    groups = defaultdict(list)
    for r in rows:
        groups[normalize_desc(r["description"])].append(r)
    today = date.today()
    events = []
    for key, txns in groups.items():
        if len(txns) < 2:
            continue
        amts = [t["amount"] for t in txns]
        med = st.median(amts)
        if abs(med) < 20:
            continue
        dates = sorted(set(date.fromtimestamp(t["posted"]) for t in txns))  # distinct days
        span = (dates[-1] - dates[0]).days
        if span < 25 or len(dates) < 2:
            continue
        if (today - dates[-1]).days > 40:
            continue  # stale pattern — stopped recurring, don't project it
        per_month = len(dates) / (span / 30.4)
        label = max(txns, key=lambda t: t["posted"])["description"][:36]

        def month_events(doms):
            for m_off in (0, 1):
                base = (today.replace(day=1) + timedelta(days=32 * m_off)).replace(day=1)
                nxt = (base.replace(day=28) + timedelta(days=8)).replace(day=1)
                for dom in doms:
                    d = nxt - timedelta(days=1) if dom >= 28 else base.replace(day=dom)
                    if today < d <= today + timedelta(days=30):
                        events.append({"date": d, "amount": round(med, 2), "label": label})

        if per_month >= 3.5:      # ~weekly
            wd = C(d.weekday() for d in dates).most_common(1)[0][0]
            d = today + timedelta(days=1)
            while d <= today + timedelta(days=30):
                if d.weekday() == wd:
                    events.append({"date": d, "amount": round(med, 2), "label": label})
                d += timedelta(days=1)
        elif per_month >= 1.6:    # semimonthly only if the two modal days are truly spread
            doms = [dom for dom, _ in C(d.day for d in dates).most_common(2)]
            if len(doms) == 2 and min(abs(doms[0] - doms[1]), 30 - abs(doms[0] - doms[1])) >= 10:
                month_events(doms)
            else:
                month_events([doms[0]])
        elif per_month >= 0.6:    # monthly
            month_events([C(d.day for d in dates).most_common(1)[0][0]])

    events.sort(key=lambda e: e["date"])
    by_day = defaultdict(list)
    for e in events:
        by_day[e["date"]].append(e)
    points, bal = [], start_bal
    low_bal, low_date = start_bal, today
    for i in range(1, 31):
        d = today + timedelta(days=i)
        for e in by_day.get(d, []):
            bal += e["amount"]
        points.append({"date": d.isoformat(), "balance": round(bal, 2)})
        if bal < low_bal:
            low_bal, low_date = bal, d
    return jsonify({
        "start_balance": round(start_bal, 2),
        "points": points,
        "low": {"balance": round(low_bal, 2), "date": low_date.isoformat()},
        "events": [{"date": e["date"].isoformat(), "amount": e["amount"],
                    "label": e["label"], "name": friendly(e["label"], names)}
                   for e in events],
    })


# ---------- trip detection ----------

US_STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","HI","ID","IL","IN","IA","KS","KY",
             "LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC",
             "ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}
def home_state(conn):
    """User's home state: the 'home_state' setting, else the most common state
    across their transactions (set once at first call)."""
    hs = get_setting(conn, "home_state")
    if hs:
        return hs
    from collections import Counter as _C
    counts = _C()
    for r in conn.execute("SELECT description FROM transactions LIMIT 4000"):
        s = txn_state(r["description"])
        if s:
            counts[s] += 1
    hs = counts.most_common(1)[0][0] if counts else "??"
    set_setting(conn, "home_state", hs)
    conn.commit()
    return hs
PRESENCE_CATS = ("Dining & Coffee", "Gas & Fuel", "Groceries", "Personal Care")


# Delivery / online merchants whose HQ state appears in the description —
# never evidence of physically being there
ONLINE_MERCHANTS = ("doorda", "bt*dd", "uber eats", "grubhub", "instacart", ".com",
                    "openai", "chatgpt", "paypal", "amzn", "amazon", "aplpay netflix")


def txn_state(desc):
    """Best-effort state code from a card description (handles Amex's glued
    suffixes and ignores 'ENDING IN 1234' card-number phrasing)."""
    cleaned = re.sub(r"ENDING IN\s*\d*", " ", (desc or "")).replace("APPLE PAY", " ")
    tokens = re.findall(r"\b([A-Z]{2})\b", cleaned)
    states = [t for t in tokens if t in US_STATES]
    return states[-1] if states else None


@app.get("/api/trips")
def api_trips():
    """Cluster out-of-state physical spending into trips; split trip spend from
    the day-to-day baseline so averages become interpretable."""
    conn = connect()
    hs = home_state(conn)
    rows = conn.execute(TXN_SELECT + """
        WHERE a.hidden=0 AND a.type IN ('checking','savings','credit','unknown')
          AND t.amount < 0 AND (c.kind IS NULL OR c.kind = 'expense')
        ORDER BY t.posted""").fetchall()

    # Evidence of physically being away: dining/gas/groceries in another state,
    # or any Travel-category charge
    from collections import Counter as Ctr
    evidence = Ctr()
    for r in rows:
        dl = (r["description"] or "").lower()
        if any(m in dl for m in ONLINE_MERCHANTS):
            continue
        d = date.fromtimestamp(r["posted"])
        st_code = txn_state(r["description"])
        if st_code in (hs, None):
            continue
        if r["cat_name"] == "Travel" or r["cat_name"] in PRESENCE_CATS + ("Cash & ATM",):
            evidence[d] += 1

    # Cluster days with gaps <= 3; a trip = 2+ away-days, or one day with 2+ hits
    clusters, cur = [], []
    for d in sorted(evidence):
        if cur and (d - cur[-1]).days > 3:
            clusters.append(cur)
            cur = []
        cur.append(d)
    if cur:
        clusters.append(cur)
    clusters = [c for c in clusters if len(c) >= 2 or evidence[c[0]] >= 2]

    trips = []
    trip_windows = []
    for c in clusters:
        w0, w1 = c[0], c[-1]
        trip_windows.append((w0, w1))
        in_window = [r for r in rows if w0 <= date.fromtimestamp(r["posted"]) <= w1]
        # prepaid travel (flights/hotels bought up to 45 days before)
        pre = [r for r in rows if r["cat_name"] == "Travel"
               and w0 - timedelta(days=45) <= date.fromtimestamp(r["posted"]) < w0]
        states = C = {}
        from collections import Counter
        C = Counter(txn_state(r["description"]) for r in in_window
                    if txn_state(r["description"]) not in (hs, None))
        loc = C.most_common(1)[0][0] if C else "?"
        total = sum(-r["amount"] for r in in_window) + sum(-r["amount"] for r in pre)
        detail = sorted(in_window + pre, key=lambda r: r["amount"])[:30]
        trips.append({
            "start": w0.isoformat(), "end": w1.isoformat(), "days": (w1 - w0).days + 1,
            "location": loc, "total": round(total, 2),
            "prepaid_travel": round(sum(-r["amount"] for r in pre), 2),
            "txns": len(in_window) + len(pre),
            "transactions": [{
                "date": date.fromtimestamp(r["posted"]).isoformat(),
                "description": r["description"][:60],
                "amount": r["amount"], "category": r["cat_name"] or "—",
                "account": r["acct_name"],
            } for r in detail],
        })

    # Baseline: variable weekly spend excluding trip windows
    FIXED = {"Housing", "Utilities", "Insurance", "Subscriptions", "Commuter Benefit",
             "AI Spending"}
    def in_trip(d):
        return any(w0 <= d <= w1 for w0, w1 in trip_windows)
    base_total, base_days = 0.0, set()
    first = data_coverage_start(conn)
    first_d = date.fromtimestamp(first) if first else None
    for r in rows:
        d = date.fromtimestamp(r["posted"])
        if first_d and d < first_d:
            continue
        if in_trip(d) or r["cat_name"] in FIXED or r["cat_name"] == "Travel":
            continue
        base_total += -r["amount"]
        base_days.add(d)
    conn.close()
    span_days = (date.today() - first_d).days if first_d else 1
    trip_days_in_cov = sum(1 for w0, w1 in trip_windows for i in range((w1-w0).days+1)
                           if first_d and w0 + timedelta(days=i) >= first_d)
    base_weekly = round(base_total / max((span_days - trip_days_in_cov) / 7, 1), 2)
    # Keep only meaningful trips; baseline comparison only where coverage supports it
    trips = [t for t in trips if t["days"] >= 2 or t["total"] >= 500]
    for t in trips:
        in_cov = first_d and date.fromisoformat(t["start"]) >= first_d
        t["premium_vs_baseline"] = (round(t["total"] - base_weekly * (t["days"] / 7), 2)
                                    if in_cov else None)
    return jsonify({"trips": sorted(trips, key=lambda t: t["start"], reverse=True),
                    "baseline_weekly": base_weekly})


# ---------- review queue (flags) ----------

def normalize_desc(desc: str) -> str:
    """Merchant fingerprint: real descriptions carry unique confirmation codes
    ('ACME PROP WEB PMTS 0ZJ8CM…'), so collapse digit runs and keep the first
    three tokens to group recurring charges from the same payee."""
    import re
    tokens = re.sub(r"\d+", "#", (desc or "").lower()).split()
    return " ".join(tokens[:3])


@app.get("/api/flags")
def api_flags():
    """Fraud-dashboard style review queue over the last 60 days.

    Only spending accounts are audited (investment/retirement trades are not
    anomalies), transfers between your own accounts are never flagged, and
    recurring bills are learned via normalized merchant fingerprints."""
    conn = connect()
    cutoff = int(time.time()) - 60 * 86400
    rows = conn.execute(TXN_SELECT + """
        WHERE t.posted >= ? AND a.hidden=0 AND t.reviewed=0
          AND a.type IN ('checking','savings','credit','unknown')
        ORDER BY t.posted DESC
    """, (cutoff,)).fetchall()

    # Baseline outflow stats per spending account over the last year (for "unusually large")
    year_ago = int(time.time()) - 365 * 86400
    base = {}
    hist_rows = conn.execute("""
        SELECT t.account_id, t.amount, t.description, t.posted FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.posted >= ? AND a.type IN ('checking','savings','credit','unknown')
    """, (year_ago,)).fetchall()
    for r in hist_rows:
        if r["amount"] < 0:
            base.setdefault(r["account_id"], []).append(-r["amount"])
    thresholds = {}
    for acct, vals in base.items():
        if len(vals) >= 8:
            med = statistics.median(vals)
            p95 = sorted(vals)[int(0.95 * (len(vals) - 1))]
            thresholds[acct] = max(p95, med * 4, 200.0)

    # Recurring charges (same merchant fingerprint ≥3 times, steady amount) are expected
    from collections import defaultdict
    by_merchant = defaultdict(list)
    first_seen = {}
    for r in hist_rows:
        if r["amount"] < 0:
            key = normalize_desc(r["description"])
            by_merchant[key].append(-r["amount"])
            first_seen[key] = min(first_seen.get(key, r["posted"]), r["posted"])
    recurring = set()
    for key, vals in by_merchant.items():
        if len(vals) >= 3 and key:
            lo, hi = min(vals), max(vals)
            if hi - lo <= 0.15 * hi:
                recurring.add(key)

    flags = []
    seen_dupe = set()
    by_key = {}
    for r in rows:
        key = (r["account_id"], round(r["amount"], 2),
               datetime.fromtimestamp(r["posted"]).date(), normalize_desc(r["description"]))
        by_key.setdefault(key, []).append(r)

    for r in rows:
        if r["cat_kind"] == "transfer":
            continue  # internal money movement is never an anomaly
        reasons = []
        amt = -r["amount"] if r["amount"] < 0 else 0
        merchant = normalize_desc(r["description"])
        thr = thresholds.get(r["account_id"])
        if thr and amt >= thr and merchant not in recurring:
            reasons.append({"type": "large", "severity": "serious",
                            "detail": f"${amt:,.0f} outflow vs. typical for this account (p95 ≈ ${thr:,.0f})"})
        key = (r["account_id"], round(r["amount"], 2),
               datetime.fromtimestamp(r["posted"]).date(), merchant)
        if len(by_key.get(key, [])) > 1 and r["amount"] < 0 and amt >= 10 and key not in seen_dupe:
            seen_dupe.add(key)
            reasons.append({"type": "duplicate", "severity": "critical",
                            "detail": f"{len(by_key[key])} identical charges same day, same amount, same merchant"})
        # New merchant: first-ever appearance AND it never recurred — a merchant
        # you kept using since is clearly yours, not a fraud signal.
        if (r["amount"] < 0 and amt >= 50 and first_seen.get(merchant, 0) >= cutoff
                and len(by_merchant.get(merchant, [])) <= 1):
            reasons.append({"type": "new_merchant", "severity": "warning",
                            "detail": "First and only time this merchant appears in your history"})
        # Uncategorized is context, not an alarm — only shown alongside a real flag
        if reasons and r["category_id"] is None:
            reasons.append({"type": "uncategorized", "severity": "warning",
                            "detail": "No category assigned — add a rule or set one manually"})
        if reasons:
            d = txn_dict(r)
            d["reasons"] = reasons
            flags.append(d)
    conn.close()
    sev_rank = {"critical": 0, "serious": 1, "warning": 2}
    flags.sort(key=lambda f: (min(sev_rank[x["severity"]] for x in f["reasons"]), -f["posted"]))
    return jsonify(flags)


# ---------- SimpleFIN connect + sync ----------

@app.get("/api/prefs")
def api_prefs_get():
    import json as _json
    conn = connect()
    out = {
        "home_state": get_setting(conn, "home_state") or "",
        "k401_defer_pct": get_setting(conn, "k401_defer_pct") or "",
        "k401_match_pct": get_setting(conn, "k401_match_pct") or "",
        "display_names": _json.loads(get_setting(conn, "display_names") or "[]"),
    }
    conn.close()
    return jsonify(out)


@app.post("/api/prefs")
def api_prefs_set():
    import json as _json
    b = request.get_json(force=True)
    conn = connect()
    if "home_state" in b:
        set_setting(conn, "home_state", (b["home_state"] or "").strip().upper()[:2])
    if "k401_defer_pct" in b or "k401_match_pct" in b:
        try:
            d = float(b.get("k401_defer_pct") or 0)
            m = float(b.get("k401_match_pct") or 0)
            set_setting(conn, "k401_defer_pct", str(d))
            set_setting(conn, "k401_match_pct", str(m))
            if m > 0 and d + m > 0:
                # The retirement feed's contribution line includes the match; this
                # is the employer's share of each line
                set_setting(conn, "k401_match_share", str(m / (d + m)))
            else:
                conn.execute("DELETE FROM settings WHERE key='k401_match_share'")
        except (TypeError, ValueError):
            pass
    if "display_names" in b and isinstance(b["display_names"], list):
        clean = [[str(p).strip(), str(n).strip()] for p, n in b["display_names"]
                 if isinstance(p, str) and str(p).strip() and str(n).strip()]
        set_setting(conn, "display_names", _json.dumps(clean))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/simplefin/connect")
def api_simplefin_connect():
    """Claim a setup token. The token/access URL never appear in any response."""
    body = request.get_json(force=True)
    token = body.get("setup_token", "").strip()
    if not token:
        return jsonify({"error": "setup_token required"}), 400
    try:
        access_url = simplefin.claim_setup_token(token)
    except simplefin.SimpleFINError as e:
        return jsonify({"error": str(e)}), 400
    conn = connect()
    set_setting(conn, "simplefin_access_url", access_url)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Connected. Access URL stored locally. Run a sync to pull data."})


@app.post("/api/simplefin/disconnect")
def api_simplefin_disconnect():
    conn = connect()
    conn.execute("DELETE FROM settings WHERE key='simplefin_access_url'")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def run_sync(days=30):
    """Full sync cycle, callable without a web request (used by /api/sync, the
    launchd background job via sync.py, and anything else that needs fresh data)."""
    conn = connect()
    access_url = get_setting(conn, "simplefin_access_url")
    if not access_url:
        conn.close()
        return {"error": "SimpleFIN is not connected yet — add your setup token in Settings.",
                "status": 400}
    try:
        data = simplefin.fetch_accounts(access_url, start_date=int(time.time()) - days * 86400)
    except simplefin.SimpleFINError as e:
        conn.execute("INSERT INTO sync_log (ts, ok, message) VALUES (?,0,?)", (int(time.time()), str(e)))
        conn.commit()
        conn.close()
        return {"error": str(e), "status": 502}

    now = int(time.time())
    added = 0
    today_s = date.today().isoformat()
    for acct in data.get("accounts", []):
        aid = acct["id"]
        existing = conn.execute("SELECT type FROM accounts WHERE id=?", (aid,)).fetchone()
        conn.execute("""
            INSERT INTO accounts (id, org_name, name, currency, balance, available, balance_date, last_sync)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              org_name=excluded.org_name, name=excluded.name, balance=excluded.balance,
              available=excluded.available, balance_date=excluded.balance_date, last_sync=excluded.last_sync
        """, (aid, (acct.get("org") or {}).get("name", ""), acct.get("name", ""),
              acct.get("currency", "USD"), float(acct.get("balance", 0)),
              float(acct["available-balance"]) if acct.get("available-balance") is not None else None,
              acct.get("balance-date"), now))
        # Guess a type for brand-new accounts from the name
        if existing is None:
            nm = (acct.get("name", "") or "").lower()
            guess = ("retirement" if any(k in nm for k in ("401", "roth", "ira")) else
                     "credit" if any(k in nm for k in ("card", "visa", "master", "amex", "credit")) else
                     "savings" if "sav" in nm else
                     "checking" if any(k in nm for k in ("check", "everyday", "college", "spending")) else "unknown")
            conn.execute("UPDATE accounts SET type=? WHERE id=?", (guess, aid))
        conn.execute("INSERT OR REPLACE INTO balance_history VALUES (?,?,?)",
                     (aid, today_s, float(acct.get("balance", 0))))
        if "holdings" in acct:
            conn.execute("DELETE FROM holdings WHERE account_id=?", (aid,))
            for h in acct["holdings"] or []:
                try:
                    conn.execute("""INSERT OR REPLACE INTO holdings
                        (account_id, symbol, description, shares, market_value, purchase_price, updated)
                        VALUES (?,?,?,?,?,?,?)""",
                        (aid, h.get("symbol", ""), h.get("description", ""),
                         float(h.get("shares") or 0), float(h.get("market_value") or 0),
                         float(h.get("purchase_price") or 0), now))
                except (TypeError, ValueError):
                    continue
        for t in acct.get("transactions", []):
            tid = f"{aid}:{t['id']}"
            # Pending transactions often lack a posted date — fall back to transacted_at
            posted_ts = int(t.get("posted") or t.get("transacted_at") or now)
            if conn.execute("SELECT 1 FROM transactions WHERE id=?", (tid,)).fetchone():
                conn.execute("UPDATE transactions SET pending=?, posted=? WHERE id=?",
                             (1 if t.get("pending") else 0, posted_ts, tid))
                continue
            row = {"description": t.get("description", ""), "payee": t.get("payee", ""),
                   "memo": t.get("memo", "")}
            cat = categorize_transaction(conn, row)
            conn.execute("""
                INSERT INTO transactions (id, account_id, posted, amount, description, payee, memo,
                                          pending, category_id, category_source, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (tid, aid, posted_ts, float(t["amount"]), t.get("description", ""),
                  t.get("payee", ""), t.get("memo", ""), 1 if t.get("pending") else 0,
                  cat, "rule" if cat else None, now))
            added += 1

    detect_transfers(conn)
    sweep_to_misc(conn)
    warnings = "; ".join(data.get("_warnings", [])) if data.get("_warnings") else None
    conn.execute("INSERT INTO sync_log (ts, ok, message, accounts, added) VALUES (?,1,?,?,?)",
                 (now, warnings or "ok", len(data.get("accounts", [])), added))
    conn.commit()
    conn.close()
    return {"ok": True, "accounts": len(data.get("accounts", [])), "added": added,
            "warnings": warnings}


@app.post("/api/sync")
def api_sync():
    result = run_sync(days=int(request.args.get("days", 90)))
    return jsonify(result), result.pop("status", 200)


# ---------- statement file import (CSV / OFX / QFX) ----------

DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d %b %Y", "%b %d, %Y")


def _parse_date(s):
    s = (s or "").strip()
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt)
            return int(d.replace(hour=12).timestamp())
        except ValueError:
            continue
    return None


def _parse_amount(s):
    s = (s or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def parse_ofx(text):
    """Minimal OFX/QFX parser. OFX amounts are already signed (charges negative)."""
    out = []
    for block in re.split(r"<STMTTRN>", text, flags=re.I)[1:]:
        block = re.split(r"</STMTTRN>", block, flags=re.I)[0]

        def tag(name):
            m = re.search(rf"<{name}>([^<\r\n]*)", block, re.I)
            return m.group(1).strip() if m else ""
        dt, amt = tag("DTPOSTED")[:8], _parse_amount(tag("TRNAMT"))
        if len(dt) != 8 or amt is None:
            continue
        try:
            posted = int(datetime(int(dt[:4]), int(dt[4:6]), int(dt[6:8]), 12).timestamp())
        except ValueError:
            continue
        out.append({"posted": posted, "amount": amt,
                    "description": tag("NAME") or tag("MEMO"),
                    "memo": tag("MEMO") if tag("NAME") else "",
                    "fitid": tag("FITID")})
    return out


def parse_csv_statement(text):
    """Generic bank-CSV parser: finds the header row, then date/description/amount
    columns (or separate debit/credit columns)."""
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return []
    header_i, cols = None, {}
    for i, row in enumerate(rows[:8]):
        low = [c.strip().lower() for c in row]
        has_date = any("date" in c for c in low)
        has_desc = any(k in c for c in low for k in ("desc", "merchant", "payee", "name", "memo"))
        if has_date and has_desc:
            header_i = i
            for j, c in enumerate(low):
                if "date" in c and "post" not in c and "date" not in cols:
                    cols["date"] = j
                elif "date" in c and "date" not in cols:
                    cols["date"] = j
                if "desc" in c or "merchant" in c or "payee" in c:
                    cols.setdefault("desc", j)
                if c == "amount" or ("amount" in c and "amount" not in cols):
                    cols["amount"] = j
                if "debit" in c:
                    cols["debit"] = j
                if "credit" in c and "card" not in c:
                    cols["credit"] = j
                if c in ("memo", "notes"):
                    cols.setdefault("memo", j)
            break
    if header_i is None or "date" not in cols or "desc" not in cols:
        return []
    out = []
    for row in rows[header_i + 1:]:
        if len(row) <= max(cols.values()):
            continue
        posted = _parse_date(row[cols["date"]])
        if posted is None:
            continue
        amt = None
        if "amount" in cols:
            amt = _parse_amount(row[cols["amount"]])
        elif "debit" in cols or "credit" in cols:
            d = _parse_amount(row[cols.get("debit", -1)] if "debit" in cols else "")
            c = _parse_amount(row[cols.get("credit", -1)] if "credit" in cols else "")
            amt = -(abs(d)) if d else (abs(c) if c else None)
        if amt is None:
            continue
        out.append({"posted": posted, "amount": amt,
                    "description": row[cols["desc"]].strip(),
                    "memo": row[cols["memo"]].strip() if "memo" in cols else "",
                    "fitid": ""})
    return out


@app.post("/api/import")
def api_import():
    f = request.files.get("file")
    account_name = (request.form.get("account_name") or "").strip()
    account_type = request.form.get("account_type", "credit")
    invert = request.form.get("invert") == "1"
    if not f or not account_name:
        return jsonify({"error": "file and account_name are required"}), 400
    try:
        text = f.read().decode("utf-8-sig", errors="replace")
    except Exception as e:
        return jsonify({"error": f"could not read file: {e}"}), 400

    is_ofx = "<OFX" in text.upper() or "OFXHEADER" in text.upper() or "<STMTTRN" in text.upper()
    txns = parse_ofx(text) if is_ofx else parse_csv_statement(text)
    if not txns:
        return jsonify({"error": "No transactions found. For CSV, the file needs a header row "
                                 "with date, description, and amount (or debit/credit) columns."}), 400
    # OFX amounts are already signed per spec; the invert option applies to CSVs
    # like Amex's, which export charges as positive numbers.
    if invert and not is_ofx:
        for t in txns:
            t["amount"] = -t["amount"]

    slug = re.sub(r"[^a-z0-9]+", "-", account_name.lower()).strip("-")
    aid = f"manual:{slug}"
    conn = connect()
    now = int(time.time())
    if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (aid,)).fetchone():
        conn.execute("INSERT INTO accounts (id, org_name, name, type, last_sync) VALUES (?,?,?,?,?)",
                     (aid, "Imported", account_name, account_type, now))
    else:
        conn.execute("UPDATE accounts SET last_sync=? WHERE id=?", (now, aid))

    added = skipped = 0
    seen_in_batch = defaultdict(int)
    for t in txns:
        if t["fitid"]:
            tid = f"{aid}:{t['fitid']}"
        else:
            base = f"{t['posted']}|{t['amount']:.2f}|{t['description']}"
            seen_in_batch[base] += 1
            tid = f"{aid}:" + hashlib.md5(f"{base}|{seen_in_batch[base]}".encode()).hexdigest()[:16]
        if conn.execute("SELECT 1 FROM transactions WHERE id=?", (tid,)).fetchone():
            skipped += 1
            continue
        cat = categorize_transaction(conn, t | {"payee": ""})
        conn.execute("""INSERT INTO transactions (id, account_id, posted, amount, description,
                        payee, memo, pending, category_id, category_source, created_at)
                        VALUES (?,?,?,?,?,'',?,0,?,?,?)""",
                     (tid, aid, t["posted"], t["amount"], t["description"], t["memo"],
                      cat, "rule" if cat else None, now))
        added += 1
    transfers = detect_transfers(conn)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "added": added, "skipped_duplicates": skipped,
                    "transfer_matched": transfers, "format": "ofx" if is_ofx else "csv",
                    "account_id": aid})


# ---------- income sources (expected vs. actual) ----------

CADENCE_MONTHLY_FACTOR = {"weekly": 52 / 12, "biweekly": 26 / 12, "semimonthly": 2,
                          "monthly": 1, "irregular": 1}
CADENCE_GAP_DAYS = {"weekly": 7, "biweekly": 14, "semimonthly": 15.2, "monthly": 30.4}


@app.get("/api/income/sources")
def api_income_sources():
    conn = connect()
    rows = conn.execute("SELECT * FROM income_sources ORDER BY amount * "
                        "CASE cadence WHEN 'weekly' THEN 4.333 WHEN 'biweekly' THEN 2.167 "
                        "WHEN 'semimonthly' THEN 2 ELSE 1 END DESC").fetchall()
    conn.close()
    return jsonify([{k: r[k] for k in r.keys()} for r in rows])


@app.post("/api/income/sources")
def api_income_source_create():
    b = request.get_json(force=True)
    name, pattern = (b.get("name") or "").strip(), (b.get("pattern") or "").strip()
    cadence = b.get("cadence", "monthly")
    try:
        amount = float(b.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0
    if not name or not pattern or amount <= 0 or cadence not in CADENCE_MONTHLY_FACTOR:
        return jsonify({"error": "name, pattern, positive amount, and a valid cadence are required"}), 400
    conn = connect()
    conn.execute("INSERT INTO income_sources (name, pattern, amount, cadence) VALUES (?,?,?,?)",
                 (name, pattern, amount, cadence))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.delete("/api/income/sources/<int:sid>")
def api_income_source_delete(sid):
    conn = connect()
    conn.execute("DELETE FROM income_sources WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.get("/api/income")
def api_income():
    """Expected vs. actual income per source per month, plus late/short status."""
    n_months = min(int(request.args.get("months", 4)), 12)
    today = date.today()
    months = []
    d = today.replace(day=1)
    for _ in range(n_months):
        months.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    months.reverse()
    start_ts = int(datetime.strptime(months[0] + "-01", "%Y-%m-%d").timestamp())

    conn = connect()
    sources = conn.execute("SELECT * FROM income_sources WHERE active=1").fetchall()
    # Candidate income transactions: inflows to cash accounts, not transfers
    rows = conn.execute(TXN_SELECT + """
        WHERE t.posted >= ? AND t.amount > 0 AND a.hidden=0
          AND a.type IN ('checking','savings','unknown')
          AND (c.kind IS NULL OR c.kind != 'transfer')
    """, (start_ts,)).fetchall()

    now = int(time.time())
    out_sources, assigned = [], set()
    for s in sources:
        pat = s["pattern"].lower()
        mine = [r for r in rows if pat in (r["description"] or "").lower()
                or pat in (r["payee"] or "").lower()]
        for r in mine:
            assigned.add(r["id"])
        by_month = defaultdict(float)
        for r in mine:
            by_month[datetime.fromtimestamp(r["posted"]).strftime("%Y-%m")] += r["amount"]
        expected_monthly = round(s["amount"] * CADENCE_MONTHLY_FACTOR[s["cadence"]], 2)
        last = max(mine, key=lambda r: r["posted"], default=None)
        status, detail = "ok", ""
        if s["cadence"] != "irregular":
            gap = CADENCE_GAP_DAYS[s["cadence"]] * 86400
            if last is None:
                status, detail = "late", "No payment found in the loaded history"
            elif now - last["posted"] > gap * 1.7:
                days = int((now - last["posted"]) / 86400)
                status, detail = "late", f"Last payment {days} days ago (expected every ~{int(gap/86400)})"
            elif last["amount"] < 0.85 * s["amount"]:
                status, detail = "short", (f"Last payment ${last['amount']:,.2f} vs. expected "
                                           f"${s['amount']:,.2f}")
        out_sources.append({
            "id": s["id"], "name": s["name"], "pattern": s["pattern"],
            "amount": s["amount"], "cadence": s["cadence"],
            "expected_monthly": expected_monthly,
            "monthly": {m: round(by_month.get(m, 0), 2) for m in months},
            "status": status, "status_detail": detail,
            "last_date": datetime.fromtimestamp(last["posted"]).strftime("%Y-%m-%d") if last else None,
            "last_amount": last["amount"] if last else None,
        })

    other_by_month = defaultdict(float)
    other_txns = []
    for r in rows:
        if r["id"] in assigned:
            continue
        other_by_month[datetime.fromtimestamp(r["posted"]).strftime("%Y-%m")] += r["amount"]
        other_txns.append(r)
    biggest_other = sorted(other_txns, key=lambda r: -r["amount"])[:8]
    conn.close()
    return jsonify({
        "months": months,
        "sources": out_sources,
        "other": {m: round(other_by_month.get(m, 0), 2) for m in months},
        "other_examples": [{"date": datetime.fromtimestamp(r["posted"]).strftime("%Y-%m-%d"),
                            "description": r["description"], "amount": r["amount"]}
                           for r in biggest_other],
    })


# ---------- investment contribution attribution ----------

def investment_flows(conn, span_start, span_end):
    """Attribute investment/retirement contributions to destinations.

    Two channels:
      1. Cash-account outflows categorized 'Investment Contribution' (e.g. Schwab
         MoneyLink from checking) — destination resolved by pairing with the matching
         inflow in a retirement/investment account, falling back to the description.
      2. Payroll-direct contributions that never touch a cash account: 401(k)
         deferrals reported inside the 401(k) feed, and paycheck splits deposited
         straight into the Roth (paycheck-split transfers in the IRA's own feed).

    Returns (dest_totals dict, payroll_direct_total, cash_total) for the span."""
    cash_rows = conn.execute(TXN_SELECT + """
        WHERE t.posted BETWEEN ? AND ? AND a.hidden=0
          AND a.type IN ('checking','savings','unknown')
          AND c.name = 'Investment Contribution'
    """, (span_start, span_end)).fetchall()
    inv_rows = conn.execute("""
        SELECT t.id, t.posted, t.amount, t.description, a.name AS acct_name, a.type AS acct_type
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.hidden=0 AND a.type IN ('retirement','investment')
    """).fetchall()
    cash_all = conn.execute("""
        SELECT t.amount, t.posted FROM transactions t JOIN accounts a ON a.id=t.account_id
        WHERE a.hidden=0 AND a.type IN ('checking','savings','unknown')
    """).fetchall()

    def dest_label(acct_name):
        nm = (acct_name or "").lower()
        if "roth" in nm or "ira" in nm:
            return "Roth IRA"
        if "401" in nm:
            return "401(k)"
        return "Brokerage & crypto"

    dest, used = defaultdict(float), set()
    for r in cash_rows:
        # Outflow to an investment account adds to its bucket; an inflow back
        # (withdrawal from the investment side) nets against it.
        amt = -r["amount"]
        partner = next((p for p in inv_rows if p["id"] not in used
                        and abs(p["amount"] + r["amount"]) < 0.01
                        and p["amount"] * r["amount"] < 0
                        and abs(p["posted"] - r["posted"]) <= 7 * 86400), None)
        if partner:
            used.add(partner["id"])
            dest[dest_label(partner["acct_name"])] += amt
        else:
            d = (r["description"] or "").lower()
            if "schwab" in d or "moneylink" in d or "roth" in d or "ira" in d:
                dest["Roth IRA"] += amt
            elif "401" in d:
                dest["401(k)"] += amt
            elif "robinhood" in d or "crypto" in d:
                dest["Brokerage & crypto"] += amt
            else:
                dest["Investments"] += amt
    cash_total = sum(-r["amount"] for r in cash_rows)

    def has_cash_partner(amt, posted):
        return any(abs(-c["amount"] - amt) < 0.01 and abs(c["posted"] - posted) <= 5 * 86400
                   for c in cash_all)

    payroll = 0.0
    payroll_by_dest = defaultdict(float)
    for p in inv_rows:
        if p["acct_type"] != "retirement" or not (span_start <= p["posted"] <= span_end):
            continue
        d = (p["description"] or "").lower()
        if "contribution" in d:
            # 401(k) feeds sometimes report contributions with an inverted sign
            amt = abs(p["amount"])
            if not has_cash_partner(amt, p["posted"]):
                dest[dest_label(p["acct_name"])] += amt
                payroll_by_dest[dest_label(p["acct_name"])] += amt
                payroll += amt
        elif p["amount"] > 0 and p["id"] not in used and (
                d.startswith("tfr") or "transfer" in d or "deposit" in d):
            if not has_cash_partner(p["amount"], p["posted"]):
                dest[dest_label(p["acct_name"])] += p["amount"]
                payroll_by_dest[dest_label(p["acct_name"])] += p["amount"]
                payroll += p["amount"]
    return dest, payroll, cash_total, payroll_by_dest


# ---------- money flow (sankey) ----------

def _flow_span(first_d, n_months, sel):
    """Shared month-window logic for /api/flow and /api/flow/detail."""
    today = date.today()
    months = []
    m = (today.replace(day=1) - timedelta(days=1)).replace(day=1)  # last full month
    for _ in range(n_months):
        if m >= first_d.replace(day=1) and not (m.year == first_d.year and m.month == first_d.month
                                                and first_d.day > 3):
            months.append(m)
        m = (m - timedelta(days=1)).replace(day=1)
    months = sorted(months)
    if not months:
        months = [(today.replace(day=1) - timedelta(days=1)).replace(day=1)]
    available = [m.strftime("%Y-%m") for m in months] + [today.strftime("%Y-%m")]
    if sel in available:
        months = [datetime.strptime(sel, "%Y-%m").date().replace(day=1)]
    span_start = int(datetime(months[0].year, months[0].month, 1).timestamp())
    next_m = (months[-1].replace(day=28) + timedelta(days=8)).replace(day=1)
    span_end = int(datetime(next_m.year, next_m.month, 1).timestamp()) - 1
    return months, available, span_start, span_end, len(months)



@app.get("/api/flow")
def api_flow():
    """Average monthly money flow: income sources -> pool -> spending categories.

    Uses only fully-covered calendar months (excludes the current partial month and
    months that start before the oldest loaded transaction) so averages aren't skewed."""
    n_months = min(int(request.args.get("months", 6)), 12)
    today = date.today()
    conn = connect()

    first = data_coverage_start(conn)
    if not first:
        conn.close()
        return jsonify({"months": [], "incomes": [], "outflows": [], "invested": 0})
    first_d = date.fromtimestamp(first)

    sel = request.args.get("month")
    months, available, span_start, span_end, n = _flow_span(first_d, n_months, sel)

    # Fetch with a few days' buffer, then filter on EFFECTIVE date: rent paid in
    # the last days of a month is next month's rent (two rents can post in one
    # calendar month), so Housing payments on day >= 28 count toward the month
    # they pay for.
    rows = conn.execute(TXN_SELECT + """
        WHERE t.posted BETWEEN ? AND ? AND a.hidden=0
          AND a.type IN ('checking','savings','credit','unknown')
          AND (c.kind IS NULL OR c.kind != 'transfer')
    """, (span_start - 5 * 86400, span_end)).fetchall()

    def effective_ts(r):
        if r["cat_name"] == "Housing" and r["amount"] < 0:
            d = date.fromtimestamp(r["posted"])
            if d.day >= 28:
                nxt = (d.replace(day=28) + timedelta(days=8)).replace(day=1)
                return int(datetime(nxt.year, nxt.month, 1, 12).timestamp())
        return r["posted"]

    rows = [r for r in rows if span_start <= effective_ts(r) <= span_end]

    sources = conn.execute("SELECT * FROM income_sources WHERE active=1").fetchall()
    incomes = defaultdict(float)
    spend = defaultdict(float)
    for r in rows:
        if r["amount"] > 0:
            if r["cat_kind"] == "expense" or r["acct_type"] == "credit":
                spend[r["cat_name"] or "Uncategorized"] -= r["amount"]  # refunds net out
            else:
                matched = next((s["name"] for s in sources
                                if s["pattern"].lower() in (r["description"] or "").lower()
                                or s["pattern"].lower() in (r["payee"] or "").lower()), None)
                if matched:
                    incomes[matched] += r["amount"]
                elif r["cat_kind"] == "income" and r["cat_name"]:
                    incomes[r["cat_name"]] += r["amount"]
                else:
                    incomes["Other income"] += r["amount"]
        else:
            spend[r["cat_name"] or "Uncategorized"] += -r["amount"]

    dest, payroll_direct, _cash_total, payroll_by_dest = investment_flows(conn, span_start, span_end)
    # Employer 401(k) match. Two feed conventions, chosen via settings:
    #   k401_match_share: the feed's 'contribution' line ALREADY includes the match —
    #     this fraction of it is employer money (e.g. a 5% deferral + 4% match
    #     plan -> match share = 4/9). Splits the line; adds nothing.
    #   k401_match_ratio: line is employee-only; match = ratio x line, added on top.
    line = payroll_by_dest.get("401(k)", 0)
    match_share = float(get_setting(conn, "k401_match_share") or 0)
    add_ratio = float(get_setting(conn, "k401_match_ratio") or 0)
    if match_share > 0:
        employer_match = round(line * match_share, 2)
        payroll_direct -= employer_match          # income node shows employee-only
    elif add_ratio > 0:
        employer_match = round(line * add_ratio, 2)
        dest["401(k)"] += employer_match
    else:
        employer_match = 0.0
    conn.close()

    # A category that nets negative (refunds/winnings exceeded spending) is money
    # coming in — show it on the income side rather than silently dropping it.
    for k in [k for k, v in spend.items() if v < 0]:
        incomes[f"{k} (net refunds)"] += -spend.pop(k)

    income_avg = [{"name": k, "monthly": round(v / n, 2)} for k, v in incomes.items() if v / n >= 1]
    spend_avg = [{"name": k, "monthly": round(v / n, 2)} for k, v in spend.items() if v / n >= 1]
    income_avg.sort(key=lambda x: -x["monthly"])
    spend_avg.sort(key=lambda x: -x["monthly"])
    invest_avg = sorted(
        [{"name": k, "monthly": round(v / n, 2)} for k, v in dest.items() if v / n >= 1],
        key=lambda x: -x["monthly"])

    # Payroll-direct contributions are earned income that never hits checking —
    # they appear on both sides so the diagram balances.
    bankroll_net = conn2 = None
    from db import connect as _c
    conn2 = _c()
    bankroll_net = conn2.execute("""
        SELECT COALESCE(SUM(-t.amount), 0) AS s FROM transactions t
        JOIN accounts a ON a.id=t.account_id JOIN categories c ON c.id=t.category_id
        WHERE t.posted BETWEEN ? AND ? AND a.hidden=0
          AND a.type IN ('checking','savings','credit','unknown')
          AND c.name = 'Gambling & Betting'
    """, (span_start, span_end)).fetchone()["s"]
    conn2.close()
    bankroll_avg = round(bankroll_net / n, 2)

    payroll_avg = round(payroll_direct / n, 2)
    match_avg = round(employer_match / n, 2)
    total_in = sum(x["monthly"] for x in income_avg) + payroll_avg + match_avg \
        + (-bankroll_avg if bankroll_avg < 0 else 0)
    total_out = sum(x["monthly"] for x in spend_avg) + sum(x["monthly"] for x in invest_avg) \
        + (bankroll_avg if bankroll_avg > 0 else 0)
    net = round(total_in - total_out, 2)

    return jsonify({
        "months": [m.strftime("%Y-%m") for m in months],
        "available_months": available,
        "selected": sel if sel in available else "avg",
        "incomes": income_avg,
        "payroll_direct": payroll_avg,
        "employer_match": match_avg,
        "bankroll_net": bankroll_avg,
        "outflows": spend_avg,
        "investments": invest_avg,
        "net": net,   # positive: surplus stays in accounts; negative: drawn from balances
    })


@app.get("/api/flow/detail")
def api_flow_detail():
    """Drill-down for a Sankey node: the transactions behind it, a sub-breakdown
    (for group hubs), or a plain-English explanation (for computed nodes)."""
    node = (request.args.get("node") or "").strip()
    kind = request.args.get("kind", "category")
    conn = connect()
    first = data_coverage_start(conn)
    first_d = date.fromtimestamp(first) if first else date.today()
    months, _avail, span_start, span_end, n = _flow_span(
        first_d, min(int(request.args.get("months", 6)), 12), request.args.get("month"))

    rows = conn.execute(TXN_SELECT + """
        WHERE t.posted BETWEEN ? AND ? AND a.hidden=0
          AND a.type IN ('checking','savings','credit','unknown')
        ORDER BY ABS(t.amount) DESC
    """, (span_start, span_end)).fetchall()
    sources = conn.execute("SELECT * FROM income_sources WHERE active=1").fetchall()

    def pack(r):
        return {"date": date.fromtimestamp(r["posted"]).isoformat(),
                "description": r["description"][:70], "amount": r["amount"],
                "account": r["acct_name"], "category": r["cat_name"] or "—"}

    def matches_source(r):
        return next((s["name"] for s in sources
                     if s["pattern"].lower() in (r["description"] or "").lower()
                     or s["pattern"].lower() in (r["payee"] or "").lower()), None)

    title, txns, breakdown, explanation = node, [], None, None
    base = node.replace(" (net refunds)", "")

    if kind == "total":
        txns = [r for r in rows if r["amount"] > 0 and (r["cat_kind"] or "") != "transfer"
                and not (r["cat_kind"] == "expense" or r["acct_type"] == "credit")]
        explanation = ("Every inflow counted as income in this window — paychecks, matched income "
                       "sources, income-category transactions, and unmatched deposits to cash accounts. "
                       "Payroll deferrals and employer match are added on top (see those nodes).")
    elif kind == "group":
        agg = {}
        for r in rows:
            if (r["cat_kind"] or "") == "transfer" or r["amount"] >= 0:
                continue
            agg[r["cat_name"] or "Uncategorized"] = agg.get(r["cat_name"] or "Uncategorized", 0) - r["amount"]
        breakdown = sorted([{"name": k, "amount": round(v / n, 2)} for k, v in agg.items()],
                           key=lambda x: -x["amount"])
        explanation = "Per-month averages by category. Click a category leaf on the chart for its transactions."
    elif node.startswith(("Deficit", "Overspend")):
        explanation = ("Not a transaction — arithmetic. You spent/invested more than came in during "
                       "this window; the gap shows as the hatched notch. In practice it means account "
                       "balances ended lower (or card balances higher) than they started — there's no "
                       "single 'source' it came from.")
    elif node.startswith("Leftover"):
        explanation = ("Not a transaction — arithmetic. Income exceeded spending + investing in this "
                       "window; the surplus simply stayed in checking/savings.")
    elif node.startswith("Employer 401"):
        txns = [r for r in conn.execute("""SELECT t.*, a.name acct_name, c.name cat_name, c.kind cat_kind,
                    a.type acct_type FROM transactions t JOIN accounts a ON a.id=t.account_id
                    LEFT JOIN categories c ON c.id=t.category_id
                    WHERE a.type='retirement' AND LOWER(t.description) LIKE '%contribution%'
                      AND t.posted BETWEEN ? AND ?""", (span_start, span_end))]
        explanation = ("Computed, not separate transactions: Fidelity reports one combined contribution "
                       "line per paycheck; per your configured plan, the stored match share of each "
                       "line below is employer money (Settings -> retirement plan).")
    elif node.startswith("Payroll deferrals"):
        txns = [r for r in conn.execute("""SELECT t.*, a.name acct_name, c.name cat_name, c.kind cat_kind,
                    a.type acct_type FROM transactions t JOIN accounts a ON a.id=t.account_id
                    LEFT JOIN categories c ON c.id=t.category_id
                    WHERE a.type='retirement' AND t.posted BETWEEN ? AND ?
                      AND (LOWER(t.description) LIKE '%contribution%' OR LOWER(t.description) LIKE 'tfr%')
                    ORDER BY t.posted DESC""", (span_start, span_end))]
        explanation = ("Money that went to retirement straight from your paycheck, before it ever reached "
                       "checking: 401(k) contribution lines (5/9 of each is your deferral) and direct "
                       "paycheck splits into the Roth.")
    elif "bankroll" in node.lower():
        txns = [r for r in rows if r["cat_name"] == "Gambling & Betting"]
        explanation = ("Bank-side moves to/from betting platforms in this window. Negative = deposit to "
                       "a platform, positive = withdrawal back to the bank.")
    elif kind == "source":
        if any(s["name"] == node for s in sources):
            txns = [r for r in rows if r["amount"] > 0 and matches_source(r) == node]
        elif node == "Other income":
            txns = [r for r in rows if r["amount"] > 0 and (r["cat_kind"] or "") not in ("transfer", "expense")
                    and r["cat_kind"] != "income" and r["acct_type"] != "credit" and not matches_source(r)]
            explanation = ("Inflows to cash accounts that matched no income source and carry no income "
                           "category — the catch-all bucket. If something here recurs, give it a rule or "
                           "an income source and it will get its own name.")
        elif node.endswith("(net refunds)"):
            txns = [r for r in rows if r["cat_name"] == base]
            explanation = (f"The '{base}' category netted POSITIVE this window — refunds/reimbursements "
                           "exceeded charges — so the net appears as money in. Both sides are listed.")
        else:  # an income category node, e.g. "Other Income", "Interest & Dividends"
            txns = [r for r in rows if r["cat_name"] == node and r["amount"] > 0]
    else:  # category leaf
        if node == "Other spending":
            agg = {}
            for r in rows:
                if r["amount"] < 0 and (r["cat_kind"] or "") != "transfer":
                    agg[r["cat_name"] or "Uncategorized"] = agg.get(r["cat_name"] or "Uncategorized", 0) - r["amount"]
            top = set(sorted(agg, key=lambda x: -agg[x])[:12])
            txns = [r for r in rows if (r["cat_name"] or "Uncategorized") not in top
                    and r["amount"] < 0 and (r["cat_kind"] or "") != "transfer"]
            explanation = "The small categories folded together to keep the chart readable."
        elif node in ("401(k)", "Roth IRA", "Brokerage & crypto", "Investments"):
            cash = [r for r in rows if r["cat_name"] == "Investment Contribution"]
            ret = [dict(r) for r in conn.execute("""SELECT t.*, a.name acct_name, c.name cat_name,
                       c.kind cat_kind, a.type acct_type FROM transactions t
                       JOIN accounts a ON a.id=t.account_id LEFT JOIN categories c ON c.id=t.category_id
                       WHERE a.type IN ('retirement','investment') AND t.posted BETWEEN ? AND ?
                         AND (LOWER(t.description) LIKE '%contribution%' OR LOWER(t.description) LIKE 'tfr%'
                              OR LOWER(t.description) LIKE '%deposit%')""", (span_start, span_end))]
            def dest(r):
                d, nm = (r["description"] or "").lower(), (r["acct_name"] or "").lower()
                hay = d + " " + nm
                if "roth" in hay or "ira" in hay or "schwab" in hay or "moneylink" in hay: return "Roth IRA"
                if "401" in hay: return "401(k)"
                return "Brokerage & crypto"
            txns = [r for r in cash if dest(r) == node] + [r for r in ret if dest(r) == node]
            explanation = ("Contributions routed to this destination: transfers from your bank plus any "
                           "payroll-direct lines in the account's own feed.")
        else:
            txns = [r for r in rows if (r["cat_name"] or "Uncategorized") == node
                    and (r["cat_kind"] or "") != "transfer"]

    conn.close()
    out = [pack(r) for r in txns[:120]]
    label = (f"{months[0].strftime('%b %Y')}" if n == 1
             else f"{months[0].strftime('%b %Y')} – {months[-1].strftime('%b %Y')}")
    return jsonify({"title": title, "window": label, "count": len(txns),
                    "truncated": len(txns) > 120, "explanation": explanation,
                    "breakdown": breakdown, "transactions": out,
                    "total": round(sum(t["amount"] for t in out), 2)})


# ---------- gambling bankroll ----------

GAMBLING_PLATFORMS = [
    ("betmgm", "BetMGM"), ("mgm*", "BetMGM"), ("kalshi", "Kalshi"), ("novig", "Novig"),
    ("parlayplay", "ParlayPlay"), ("polymarket", "Polymarket"), ("rebet", "Rebet"),
    ("betr", "BETR"), ("draftkings", "DraftKings"), ("from: draft", "DraftKings"),
    ("fanduel", "FanDuel"), ("prizepicks", "PrizePicks"), ("from: prize", "PrizePicks"),
    ("from: chalk", "Chalk"),
]


def platform_of(desc):
    d = (desc or "").lower()
    for pat, name in GAMBLING_PLATFORMS:
        if pat in d:
            return name
    return "Other"


def gambling_cash_txns(conn):
    """Bank-side bankroll movements: negative = deposit to a platform, positive = withdrawal."""
    return conn.execute(TXN_SELECT + """
        WHERE a.hidden=0 AND a.type IN ('checking','savings','credit','unknown')
          AND c.name = 'Gambling & Betting' ORDER BY t.posted
    """).fetchall()


def bankroll_reference(conn):
    """(snapshot_date_iso, amount) of the latest snapshot, or None."""
    r = conn.execute("SELECT date, amount FROM bankroll_snapshots ORDER BY date DESC LIMIT 1").fetchone()
    return (r["date"], r["amount"]) if r else None


def bankroll_estimate_now(conn):
    """Latest snapshot plus net transfers since; None if no snapshot exists."""
    ref = bankroll_reference(conn)
    if not ref:
        return None
    ref_ts = int(datetime.strptime(ref[0], "%Y-%m-%d").timestamp()) + 86399
    delta = sum(-t["amount"] for t in gambling_cash_txns(conn) if t["posted"] > ref_ts)
    return round(max(ref[1] + delta, 0), 2)


def parse_bets_csv(text):
    """Flexible bet-history CSV parser (Pikkit export or similar)."""
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return []
    header_i, cols = None, {}
    for i, row in enumerate(rows[:8]):
        low = [c.strip().lower() for c in row]
        has_stake = any(k in c for c in low for k in ("stake", "risk", "wager"))
        has_meta = any(k in c for c in low for k in ("odds", "result", "status", "book"))
        if has_stake and has_meta:
            header_i = i
            for j, c in enumerate(low):
                if any(k in c for k in ("settled", "placed", "date", "time")):
                    cols.setdefault("date", j)
                if "book" in c:
                    cols.setdefault("book", j)
                if any(k in c for k in ("description", "selection", "name", "event", "pick", "bet info")):
                    cols.setdefault("desc", j)
                if "odds" in c or c == "line":
                    cols.setdefault("odds", j)
                if any(k in c for k in ("stake", "risk", "wager")):
                    cols.setdefault("stake", j)
                if any(k in c for k in ("payout", "return", "winnings")) and "potential" not in c:
                    cols.setdefault("payout", j)
                if "profit" in c or c == "net":
                    cols.setdefault("profit", j)
                if any(k in c for k in ("result", "status", "outcome")):
                    cols.setdefault("result", j)
            break
    if header_i is None or "stake" not in cols:
        return []

    def num(row, key):
        if key not in cols or len(row) <= cols[key]:
            return None
        return _parse_amount(row[cols[key]])

    def txt(row, key):
        return row[cols[key]].strip() if key in cols and len(row) > cols[key] else ""

    out = []
    for row in rows[header_i + 1:]:
        stake = num(row, "stake")
        if stake is None or stake == 0:
            continue
        stake = abs(stake)
        ds = txt(row, "date")
        placed = _parse_date(ds)
        if placed is None:
            for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    placed = int(datetime.strptime(ds.split(".")[0].replace("Z", ""), fmt).timestamp())
                    break
                except ValueError:
                    continue
        result = txt(row, "result").lower()
        payout = num(row, "payout")
        profit = num(row, "profit")
        if profit is None:
            if any(k in result for k in ("pend", "open", "live")):
                profit = None
            elif payout is not None:
                profit = round(payout - stake, 2)
            elif any(k in result for k in ("lost", "loss")):
                profit = -stake
            elif any(k in result for k in ("push", "void", "cancel")):
                profit = 0.0
        out.append({"placed": placed, "book": txt(row, "book"), "description": txt(row, "desc"),
                    "odds": txt(row, "odds"), "stake": stake, "payout": payout,
                    "profit": profit, "result": result or ("pending" if profit is None else "")})
    return out


@app.post("/api/gambling/import")
def api_gambling_import():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file required"}), 400
    text = f.read().decode("utf-8-sig", errors="replace")
    bets = parse_bets_csv(text)
    if not bets:
        return jsonify({"error": "No bets found. The CSV needs a header row with a stake/risk/wager "
                                 "column plus odds/result/book columns (Pikkit's export works)."}), 400
    conn = connect()
    now = int(time.time())
    added = updated = 0
    for b in bets:
        bid = hashlib.md5(f"{b['placed']}|{b['book']}|{b['description']}|{b['stake']:.2f}".encode()).hexdigest()[:20]
        existing = conn.execute("SELECT result FROM bets WHERE id=?", (bid,)).fetchone()
        if existing is None:
            conn.execute("""INSERT INTO bets (id, placed, book, description, odds, stake, payout,
                            profit, result, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (bid, b["placed"], b["book"], b["description"], b["odds"], b["stake"],
                          b["payout"], b["profit"], b["result"], now))
            added += 1
        elif "pend" in (existing["result"] or "") and "pend" not in b["result"]:
            conn.execute("UPDATE bets SET payout=?, profit=?, result=? WHERE id=?",
                         (b["payout"], b["profit"], b["result"], bid))
            updated += 1
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "added": added, "settled_updates": updated})


@app.get("/api/gambling/snapshots")
def api_gambling_snapshots():
    conn = connect()
    rows = conn.execute("SELECT * FROM bankroll_snapshots ORDER BY date DESC LIMIT 20").fetchall()
    conn.close()
    return jsonify([{k: r[k] for k in r.keys()} for r in rows])


@app.post("/api/gambling/snapshots")
def api_gambling_snapshot_create():
    b = request.get_json(force=True)
    try:
        amount = float(b.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"error": "amount required"}), 400
    d = b.get("date") or date.today().isoformat()
    conn = connect()
    conn.execute("INSERT INTO bankroll_snapshots (date, amount, note) VALUES (?,?,?) "
                 "ON CONFLICT(date) DO UPDATE SET amount=excluded.amount, note=excluded.note",
                 (d, amount, b.get("note", "")))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.get("/api/gambling/summary")
def api_gambling_summary():
    conn = connect()
    txns = gambling_cash_txns(conn)
    deposited = sum(-t["amount"] for t in txns if t["amount"] < 0)
    withdrawn = sum(t["amount"] for t in txns if t["amount"] > 0)
    monthly = defaultdict(lambda: {"deposited": 0.0, "withdrawn": 0.0})
    plat = defaultdict(lambda: {"deposited": 0.0, "withdrawn": 0.0})
    for t in txns:
        mk = datetime.fromtimestamp(t["posted"]).strftime("%Y-%m")
        p = platform_of(t["description"])
        if t["amount"] < 0:
            monthly[mk]["deposited"] += -t["amount"]
            plat[p]["deposited"] += -t["amount"]
        else:
            monthly[mk]["withdrawn"] += t["amount"]
            plat[p]["withdrawn"] += t["amount"]

    ref = bankroll_reference(conn)
    est = bankroll_estimate_now(conn)
    # Implied P/L over the loaded bank history: what's on platform now, plus what
    # came back, minus what went in. Only meaningful once a snapshot exists.
    implied = round(est + withdrawn - deposited, 2) if est is not None else None

    bets = conn.execute("SELECT * FROM bets").fetchall()
    bets_out = None
    if bets:
        settled = [b for b in bets if b["profit"] is not None]
        pending = [b for b in bets if b["profit"] is None]
        staked = sum(b["stake"] for b in settled)
        profit = sum(b["profit"] for b in settled)
        wins = sum(1 for b in settled if b["profit"] > 0)
        by_month = defaultdict(lambda: {"profit": 0.0, "staked": 0.0})
        by_book = defaultdict(lambda: {"count": 0, "staked": 0.0, "profit": 0.0})
        for b in settled:
            mk = datetime.fromtimestamp(b["placed"]).strftime("%Y-%m") if b["placed"] else "unknown"
            by_month[mk]["profit"] += b["profit"]
            by_month[mk]["staked"] += b["stake"]
            bk = b["book"] or "Unknown"
            by_book[bk]["count"] += 1
            by_book[bk]["staked"] += b["stake"]
            by_book[bk]["profit"] += b["profit"]
        bets_out = {
            "count": len(settled), "pending": len(pending),
            "pending_stake": round(sum(b["stake"] for b in pending), 2),
            "staked": round(staked, 2), "profit": round(profit, 2),
            "roi_pct": round(100 * profit / staked, 1) if staked else None,
            "win_rate": round(100 * wins / len(settled), 1) if settled else None,
            "by_month": [{"month": m, "profit": round(v["profit"], 2), "staked": round(v["staked"], 2)}
                         for m, v in sorted(by_month.items()) if m != "unknown"],
            "by_book": sorted([{"book": k, **{x: round(v[x], 2) for x in ("staked", "profit")},
                                "count": v["count"]} for k, v in by_book.items()],
                              key=lambda x: -x["staked"]),
        }
    conn.close()
    return jsonify({
        "bankroll": {"estimate": est, "snapshot_date": ref[0] if ref else None,
                     "snapshot_amount": ref[1] if ref else None},
        "deposited": round(deposited, 2), "withdrawn": round(withdrawn, 2),
        "net_funded": round(deposited - withdrawn, 2),
        "implied_pl": implied,
        "monthly": [{"month": m, **{k: round(v[k], 2) for k in v}} for m, v in sorted(monthly.items())],
        "platforms": sorted([{"name": k, **{x: round(v[x], 2) for x in v},
                              "net": round(v["deposited"] - v["withdrawn"], 2)}
                             for k, v in plat.items()], key=lambda x: -x["deposited"]),
        "bets": bets_out,
    })


# ---------- demo data ----------

@app.post("/api/demo/seed")
def api_demo_seed():
    n = demo_data.seed_demo()
    return jsonify({"ok": True, "transactions": n})


@app.post("/api/demo/clear")
def api_demo_clear():
    demo_data.clear_demo()
    return jsonify({"ok": True})


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5177))
    try:
        app.run(host="127.0.0.1", port=port, debug=False)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"\nPort {port} is already taken — the dashboard is probably already running "
                  f"at http://localhost:{port}.\nTo replace that instance, run: ./run.sh")
        else:
            raise
