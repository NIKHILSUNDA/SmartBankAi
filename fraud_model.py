"""Mode B (default): unsupervised Isolation Forest. Anomaly score != proof of fraud.
Mode A: train_supervised() for datasets that DO have trustworthy labels (not used by the demo DB, which has none)."""
import json
from datetime import datetime
import joblib, numpy as np, pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import precision_recall_fscore_support, average_precision_score, roc_auc_score, confusion_matrix
from sklearn.model_selection import train_test_split
from config import MODEL_PATH, META_PATH, THRESHOLDS

TYPES = ["PAYMENT", "TRANSFER", "CASH_OUT", "CASH_IN", "DEBIT"]
_A = None

def ready(): return MODEL_PATH.exists()

def _stats(df):
    la = np.log1p(df.amount.astype(float))
    g = (float(la.mean()), max(float(la.std()), 0.3))
    d = pd.DataFrame({"a": df.account_id.values, "l": la.values}).groupby("a").l.agg(["mean", "std", "count"])
    acct = {a: (float(r["mean"]), max(float(r["std"]) if r["std"] == r["std"] else 0.3, 0.3))
            for a, r in d.iterrows() if r["count"] >= 5}
    return {"global": g, "acct": acct}

def features(df, st):
    df = df.reset_index(drop=True)
    ts = pd.to_datetime(df.transaction_timestamp)
    la = np.log1p(df.amount.astype(float)); g = st["global"]
    m = df.account_id.map(lambda a: st["acct"].get(a, g)[0]).astype(float)
    s = df.account_id.map(lambda a: st["acct"].get(a, g)[1]).astype(float)
    X = pd.DataFrame({"log_amount": la, "acct_z": (la - m) / s,
                      "hour_sin": np.sin(2 * np.pi * ts.dt.hour / 24), "hour_cos": np.cos(2 * np.pi * ts.dt.hour / 24),
                      "cross_border": ((df.origin_country != df.destination_country) & df.destination_country.notna()).astype(float)})
    for t in TYPES: X["t_" + t] = (df.transaction_type == t).astype(float)
    return X

def category(r):
    a, b, c = THRESHOLDS
    return "CRITICAL" if r >= c else "HIGH" if r >= b else "MEDIUM" if r >= a else "LOW"

def train(df):
    global _A
    st = _stats(df); X = features(df, st)
    m = IsolationForest(n_estimators=200, random_state=42).fit(X)
    raw = -m.score_samples(X)
    lo, hi = (float(v) for v in np.quantile(raw, [0.5, 0.999]))   # calibration: median -> 0, 99.9th pct -> 100
    ver = datetime.utcnow().strftime("iforest-%Y%m%d%H%M%S")
    risk = np.clip((raw - lo) / (hi - lo), 0, 1) * 100
    cats = pd.Series([category(r) for r in risk]).value_counts().to_dict()
    meta = {"mode": "unsupervised_isolation_forest", "model_version": ver, "trained_rows": int(len(df)),
            "trained_at": datetime.utcnow().isoformat(), "thresholds": THRESHOLDS, "category_counts": cats,
            "calibration": {"raw_p50": lo, "raw_p999": hi},
            "evaluation": "No ground-truth fraud labels exist, so precision/recall/PR-AUC cannot be computed. "
                          "Scores rank unusual behaviour only; flagged != confirmed fraud. Calibrate thresholds with analyst feedback (FALSE_POSITIVE rates)."}
    MODEL_PATH.parent.mkdir(exist_ok=True)
    _A = {"model": m, "stats": st, "lo": lo, "hi": hi, "version": ver, "cols": list(X.columns),
          "amt99": float(df.amount.astype(float).quantile(0.99))}
    joblib.dump(_A, MODEL_PATH); META_PATH.write_text(json.dumps(meta, indent=2))
    return meta

def load_meta():
    return json.loads(META_PATH.read_text()) if META_PATH.exists() else None

def score(df):
    global _A
    if _A is None:
        if not ready(): raise RuntimeError("Model not trained yet")
        _A = joblib.load(MODEL_PATH)
    a = _A; df = df.reset_index(drop=True)
    X = features(df, a["stats"])[a["cols"]]
    risk = np.clip((-a["model"].score_samples(X) - a["lo"]) / (a["hi"] - a["lo"]), 0, 1) * 100
    hours = pd.to_datetime(df.transaction_timestamp).dt.hour
    out = []
    for i, r in enumerate(risk):
        x = X.iloc[i]; why = []
        if abs(x.acct_z) >= 3: why.append(f"Amount deviates strongly from this account's usual amounts (z={x.acct_z:.1f})")
        if x.log_amount >= np.log1p(a["amt99"]): why.append("Amount is in the top 1% of all transactions")
        if hours[i] <= 5: why.append("Transaction time is unusual (00:00-05:59 UTC)")
        if x.cross_border: why.append("Cross-border transfer relative to the account's country")
        r = round(float(r), 1)
        out.append({"risk_score": r, "risk_category": category(r), "model_prediction": int(r >= THRESHOLDS[1]),
                    "model_version": a["version"], "reasons": why or ["Unusual combination of features (no single dominant indicator)"]})
    return out

def train_supervised(X, y):
    """Mode A: stratified split, class_weight for imbalance, PR-AUC focus. X numeric DataFrame, y 0/1 labels."""
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, stratify=y, random_state=42)
    clf = RandomForestClassifier(n_estimators=300, class_weight="balanced_subsample", random_state=42, n_jobs=-1).fit(Xtr, ytr)
    p = clf.predict_proba(Xte)[:, 1]; pred = (p >= 0.5).astype(int)
    pr, rc, f1, _ = precision_recall_fscore_support(yte, pred, average="binary", zero_division=0)
    return clf, {"precision": pr, "recall": rc, "f1": f1, "pr_auc": float(average_precision_score(yte, p)),
                 "roc_auc": float(roc_auc_score(yte, p)), "confusion_matrix": confusion_matrix(yte, pred).tolist()}
