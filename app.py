import csv, hmac, io, json, logging, random, re, time
from collections import defaultdict, deque
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
import mysql.connector
import pandas as pd
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
import analytics, config, fraud_model as fm, seed_data
from db import q, conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("smartbank")
app = Flask(__name__)
def _enc(o):
    if isinstance(o, Decimal): return float(o)
    if isinstance(o, (datetime, date)): return o.isoformat()
    raise TypeError(type(o))
app.json.default = _enc

STATUSES = {"NEW", "UNDER_REVIEW", "ESCALATED", "RESOLVED", "FALSE_POSITIVE"}
CATS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
SORTS = {"transaction_id": "t.transaction_id", "transaction_timestamp": "t.transaction_timestamp", "amount": "t.amount", "risk_score": "p.risk_score"}
SEL = ("t.transaction_id,t.account_id,t.transaction_timestamp,t.amount,t.currency,t.transaction_type,t.merchant_category,t.origin_country,t.destination_country,"
       "t.origin_latitude,t.origin_longitude,t.destination_latitude,t.destination_longitude,t.is_synthetic,p.risk_score,p.risk_category,p.model_prediction,"
       "p.model_version,p.explanation,c.case_id,c.case_status,c.investigation_notes,c.assigned_analyst")
FROM = ("transactions t LEFT JOIN transaction_predictions p ON p.transaction_id=t.transaction_id "
        "LEFT JOIN investigation_cases c ON c.transaction_id=t.transaction_id")

# ---------- security helpers ----------
_hits = defaultdict(deque)
def limited(n=30):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            now = time.time(); d = _hits[(f.__name__, request.remote_addr)]
            while d and now - d[0] > 60: d.popleft()
            if len(d) >= n: return jsonify(error="Rate limit exceeded"), 429
            d.append(now); return f(*a, **k)
        return w
    return deco

def auth(f):
    @wraps(f)
    def w(*a, **k):
        if not config.API_KEY: return jsonify(error="ANALYST_API_KEY is not configured on the server"), 503
        if not hmac.compare_digest(request.headers.get("X-API-Key", ""), config.API_KEY):
            return jsonify(error="Invalid or missing analyst API key"), 401
        return f(*a, **k)
    return w

@app.errorhandler(ValueError)
def _bad(e): return jsonify(error=str(e)), 400
@app.errorhandler(mysql.connector.Error)
def _db(e): log.error("DB error: %s", e); return jsonify(error="Database unavailable or query failed"), 503
@app.errorhandler(Exception)
def _any(e):
    if isinstance(e, HTTPException): return jsonify(error=e.description), e.code
    log.exception("Unhandled"); return jsonify(error="Internal server error"), 500

# ---------- helpers ----------
def days_arg():
    d = int(request.args.get("days", 7)); return min(max(d, 1), 90)
def fix(r):
    if isinstance(r.get("explanation"), (str, bytes)): r["explanation"] = json.loads(r["explanation"])
    return r
def num_arg(k):
    return float(request.args[k])
def tx_filters():
    g = request.args; w, a = ["1=1"], []
    if g.get("days"): w.append("t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL %s DAY"); a.append(days_arg())
    if g.get("q"):
        s = g["q"].strip()[:40].lstrip("#"); w.append("(t.account_id LIKE %s OR CAST(t.transaction_id AS CHAR)=%s)"); a += [f"%{s}%", s]
    if g.get("type"):
        if g["type"] not in seed_data.TYPES: raise ValueError("invalid type")
        w.append("t.transaction_type=%s"); a.append(g["type"])
    if g.get("risk"):
        if g["risk"] not in CATS: raise ValueError("invalid risk")
        w.append("p.risk_category=%s"); a.append(g["risk"])
    if g.get("status"):
        if g["status"] not in STATUSES: raise ValueError("invalid status")
        w.append("c.case_status=%s"); a.append(g["status"])
    if g.get("min_amount"): w.append("t.amount>=%s"); a.append(num_arg("min_amount"))
    if g.get("max_amount"): w.append("t.amount<=%s"); a.append(num_arg("max_amount"))
    if g.get("since_id"): w.append("t.transaction_id>%s"); a.append(int(g["since_id"]))
    if g.get("flagged") == "1": w.append("p.model_prediction=1")
    return " AND ".join(w), a

def csv_resp(rows, cols, name):
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(cols)
    for r in rows:   # guard against spreadsheet formula injection
        w.writerow([("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for v in (r.get(c) for c in cols)])
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={name}"})

# ---------- routes ----------
@app.get("/")
def index(): return send_from_directory(config.BASE, "index.html")

@app.get("/api/health")
def health():
    try: q("SELECT 1 AS ok"); db = True
    except Exception as e: log.error("health: %s", e); db = False
    return jsonify(status="ok" if db else "degraded", database=db, model_ready=fm.ready(), auth_configured=bool(config.API_KEY),
                   data_source="synthetic demo dataset"), (200 if db else 503)

def window_stats(lo, hi):
    return q("""SELECT COUNT(*) n,COALESCE(SUM(t.amount),0) val,COALESCE(SUM(p.model_prediction=1),0) flagged,COALESCE(SUM(p.risk_category='CRITICAL'),0) critical,
      COALESCE(SUM(CASE WHEN p.model_prediction=1 THEN t.amount END),0) flagged_amount FROM transactions t LEFT JOIN transaction_predictions p ON p.transaction_id=t.transaction_id
      WHERE t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL %s DAY AND t.transaction_timestamp<=UTC_TIMESTAMP()-INTERVAL %s DAY+INTERVAL 1 MINUTE""", (lo, hi), one=True)

def bucket_fmt(bucket): return "%Y-%m-%d %H:00" if bucket == "hour" else "%Y-%m-%d"

@app.get("/api/overview")
def overview():
    d = days_arg()
    series = q("""SELECT DATE_FORMAT(t.transaction_timestamp,%s) b,COUNT(*) n,SUM(t.amount) v,COALESCE(SUM(p.model_prediction=1),0) f,COALESCE(SUM(p.risk_category='CRITICAL'),0) c,
      COALESCE(SUM(CASE WHEN p.model_prediction=1 THEN t.amount END),0) fa FROM transactions t LEFT JOIN transaction_predictions p ON p.transaction_id=t.transaction_id
      WHERE t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY b ORDER BY b""", (bucket_fmt("hour" if d <= 1 else "day"), d))
    return jsonify(days=d, current=window_stats(d, 0), previous=window_stats(2 * d, d), series=series,
                   ground_truth_available=False, note="Flagged = model risk >= 60. Flagged amounts are NOT confirmed fraud losses.")

@app.get("/api/transactions")
def transactions():
    where, args = tx_filters()
    page = max(int(request.args.get("page", 1)), 1); per = min(max(int(request.args.get("per_page", 25)), 1), 200)
    sort = SORTS.get(request.args.get("sort"), "t.transaction_id"); dr = "ASC" if request.args.get("dir") == "asc" else "DESC"
    total = q(f"SELECT COUNT(*) n FROM {FROM} WHERE {where}", args, one=True)["n"]
    rows = q(f"SELECT {SEL} FROM {FROM} WHERE {where} ORDER BY {sort} {dr}, t.transaction_id DESC LIMIT %s OFFSET %s", args + [per, (page - 1) * per])
    return jsonify(items=[fix(r) for r in rows], total=total, page=page, per_page=per)

@app.get("/api/transactions/<int:tid>")
def tx_detail(tid):
    r = q(f"SELECT {SEL} FROM {FROM} WHERE t.transaction_id=%s", (tid,), one=True)
    if not r: return jsonify(error="Transaction not found"), 404
    hist = q(f"SELECT {SEL} FROM {FROM} WHERE t.account_id=%s ORDER BY t.transaction_timestamp DESC LIMIT 12", (r["account_id"],))
    audit = q("SELECT action,previous_value,new_value,changed_by,changed_at FROM audit_logs WHERE case_id=%s ORDER BY audit_id DESC LIMIT 20", (r["case_id"],)) if r["case_id"] else []
    return jsonify(transaction=fix(r), history=[fix(h) for h in hist], audit=audit)

@app.get("/api/alerts")
def alerts():
    since = int(request.args.get("since_id", 0)); lim = min(int(request.args.get("limit", 25)), 100)
    rows = q(f"SELECT {SEL} FROM {FROM} WHERE p.model_prediction=1 AND t.transaction_id>%s ORDER BY t.transaction_id DESC LIMIT %s", (since, lim))
    return jsonify(items=[fix(r) for r in rows])

@app.get("/api/analytics/volume")
def volume():
    d = days_arg(); b = "hour" if request.args.get("bucket") == "hour" else "day"
    rows = q("""SELECT DATE_FORMAT(t.transaction_timestamp,%s) b,COUNT(*) n,SUM(t.amount) v,COALESCE(SUM(p.model_prediction=1),0) f FROM transactions t
      LEFT JOIN transaction_predictions p ON p.transaction_id=t.transaction_id WHERE t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY b ORDER BY b""", (bucket_fmt(b), d))
    return jsonify(bucket=b, items=rows)

@app.get("/api/analytics/types")
def types():
    d = days_arg()
    base = """SELECT {k} k,COUNT(*) n,COALESCE(SUM(p.model_prediction=1),0) flagged FROM transactions t LEFT JOIN transaction_predictions p ON p.transaction_id=t.transaction_id
      WHERE t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL %s DAY {x} GROUP BY k ORDER BY n DESC"""
    return jsonify(types=q(base.format(k="t.transaction_type", x=""), (d,)),
                   merchants=q(base.format(k="t.merchant_category", x="AND t.merchant_category IS NOT NULL"), (d,)))

@app.get("/api/analytics/risk-distribution")
def risk_dist():
    d = days_arg()
    risk = q("""SELECT FLOOR(LEAST(p.risk_score,99.99)/10)*10 bucket,COUNT(*) n FROM transactions t JOIN transaction_predictions p ON p.transaction_id=t.transaction_id
      WHERE t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY bucket ORDER BY bucket""", (d,))
    amt = q("SELECT FLOOR(LOG10(t.amount)) e,COUNT(*) n FROM transactions t WHERE t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL %s DAY GROUP BY e ORDER BY e", (d,))
    return jsonify(risk=risk, amount=amt)

@app.get("/api/analytics/geography")
def geography():
    d = days_arg()
    rows = q("""SELECT t.origin_country code,COUNT(*) n,SUM(t.amount) v,COALESCE(SUM(p.model_prediction=1),0) flagged FROM transactions t
      LEFT JOIN transaction_predictions p ON p.transaction_id=t.transaction_id WHERE t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL %s DAY AND t.origin_country IS NOT NULL
      GROUP BY code ORDER BY n DESC""", (d,))
    for r in rows: r["name"], r["lat"], r["lon"] = config.COUNTRIES.get(r["code"], (r["code"], None, None))
    routes = q("""SELECT origin_country o,destination_country d,COUNT(*) n FROM transactions t WHERE t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL %s DAY
      AND destination_country IS NOT NULL AND destination_country<>origin_country GROUP BY o,d ORDER BY n DESC LIMIT 15""", (d,))
    return jsonify(countries=rows, routes=routes, note="Coordinates are approximate country centroids, not customer locations.")

@app.get("/api/customers/unusual")
def unusual():
    rows = q("""SELECT a.customer_id,COUNT(*) n,AVG(t.amount) avg_amt,SUM(t.amount) total,SUM(p.model_prediction=1) flagged,MAX(p.risk_score) max_risk FROM transactions t
      JOIN accounts a ON a.account_id=t.account_id LEFT JOIN transaction_predictions p ON p.transaction_id=t.transaction_id
      WHERE t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL 30 DAY GROUP BY a.customer_id HAVING flagged>0 ORDER BY flagged DESC,max_risk DESC LIMIT 15""")
    return jsonify(items=rows)

@app.get("/api/customers/<cid>/summary")
def cust_summary(cid):
    if not re.fullmatch(r"[A-Za-z0-9\-]{3,20}", cid): raise ValueError("invalid customer id")
    cu = q("SELECT customer_id,customer_segment,home_country,account_created_at FROM customers WHERE customer_id=%s", (cid,), one=True)
    if not cu: return jsonify(error="Customer not found"), 404
    j = "FROM transactions t JOIN accounts a ON a.account_id=t.account_id LEFT JOIN transaction_predictions p ON p.transaction_id=t.transaction_id WHERE a.customer_id=%s"
    stats = q(f"SELECT COUNT(*) n,COALESCE(AVG(t.amount),0) avg_amt,COALESCE(SUM(t.amount),0) total,COALESCE(SUM(p.model_prediction=1),0) flagged {j}", (cid,), one=True)
    tp = q(f"SELECT t.transaction_type k,COUNT(*) n {j} GROUP BY k", (cid,))
    daily = q(f"SELECT DATE(t.transaction_timestamp) b,COUNT(*) n,SUM(t.amount) v {j} GROUP BY b ORDER BY b", (cid,))
    ch = q(f"""SELECT AVG(CASE WHEN t.transaction_timestamp>=UTC_TIMESTAMP()-INTERVAL 3 DAY THEN t.amount END) recent,
      AVG(CASE WHEN t.transaction_timestamp<UTC_TIMESTAMP()-INTERVAL 3 DAY THEN t.amount END) prior {j}""", (cid,), one=True)
    return jsonify(customer=cu, stats=stats, types=tp, daily=daily, spending_change={"recent_3d_avg": ch["recent"], "prior_avg": ch["prior"]})

@app.get("/api/model/metrics")
def model_metrics():
    dist = q("SELECT risk_category k,COUNT(*) n FROM transaction_predictions GROUP BY k")
    return jsonify(meta=fm.load_meta(), live_distribution=dist, disclaimer="Anomaly scores are not proof of fraud.")

@app.post("/api/model/train")
@auth
@limited(5)
def model_train(): return jsonify(meta=analytics.train_and_store())

@app.post("/api/model/predict")
@limited(60)
def model_predict():
    b = request.get_json(silent=True) or {}
    if b.get("transaction_type") not in seed_data.TYPES: raise ValueError("transaction_type invalid")
    amt = float(b.get("amount", 0))
    if not 0 < amt < 1e9: raise ValueError("amount out of range")
    oc, dc = b.get("origin_country"), b.get("destination_country")
    for c in (oc, dc):
        if c is not None and c not in config.COUNTRIES: raise ValueError("unknown country code")
    ts = pd.Timestamp(b.get("timestamp") or datetime.utcnow())
    df = pd.DataFrame([{"transaction_id": 0, "account_id": str(b.get("account_id", ""))[:16], "transaction_timestamp": ts, "amount": amt,
                        "transaction_type": b["transaction_type"], "origin_country": oc, "destination_country": dc}])
    try: return jsonify(fm.score(df)[0])
    except RuntimeError as e: return jsonify(error=str(e)), 409

@app.post("/api/demo/generate")
@auth
@limited(60)
def demo_generate():
    if not fm.ready(): raise ValueError("Train the model first (python seed_data.py --reset)")
    a = q("""SELECT a.account_id,c.home_country,(SELECT AVG(amount) FROM transactions x WHERE x.account_id=a.account_id) avg_amt FROM accounts a
      JOIN customers c ON c.customer_id=a.customer_id WHERE a.account_status='ACTIVE' ORDER BY RAND() LIMIT 1""", one=True)
    if not a: raise ValueError("No accounts. Run seed_data.py")
    t = seed_data.gen_tx(a["account_id"], a["home_country"], datetime.utcnow(), random, random.random() < 0.12, float(a["avg_amt"] or 90) / 90)
    tid = analytics.insert_tx(t); analytics.score_and_store([tid])
    return jsonify(transaction_id=tid, synthetic=True), 201

@app.get("/api/cases")
def cases():
    g = request.args; w, a = ["c.case_id IS NOT NULL"], []
    if g.get("status"):
        if g["status"] not in STATUSES: raise ValueError("invalid status")
        w.append("c.case_status=%s"); a.append(g["status"])
    page = max(int(g.get("page", 1)), 1); per = min(max(int(g.get("per_page", 15)), 1), 100)
    where = " AND ".join(w)
    total = q(f"SELECT COUNT(*) n FROM {FROM} WHERE {where}", a, one=True)["n"]
    rows = q(f"SELECT {SEL} FROM {FROM} WHERE {where} ORDER BY p.risk_score DESC,t.transaction_id DESC LIMIT %s OFFSET %s", a + [per, (page - 1) * per])
    return jsonify(items=[fix(r) for r in rows], total=total, page=page, per_page=per)

@app.patch("/api/cases/<int:cid>")
@auth
@limited(60)
def patch_case(cid):
    b = request.get_json(silent=True) or {}; st, notes = b.get("status"), b.get("notes")
    if st is not None and st not in STATUSES: raise ValueError("invalid status")
    if st is None and notes is None: raise ValueError("nothing to update")
    notes = None if notes is None else str(notes)[:4000]; who = str(b.get("analyst") or "analyst")[:60]
    c = conn()
    try:
        cur = c.cursor(dictionary=True)
        cur.execute("SELECT * FROM investigation_cases WHERE case_id=%s FOR UPDATE", (cid,)); old = cur.fetchone()
        if not old: return jsonify(error="Case not found"), 404
        ns = st or old["case_status"]; nn = old["investigation_notes"] if notes is None else notes
        cur.execute("UPDATE investigation_cases SET case_status=%s,investigation_notes=%s,assigned_analyst=%s,updated_at=UTC_TIMESTAMP() WHERE case_id=%s", (ns, nn, who, cid))
        if ns != old["case_status"]:
            cur.execute("INSERT INTO audit_logs (case_id,action,previous_value,new_value,changed_by) VALUES (%s,'STATUS_CHANGE',%s,%s,%s)", (cid, old["case_status"], ns, who))
        if nn != old["investigation_notes"]:
            cur.execute("INSERT INTO audit_logs (case_id,action,previous_value,new_value,changed_by) VALUES (%s,'NOTES_UPDATED',%s,%s,%s)", (cid, old["investigation_notes"], nn, who))
        c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()
    return jsonify(case_id=cid, case_status=ns)

@app.get("/api/audit-logs")
def audit_logs():
    lim = min(int(request.args.get("limit", 50)), 200)
    return jsonify(items=q("SELECT audit_id,case_id,action,previous_value,new_value,changed_by,changed_at FROM audit_logs ORDER BY audit_id DESC LIMIT %s", (lim,)))

@app.get("/api/export/transactions.csv")
def export_tx():
    where, args = tx_filters()
    rows = q(f"SELECT {SEL} FROM {FROM} WHERE {where} ORDER BY t.transaction_id DESC LIMIT 50000", args)
    cols = ["transaction_id", "account_id", "transaction_timestamp", "amount", "currency", "transaction_type", "merchant_category", "origin_country", "destination_country",
            "risk_score", "risk_category", "model_prediction", "model_version", "case_status"]
    return csv_resp(rows, cols, "transactions.csv")

@app.get("/api/export/cases.csv")
def export_cases():
    rows = q(f"SELECT {SEL} FROM {FROM} WHERE c.case_id IS NOT NULL ORDER BY p.risk_score DESC LIMIT 50000")
    return csv_resp(rows, ["case_id", "transaction_id", "account_id", "transaction_timestamp", "amount", "risk_score", "risk_category", "case_status", "assigned_analyst", "investigation_notes"], "cases.csv")

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=False)
