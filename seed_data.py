"""Reproducible SYNTHETIC data generator (seed 42). Not real banking data. Usage: python seed_data.py [--reset]"""
import math, random, sys
from datetime import datetime, timedelta
from config import COUNTRIES
from db import conn

TYPES = ["PAYMENT", "TRANSFER", "CASH_OUT", "CASH_IN", "DEBIT"]
MERCH = ["GROCERY", "TRAVEL", "ELECTRONICS", "DINING", "UTILITIES", "FUEL", "HEALTH", "ENTERTAINMENT"]

def gen_tx(account_id, home, ts, rng, anomalous=False, scale=1.0):
    typ = rng.choices(TYPES, [45, 20, 10, 10, 15])[0]
    amt = min(rng.lognormvariate(math.log(60), 0.9), 5000) * scale
    dest = (home if rng.random() < 0.85 else rng.choice(list(COUNTRIES))) if typ == "TRANSFER" else None
    if anomalous:
        amt = rng.uniform(3000, 20000); typ = rng.choice(["TRANSFER", "CASH_OUT"])
        dest = rng.choice([c for c in COUNTRIES if c != home])
    o = COUNTRIES[home]; d = COUNTRIES.get(dest)
    return {"account_id": account_id, "transaction_timestamp": ts, "amount": round(max(amt, 1), 2), "currency": "USD",
            "transaction_type": typ, "merchant_category": rng.choice(MERCH) if typ in ("PAYMENT", "DEBIT") else None,
            "origin_country": home, "destination_country": dest, "origin_latitude": o[1], "origin_longitude": o[2],
            "destination_latitude": d[1] if d else None, "destination_longitude": d[2] if d else None}

def main(reset):
    rng = random.Random(42); now = datetime.utcnow(); codes = list(COUNTRIES)
    c = conn(); cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM customers")
    if cur.fetchone()[0]:
        if not reset: sys.exit("Data already exists. Re-run with --reset to wipe and regenerate.")
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for t in ["audit_logs", "investigation_cases", "transaction_predictions", "transactions", "accounts", "customers"]: cur.execute(f"TRUNCATE TABLE {t}")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
    cust, acc, accinfo = [], [], []
    for i in range(1, 301):
        home = rng.choices(codes, [20, 8, 20, 6, 5, 4, 4, 5, 4, 3, 3, 2, 6, 4, 2])[0]
        cid = f"CUST-{i:04d}"; cust.append((cid, rng.choice(["RETAIL", "RETAIL", "PREMIUM", "BUSINESS"]), home, now - timedelta(days=rng.randint(60, 2000))))
        for j in range(rng.choice([1, 1, 2])):
            aid = f"ACC-{len(acc)+1:06d}"; acc.append((aid, cid, rng.choice(["SAVINGS", "CURRENT", "CREDIT"]), "ACTIVE", now - timedelta(days=rng.randint(30, 1900))))
            accinfo.append((aid, home, rng.uniform(0.5, 3.0)))
    cur.executemany("INSERT INTO customers VALUES (%s,%s,%s,%s)", cust)
    cur.executemany("INSERT INTO accounts (account_id,customer_id,account_type,account_status,created_at) VALUES (%s,%s,%s,%s,%s)", acc)
    rows = []
    for _ in range(8000):
        aid, home, sc = rng.choice(accinfo); bad = rng.random() < 0.015
        ts = now - timedelta(seconds=rng.uniform(60, 14 * 86400))
        while ts.hour < 6 and rng.random() < 0.85: ts = now - timedelta(seconds=rng.uniform(60, 14 * 86400))
        if bad:
            ts = ts.replace(hour=rng.choice([1, 2, 3, 4]))
            if ts > now: ts -= timedelta(days=1)
        t = gen_tx(aid, home, ts, rng, bad, sc)
        rows.append((t["account_id"], t["transaction_timestamp"], t["amount"], t["currency"], t["transaction_type"], t["merchant_category"], t["origin_country"],
                     t["destination_country"], t["origin_latitude"], t["origin_longitude"], t["destination_latitude"], t["destination_longitude"], 1))
    cur.executemany("INSERT INTO transactions (account_id,transaction_timestamp,amount,currency,transaction_type,merchant_category,origin_country,destination_country,"
                    "origin_latitude,origin_longitude,destination_latitude,destination_longitude,is_synthetic) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    c.commit(); c.close()
    print(f"Seeded {len(cust)} customers, {len(acc)} accounts, {len(rows)} SYNTHETIC transactions. Training model...")
    import analytics; meta = analytics.train_and_store()
    print("Model", meta["model_version"], "category counts:", meta["category_counts"])

if __name__ == "__main__":
    main("--reset" in sys.argv)
