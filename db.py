"""SQLite schema + connection helpers for the audit dashboard."""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "audit.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id            TEXT PRIMARY KEY,          -- simplefin id or demo:<slug>
    org_name      TEXT,
    name          TEXT,
    currency      TEXT DEFAULT 'USD',
    balance       REAL DEFAULT 0,
    available     REAL,
    balance_date  INTEGER,
    -- user-assigned; drives cash-flow vs net-worth treatment
    type          TEXT DEFAULT 'unknown',    -- checking|savings|credit|retirement|investment|unknown
    hidden        INTEGER DEFAULT 0,
    is_demo       INTEGER DEFAULT 0,
    last_sync     INTEGER
);

CREATE TABLE IF NOT EXISTS balance_history (
    account_id TEXT NOT NULL,
    date       TEXT NOT NULL,                -- YYYY-MM-DD
    balance    REAL NOT NULL,
    PRIMARY KEY (account_id, date)
);

CREATE TABLE IF NOT EXISTS categories (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT UNIQUE NOT NULL,
    kind   TEXT NOT NULL DEFAULT 'expense'   -- expense|income|transfer
);

CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT NOT NULL,               -- substring, case-insensitive
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    priority    INTEGER DEFAULT 100,         -- lower wins
    active      INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,        -- simplefin id (scoped by account) or demo id
    account_id      TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    posted          INTEGER NOT NULL,        -- unix ts
    amount          REAL NOT NULL,           -- negative = outflow
    description     TEXT DEFAULT '',
    payee           TEXT DEFAULT '',
    memo            TEXT DEFAULT '',
    pending         INTEGER DEFAULT 0,
    category_id     INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    category_source TEXT,                    -- 'rule' | 'manual' | NULL
    reviewed        INTEGER DEFAULT 0,
    is_demo         INTEGER DEFAULT 0,
    created_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_txn_posted  ON transactions(posted);
CREATE INDEX IF NOT EXISTS idx_txn_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_txn_cat     ON transactions(category_id);

CREATE TABLE IF NOT EXISTS income_sources (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    pattern  TEXT NOT NULL,                  -- substring matched against description/payee
    amount   REAL NOT NULL,                  -- expected amount per payment (or per month if irregular)
    cadence  TEXT NOT NULL DEFAULT 'monthly',-- weekly|biweekly|semimonthly|monthly|irregular
    active   INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS holdings (
    account_id   TEXT NOT NULL,
    symbol       TEXT DEFAULT '',
    description  TEXT DEFAULT '',
    shares       REAL,
    market_value REAL,
    purchase_price REAL,
    updated      INTEGER,
    PRIMARY KEY (account_id, symbol, description)
);

CREATE TABLE IF NOT EXISTS bets (
    id        TEXT PRIMARY KEY,              -- hash of placed|book|description|stake
    placed    INTEGER,                       -- unix ts
    book      TEXT DEFAULT '',
    description TEXT DEFAULT '',
    odds      TEXT DEFAULT '',
    stake     REAL,
    payout    REAL,                          -- total returned (0 for a loss)
    profit    REAL,                          -- NULL while pending
    result    TEXT DEFAULT '',               -- won|lost|push|pending|cashout|...
    imported_at INTEGER
);

CREATE TABLE IF NOT EXISTS bankroll_snapshots (
    date   TEXT PRIMARY KEY,                 -- YYYY-MM-DD
    amount REAL NOT NULL,
    note   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER,
    ok       INTEGER,
    message  TEXT,
    accounts INTEGER DEFAULT 0,
    added    INTEGER DEFAULT 0
);
"""

DEFAULT_CATEGORIES = [
    # (name, kind)
    ("Salary", "income"), ("Interest & Dividends", "income"), ("Other Income", "income"),
    ("Housing", "expense"), ("Utilities", "expense"), ("Groceries", "expense"),
    ("Dining & Coffee", "expense"), ("Transport", "expense"), ("Shopping", "expense"),
    ("Health", "expense"), ("Insurance", "expense"), ("Subscriptions", "expense"),
    ("Entertainment", "expense"), ("Travel", "expense"), ("Fees & Charges", "expense"),
    ("Miscellaneous", "expense"), ("Unlinked Card Spending", "expense"),
    # Gambling money is a bankroll transfer, not consumption — true cost is bet P/L
    ("Gas & Fuel", "expense"), ("Gambling & Betting", "transfer"),
    ("Personal Care", "expense"), ("Cash & ATM", "expense"),
    ("Commuter Benefit", "expense"), ("AI Spending", "expense"), ("Venmo", "expense"),
    ("Credit Card Payment", "transfer"), ("Account Transfer", "transfer"),
    ("Investment Contribution", "transfer"),
]

DEFAULT_RULES = [
    # (pattern, category, priority)
    ("payroll", "Salary", 10), ("direct dep", "Salary", 10), ("dd payroll", "Salary", 10),
    ("interest", "Interest & Dividends", 20), ("dividend", "Interest & Dividends", 20),
    ("rent", "Housing", 30), ("mortgage", "Housing", 30),
    ("electric", "Utilities", 40), ("water", "Utilities", 40), ("gas co", "Utilities", 40),
    ("comcast", "Utilities", 40), ("xfinity", "Utilities", 40), ("verizon", "Utilities", 40),
    ("t-mobile", "Utilities", 40), ("at&t", "Utilities", 40),
    # NOTE: no bare "internet" rule — Discover payment credits say "INTERNET PAYMENT - THANK YOU"
    ("whole foods", "Groceries", 50), ("wholefds", "Groceries", 50),
    ("trader joe", "Groceries", 50), ("safeway", "Groceries", 50), ("kroger", "Groceries", 50),
    ("costco", "Groceries", 50), ("aldi", "Groceries", 50), ("wegmans", "Groceries", 50),
    ("instacart", "Groceries", 50), ("grocery", "Groceries", 50), ("h mart", "Groceries", 50),
    ("american express ach pmt", "Unlinked Card Spending", 12),
    ("discover e-payment", "Unlinked Card Spending", 12),
    ("starbucks", "Dining & Coffee", 60), ("chipotle", "Dining & Coffee", 60),
    ("dunkin", "Dining & Coffee", 60),
    ("doordash", "Dining & Coffee", 60), ("grubhub", "Dining & Coffee", 60),
    ("uber eats", "Dining & Coffee", 55), ("restaurant", "Dining & Coffee", 60),
    ("cafe", "Dining & Coffee", 60), ("coffee", "Dining & Coffee", 60),
    ("mcdonald", "Dining & Coffee", 60), ("sweetgreen", "Dining & Coffee", 60),
    ("uber", "Transport", 70), ("lyft", "Transport", 70), ("parking", "Transport", 70),
    ("transit", "Transport", 70), ("clipper", "Transport", 70), ("mta", "Transport", 70),
    ("amazon", "Shopping", 80), ("amzn", "Shopping", 80), ("target", "Shopping", 80),
    ("walmart", "Shopping", 80), ("best buy", "Shopping", 80), ("ebay", "Shopping", 80),
    ("cvs", "Health", 90), ("walgreens", "Health", 90), ("pharmacy", "Health", 90),
    ("dental", "Health", 90), ("medical", "Health", 90), ("clinic", "Health", 90),
    ("geico", "Insurance", 95), ("state farm", "Insurance", 95), ("allstate", "Insurance", 95),
    ("insurance", "Insurance", 95),
    ("netflix", "Subscriptions", 100), ("spotify", "Subscriptions", 100),
    ("hulu", "Subscriptions", 100), ("youtube prem", "Subscriptions", 100),
    ("apple.com/bill", "Subscriptions", 100), ("icloud", "Subscriptions", 100),
    ("hbo", "Subscriptions", 100), ("disney", "Subscriptions", 100),
    ("patreon", "Subscriptions", 100), ("substack", "Subscriptions", 100),
    ("gym", "Health", 90), ("fitness", "Health", 90),
    ("cinema", "Entertainment", 110), ("amc ", "Entertainment", 110),
    ("ticketmaster", "Entertainment", 110), ("steam", "Entertainment", 110),
    ("airline", "Travel", 120), ("united air", "Travel", 120), ("delta air", "Travel", 120),
    ("southwest", "Travel", 120), ("airbnb", "Travel", 120), ("hotel", "Travel", 120),
    ("marriott", "Travel", 120), ("hilton", "Travel", 120), ("expedia", "Travel", 120),
    ("atm fee", "Fees & Charges", 130), ("overdraft", "Fees & Charges", 130),
    ("service fee", "Fees & Charges", 130), ("annual fee", "Fees & Charges", 130),
    ("card payment", "Credit Card Payment", 5),
    ("payment thank you", "Credit Card Payment", 5), ("epay", "Credit Card Payment", 6),
    ("payment - thank you", "Credit Card Payment", 5), ("directpay full balance", "Credit Card Payment", 5),
    ("refer a friend credit", "Other Income", 40), ("statement credit", "Other Income", 45),
    ("transfer", "Account Transfer", 15), ("zelle", "Account Transfer", 16),
    ("venmo", "Venmo", 16),   # P2P clustered into its own bucket (outflows spend, cashouts net)
    ("contribution", "Investment Contribution", 8), ("vanguard buy", "Investment Contribution", 8),
    ("fidelity inv", "Investment Contribution", 8), ("401k", "Investment Contribution", 8),
    ("roth", "Investment Contribution", 8),
]

# Vendor library: recognizable merchants get categorized automatically.
# Specific merchants run at priority 55-70; generic keywords (e.g. "pizza",
# "resort") run at 110-160 so a specific rule always wins first.
DEFAULT_RULES += [
    ("racetrac", "Gas & Fuel", 65), ("quiktrip", "Gas & Fuel", 65), ("texaco", "Gas & Fuel", 65),
    ("chevron", "Gas & Fuel", 65), ("shell oil", "Gas & Fuel", 65), ("exxon", "Gas & Fuel", 65),
    ("circle k", "Gas & Fuel", 65), ("wawa", "Gas & Fuel", 65), ("sheetz", "Gas & Fuel", 65),
    ("speedway", "Gas & Fuel", 65), ("sunoco", "Gas & Fuel", 65), ("valero", "Gas & Fuel", 65),
    ("citgo", "Gas & Fuel", 65), ("murphy usa", "Gas & Fuel", 65), ("marathon petro", "Gas & Fuel", 65),
    ("pilot travel", "Gas & Fuel", 65), ("loves travel", "Gas & Fuel", 65), ("bp products", "Gas & Fuel", 65),
    ("betmgm", "Gambling & Betting", 55), ("kalshi", "Gambling & Betting", 55),
    ("novig", "Gambling & Betting", 55), ("parlayplay", "Gambling & Betting", 55),
    ("polymarket", "Gambling & Betting", 55), ("rebet", "Gambling & Betting", 55),
    ("draftkings", "Gambling & Betting", 55), ("fanduel", "Gambling & Betting", 55),
    ("underdog fantasy", "Gambling & Betting", 55), ("caesars sport", "Gambling & Betting", 55),
    ("seeticket", "Entertainment", 65), ("axs event", "Entertainment", 65),
    ("stubhub", "Entertainment", 65), ("eventbrite", "Entertainment", 65),
    ("cinemark", "Entertainment", 65), ("regal ", "Entertainment", 65),
    ("dave & buster", "Entertainment", 65), ("topgolf", "Entertainment", 65),
    ("pokemon", "Entertainment", 65), ("pokémon", "Entertainment", 65),
    ("chick-fil-a", "Dining & Coffee", 65), ("chickfila", "Dining & Coffee", 65),
    ("wingstop", "Dining & Coffee", 65), ("popeyes", "Dining & Coffee", 65),
    ("panera", "Dining & Coffee", 65), ("waffle house", "Dining & Coffee", 65),
    ("zaxby", "Dining & Coffee", 65), ("panda express", "Dining & Coffee", 65),
    ("five guys", "Dining & Coffee", 65), ("shake shack", "Dining & Coffee", 65),
    ("krispy kreme", "Dining & Coffee", 65), ("dutch bros", "Dining & Coffee", 65),
    ("van leeuwen", "Dining & Coffee", 65), ("ben & jerry", "Dining & Coffee", 65),
    ("ice cream", "Dining & Coffee", 150), ("boba", "Dining & Coffee", 150),
    ("bakery", "Dining & Coffee", 150), ("pizza", "Dining & Coffee", 150),
    ("taco", "Dining & Coffee", 150), ("burger", "Dining & Coffee", 150),
    ("sushi", "Dining & Coffee", 150), ("ramen", "Dining & Coffee", 150),
    ("bbq", "Dining & Coffee", 150), ("brewery", "Dining & Coffee", 150),
    ("brewing", "Dining & Coffee", 150), ("taproom", "Dining & Coffee", 150),
    ("bistro", "Dining & Coffee", 150), ("cantina", "Dining & Coffee", 150),
    ("food truck", "Dining & Coffee", 150), ("smoothie", "Dining & Coffee", 150),
    ("donut", "Dining & Coffee", 150), ("doughnut", "Dining & Coffee", 150),
    ("tst*", "Dining & Coffee", 140), ("spo*", "Dining & Coffee", 140),
    ("jersey mike", "Dining & Coffee", 65), ("fandango", "Entertainment", 65),
    ("crunchyroll", "Subscriptions", 65), ("apple.com/bi", "Subscriptions", 65),
    ("headway", "Health", 65),
    ("tj maxx", "Shopping", 65), ("marshalls", "Shopping", 65), ("ross dress", "Shopping", 65),
    ("homegoods", "Shopping", 65), ("nordstrom", "Shopping", 65), ("macys", "Shopping", 65),
    ("macy's", "Shopping", 65), ("uniqlo", "Shopping", 65), ("old navy", "Shopping", 65),
    ("urban outfitters", "Shopping", 65), ("etsy", "Shopping", 65), ("ikea", "Shopping", 65),
    ("home depot", "Shopping", 65), ("lowe's", "Shopping", 65), ("dollar tree", "Shopping", 65),
    ("dollar general", "Shopping", 65), ("five below", "Shopping", 65),
    ("sephora", "Shopping", 65), ("ulta beauty", "Shopping", 65),
    ("petsmart", "Shopping", 65), ("petco", "Shopping", 65),
    ("labcorp", "Health", 65), ("quest diag", "Health", 65), ("urgent care", "Health", 150),
    ("hair salon", "Personal Care", 65), ("nail salon", "Personal Care", 65),
    ("barber", "Personal Care", 150), ("salon", "Personal Care", 160),
    ("georgia power", "Utilities", 40), ("paymentus", "Utilities", 70),
    ("openai", "AI Spending", 65), ("anthropic", "AI Spending", 65),
    ("claude.ai", "AI Spending", 65), ("chatgpt", "AI Spending", 65),
    ("perplexity", "AI Spending", 65), ("midjourney", "AI Spending", 65),
    ("elevenlabs", "AI Spending", 65), ("github copilot", "AI Spending", 65),
    ("oddsjam", "Gambling & Betting", 55), ("propprofessor", "Gambling & Betting", 55),
    ("hinge", "Subscriptions", 65),
    ("bird app", "Transport", 65), ("marta", "Transport", 65), ("scooter", "Transport", 150),
    ("resort", "Travel", 150), ("state park", "Travel", 110),
    ("atm withdrawal", "Cash & ATM", 40), ("atm cash deposit", "Cash & ATM", 40),
    ("atm check deposit", "Cash & ATM", 40),
    ("simplefin", "Fees & Charges", 40),
    ("bbt flex", "Commuter Benefit", 40),
    ("points redeemed", "Other Income", 40), ("gasttaxrfd", "Other Income", 40),
    # NOTE: no rule for "robinhood credits" — those are brokerage->bank transfers,
    # left to pair-matching so they never count as income.
    ("publix", "Groceries", 50), ("sprouts", "Groceries", 50), ("food lion", "Groceries", 50),
    ("ingles", "Groceries", 50), ("lidl", "Groceries", 50), ("piggly wiggly", "Groceries", 50),
]


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    # Seed categories + rules once
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        for name, kind in DEFAULT_CATEGORIES:
            conn.execute("INSERT INTO categories (name, kind) VALUES (?, ?)", (name, kind))
        cat_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
        for pattern, cat, prio in DEFAULT_RULES:
            conn.execute("INSERT INTO rules (pattern, category_id, priority) VALUES (?, ?, ?)",
                         (pattern, cat_ids[cat], prio))
    conn.commit()
    conn.close()


def categorize_transaction(conn, txn_row) -> int | None:
    """Return category_id for a transaction using substring rules (lowest priority wins)."""
    text = " ".join(filter(None, [txn_row["description"], txn_row["payee"], txn_row["memo"]])).lower()
    if not text.strip():
        return None
    rules = conn.execute(
        "SELECT pattern, category_id FROM rules WHERE active=1 ORDER BY priority, id"
    ).fetchall()
    for r in rules:
        if r["pattern"].lower() in text:
            return r["category_id"]
    return None
