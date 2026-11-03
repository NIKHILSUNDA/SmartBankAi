import json
from datetime import datetime
import pandas as pd
import fraud_model as fm
from db import q, conn
COLS = "transaction_id,account_id,transaction_timestamp,amount,transaction_type,origin_country,destination_country"

def load_df(ids=None):
    if ids:
        return pd.DataFrame(q(f"SELECT {COLS} FROM transactions WHERE transaction_id IN ({','.join(['%s']*len(ids))})", list(ids)))
    return pd.DataFrame(q(f"SELECT {COLS} FROM transactions"))

def store(df, res):
    now = datetime.utcnow(); pr, cs = [], []
    for tid, r in zip(df.transaction_id, res):
        pr.append((int(tid), r["risk_score"], r["risk_category"], r["model_prediction"], r["model_version"], json.dumps(r["reasons"]), now))
        if r["model_prediction"]: cs.append((int(tid), "NEW"))
    c = conn()
    try:
        cur = c.cursor()
        cur.executemany("INSERT INTO transaction_predictions (transaction_id,risk_score,risk_category,model_prediction,model_version,explanation,predicted_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE risk_score=VALUES(risk_score),risk_category=VALUES(risk_category),"
                        "model_prediction=VALUES(model_prediction),model_version=VALUES(model_version),explanation=VALUES(explanation),predicted_at=VALUES(predicted_at)", pr)
        if cs: cur.executemany("INSERT IGNORE INTO investigation_cases (transaction_id,case_status) VALUES (%s,%s)", cs)
        c.commit()
    finally:
        c.close()

def score_and_store(ids):
    df = load_df(ids); store(df, fm.score(df))

def train_and_store():
    df = load_df()
    if len(df) < 200: raise ValueError("Need at least 200 transactions to train (run seed_data.py)")
    meta = fm.train(df); store(df, fm.score(df)); return meta

def insert_tx(t):
    c = conn()
    try:
        cur = c.cursor()
        cur.execute("INSERT INTO transactions (account_id,transaction_timestamp,amount,currency,transaction_type,merchant_category,origin_country,destination_country,"
                    "origin_latitude,origin_longitude,destination_latitude,destination_longitude,is_synthetic) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (t["account_id"], t["transaction_timestamp"], t["amount"], t["currency"], t["transaction_type"], t["merchant_category"], t["origin_country"],
                     t["destination_country"], t["origin_latitude"], t["origin_longitude"], t["destination_latitude"], t["destination_longitude"], 1))
        c.commit(); return cur.lastrowid
    finally:
        c.close()
