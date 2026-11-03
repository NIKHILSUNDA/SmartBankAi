import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
BASE = Path(__file__).parent
DB = dict(host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3306")),
          user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""),
          database=os.getenv("DB_NAME", "smartbank_analytics"))
API_KEY = os.getenv("ANALYST_API_KEY", "")
HOST = os.getenv("FLASK_HOST", "127.0.0.1")
PORT = int(os.getenv("FLASK_PORT", "5000"))
MODEL_PATH = BASE / "models" / "iforest.joblib"
META_PATH = BASE / "models" / "metrics.json"
# Initial, configurable (NOT validated) thresholds: MEDIUM>=30, HIGH>=60, CRITICAL>=80. Flagged = HIGH or CRITICAL.
THRESHOLDS = (30, 60, 80)
# APPROXIMATE country centroids (lat, lon). Used as documented fallback: not real customer locations.
COUNTRIES = {"US": ("United States", 39.8, -98.6), "GB": ("United Kingdom", 54.0, -2.0), "IN": ("India", 22.0, 79.0),
             "DE": ("Germany", 51.2, 10.4), "FR": ("France", 46.6, 2.4), "SG": ("Singapore", 1.35, 103.8),
             "AE": ("UAE", 24.0, 54.0), "BR": ("Brazil", -10.8, -52.9), "JP": ("Japan", 36.2, 138.3),
             "AU": ("Australia", -25.7, 134.5), "NG": ("Nigeria", 9.1, 8.7), "ZA": ("South Africa", -29.0, 24.0),
             "CN": ("China", 35.0, 103.0), "CA": ("Canada", 56.0, -106.0), "RU": ("Russia", 61.0, 100.0)}
