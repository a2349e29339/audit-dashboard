"""Seed realistic demo data so the dashboard is usable before real accounts are linked.

Everything created here is flagged is_demo=1 and can be removed with one click
in Settings once real SimpleFIN data is flowing.
"""
import random
import time
from datetime import date, datetime, timedelta

from db import connect, categorize_transaction

RNG = random.Random(42)

ACCOUNTS = [
    # (id, org, name, type, starting balance ~12mo ago)
    ("demo:checking",  "Demo Bank",       "Everyday Checking", "checking",   4200.0),
    ("demo:savings",   "Demo Bank",       "High-Yield Savings", "savings",   18500.0),
    ("demo:credit",    "Demo Card Co",    "Rewards Visa",      "credit",     -640.0),
    ("demo:roth",      "Demo Brokerage",  "Roth IRA",          "retirement", 31200.0),
    ("demo:401k",      "Demo Retirement", "401(k)",            "retirement", 74800.0),
]

MERCHANTS = {
    "groceries": ["TRADER JOE'S #112", "WHOLEFDS MKT 10214", "SAFEWAY STORE 1442", "COSTCO WHSE #487"],
    "dining":    ["STARBUCKS 8842", "CHIPOTLE 1123", "DOORDASH*THAI HOUSE", "SWEETGREEN SOMA",
                  "BLUE BOTTLE COFFEE", "LOCAL RESTAURANT GRP"],
    "transport": ["UBER TRIP", "LYFT RIDE", "CHEVRON 0091", "CITY PARKING METER"],
    "shopping":  ["AMZN MKTP US*2K4", "TARGET T-1893", "BEST BUY #442", "AMAZON.COM*ORDER"],
    "subs":      ["NETFLIX.COM", "SPOTIFY USA", "APPLE.COM/BILL", "HULU 84123"],
    "health":    ["CVS/PHARMACY #9912", "WALGREENS #4471", "BAY AREA GYM MEMBERSHIP"],
    "fun":       ["AMC THEATRES 0442", "STEAMGAMES.COM", "TICKETMASTER EVENT"],
}


def month_starts(n_months: int):
    today = date.today().replace(day=1)
    months = []
    d = today
    for _ in range(n_months):
        months.append(d)
        d = (d - timedelta(days=1)).replace(day=1)
    return list(reversed(months))


def ts(d: date, hour=12) -> int:
    return int(datetime(d.year, d.month, d.day, hour).timestamp())


def seed_demo():
    conn = connect()
    now = int(time.time())
    for aid, org, name, typ, bal in ACCOUNTS:
        conn.execute(
            """INSERT OR REPLACE INTO accounts
               (id, org_name, name, type, balance, balance_date, is_demo, last_sync)
               VALUES (?,?,?,?,?,?,1,?)""",
            (aid, org, name, typ, bal, now, now))

    txns = []  # (id, account_id, date, amount, description)
    seq = 0

    def add(acct, d, amount, desc):
        nonlocal seq
        seq += 1
        txns.append((f"demo:txn:{seq}", acct, d, round(amount, 2), desc))

    months = month_starts(13)
    today = date.today()

    for m in months:
        # --- Income: biweekly-ish payroll into checking
        for payday in (7, 22):
            d = m.replace(day=payday)
            if d > today:
                continue
            add("demo:checking", d, 3120.0 + RNG.uniform(-15, 15), "ACME CORP DIRECT DEP PAYROLL")
        # Savings interest
        d = m.replace(day=28)
        if d <= today:
            add("demo:savings", d, 62.0 + RNG.uniform(-8, 20), "INTEREST PAYMENT")
        # --- Fixed bills from checking
        bills = [
            (1,  -2350.0, "PROPMGMT RENT PAYMENT"),
            (5,  -89.0,   "PG&E ELECTRIC AUTOPAY"),
            (6,  -75.0,   "XFINITY INTERNET"),
            (9,  -68.0,   "T-MOBILE AUTOPAY"),
            (12, -142.0,  "GEICO INSURANCE PREM"),
        ]
        for day, amt, desc in bills:
            d = m.replace(day=day)
            if d <= today:
                add("demo:checking", d, amt + RNG.uniform(-4, 4) * (0 if day == 1 else 1), desc)
        # --- Transfers: savings + retirement contributions from checking
        d = m.replace(day=8)
        if d <= today:
            add("demo:checking", d, -500.0, "ONLINE TRANSFER TO SAVINGS XXXX2211")
            add("demo:savings", d, 500.0, "ONLINE TRANSFER FROM CHECKING XXXX8804")
        d = m.replace(day=16)
        if d <= today:
            add("demo:checking", d, -541.66, "VANGUARD BUY ROTH CONTRIBUTION")
        # --- Credit card autopay (previous cycle)
        d = m.replace(day=20)
        if d <= today:
            pay_amt = round(1350 + RNG.uniform(-250, 350), 2)
            add("demo:checking", d, -pay_amt, "REWARDS VISA EPAY AUTOPAY")
            add("demo:credit", d, pay_amt, "PAYMENT THANK YOU - AUTOPAY")
        # --- Variable spending on the credit card
        def spread(pool, count, lo, hi):
            for _ in range(count):
                day = RNG.randint(1, 28)
                d = m.replace(day=day)
                if d <= today:
                    add("demo:credit", d, -RNG.uniform(lo, hi), RNG.choice(MERCHANTS[pool]))
        spread("groceries", 6, 38, 145)
        spread("dining", 9, 9, 68)
        spread("transport", 5, 8, 52)
        spread("shopping", 4, 15, 180)
        spread("health", 1, 12, 90)
        spread("fun", 2, 12, 85)
        for day, sub, amt in ((3, "NETFLIX.COM", 15.49), (11, "SPOTIFY USA", 11.99),
                              (14, "APPLE.COM/BILL", 2.99), (18, "BAY AREA GYM MEMBERSHIP", 89.0)):
            d = m.replace(day=day)
            if d <= today:
                add("demo:credit", d, -amt, sub)

    # A few "review queue" style anomalies in the last 45 days
    recent = today - timedelta(days=12)
    add("demo:credit", recent, -840.0, "ELECTRONICS OUTLET ONLINE")            # unusually large
    dup_day = today - timedelta(days=6)
    add("demo:credit", dup_day, -64.20, "LOCAL RESTAURANT GRP")                 # duplicate pair
    add("demo:credit", dup_day, -64.20, "LOCAL RESTAURANT GRP")
    add("demo:credit", today - timedelta(days=3), -29.99, "UNKWN DIGITAL SVC 8332")  # new merchant
    add("demo:checking", today - timedelta(days=9), -35.00, "ATM FEE NON-NETWORK")

    # Insert transactions with rule-based categories
    for tid, acct, d, amount, desc in txns:
        row = {"description": desc, "payee": "", "memo": ""}
        cat = categorize_transaction(conn, row)
        conn.execute(
            """INSERT OR REPLACE INTO transactions
               (id, account_id, posted, amount, description, payee, memo, pending,
                category_id, category_source, reviewed, is_demo, created_at)
               VALUES (?,?,?,?,?,'','',0,?,?,0,1,?)""",
            (tid, acct, ts(d), amount, desc, cat, "rule" if cat else None, now))

    # --- Balance history (monthly snapshots) for net-worth line
    hist = {
        "demo:checking": 4200.0, "demo:savings": 18500.0, "demo:credit": -640.0,
        "demo:roth": 31200.0, "demo:401k": 74800.0,
    }
    for i, m in enumerate(months):
        # cash accounts drift with activity; retirement grows with contributions + market
        hist["demo:checking"] += RNG.uniform(-150, 250)
        hist["demo:savings"] += 560 + RNG.uniform(-10, 30)
        hist["demo:credit"] = -(600 + RNG.uniform(-200, 400))
        hist["demo:roth"] = hist["demo:roth"] * (1 + RNG.uniform(-0.015, 0.03)) + 541.66
        hist["demo:401k"] = hist["demo:401k"] * (1 + RNG.uniform(-0.015, 0.03)) + 875.0
        for aid in hist:
            conn.execute("INSERT OR REPLACE INTO balance_history VALUES (?,?,?)",
                         (aid, m.isoformat(), round(hist[aid], 2)))
    # Set current balances to the last snapshot
    for aid in hist:
        conn.execute("UPDATE accounts SET balance=? WHERE id=?", (round(hist[aid], 2), aid))

    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM transactions WHERE is_demo=1").fetchone()[0]
    conn.close()
    return n


def clear_demo():
    conn = connect()
    conn.execute("DELETE FROM balance_history WHERE account_id IN (SELECT id FROM accounts WHERE is_demo=1)")
    conn.execute("DELETE FROM transactions WHERE is_demo=1")
    conn.execute("DELETE FROM accounts WHERE is_demo=1")
    conn.commit()
    conn.close()
