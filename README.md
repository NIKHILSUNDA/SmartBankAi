# SMARTBANK AI — Fraud Detection & Threat Intelligence (synthetic-data demo)
Flask + MySQL 8 + scikit-learn (Isolation Forest) + single-file `index.html` (Three.js globe, Chart.js).
**All data is synthetic. Flags are "unusual, needs review" — not confirmed fraud. Not production-secure; no security review done.**
Needs internet for CDN libs (Three.js, Chart.js, Inter font, Earth texture); the app degrades gracefully without them.

## Windows 10/11 setup (PowerShell in VS Code terminal)
```powershell
python --version          # 3.10+
mysql --version           # MySQL 8
cd smartbank-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
copy .env.example .env            # then edit .env: DB_USER / DB_PASSWORD / ANALYST_API_KEY
mysql -u root -p -e "source schema.sql"
# optional dedicated user:
# mysql -u root -p -e "CREATE USER 'smartbank'@'localhost' IDENTIFIED BY 'change_me'; GRANT ALL ON smartbank_analytics.* TO 'smartbank'@'localhost';"
python seed_data.py --reset       # creates synthetic data AND trains + scores with the model
python app.py                     # open http://127.0.0.1:5000  (never file://)
```
In the app: **System Settings → paste ANALYST_API_KEY** → needed for case updates, retraining and the demo simulator.

## How it works
- **Scoring**: Isolation Forest on log-amount, per-account amount z-score, hour (cyclical), cross-border flag, type. Raw anomaly score is calibrated: training median → 0, 99.9th percentile → 100. Categories (config.py `THRESHOLDS`): LOW <30, MEDIUM 30–59, HIGH 60–79, CRITICAL ≥80; flagged = ≥60. These are *unvalidated defaults*; calibrate using analyst outcomes (FALSE_POSITIVE rate per band).
- **Metrics**: no labels → no precision/recall. `fraud_model.train_supervised()` implements the supervised path (Random Forest, stratified split, PR-AUC/ROC-AUC/confusion matrix) for labelled datasets.
- **Geography**: country centroids from `config.COUNTRIES` (approximate). Arcs only if both endpoints exist.
- **Live updates**: browser polls every 3–5 s. LIVE = backend+DB reachable; DEMO SIMULATION = simulator on; OFFLINE = unreachable.
- Times are stored/shown from UTC.

## Troubleshooting
- `1045 Access denied` → wrong DB_USER/DB_PASSWORD in `.env`. `2003 Can't connect` → start the MySQL80 service (`services.msc`). `1049 Unknown database` → run schema.sql.
- `Data already exists` → use `--reset`. Dashboard shows OFFLINE → check the terminal running `app.py` and `/api/health`.
- 401 on save → API key in Settings must equal `ANALYST_API_KEY` in `.env` (restart app after editing).
