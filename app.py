"""SkyCast (offline edition): typical weather from stored past climate data. No API, no internet needed."""
import calendar
import csv
import difflib
import os
import sqlite3
from datetime import date
from pathlib import Path

from flask import Flask, g, redirect, render_template, request, url_for

app = Flask(__name__)
BASE = Path(__file__).parent
CSV_PATH = BASE / "data" / "climate.csv"
# Vercel's disk is read-only except /tmp
DB_PATH = Path("/tmp/skycast.db") if os.environ.get("VERCEL") else BASE / "skycast.db"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

ALIASES = {
    "banglore": "Bengaluru", "bangalore": "Bengaluru", "blr": "Bengaluru", "bombay": "Mumbai",
    "madras": "Chennai", "calcutta": "Kolkata", "delhi": "New Delhi", "trivandrum": "Thiruvananthapuram",
    "cochin": "Kochi", "vizag": "Visakhapatnam", "poona": "Pune", "nyc": "New York", "panaji": "Goa",
}

QUICK = ["Bengaluru", "Mumbai", "New Delhi", "Chennai", "Kolkata", "Hyderabad", "Pune", "Goa"]


# ---------------------------------------------------------------- data (CSV)
def load_climate():
    """Read the CSV once at startup into {city: {info..., months: [12 dicts]}}."""
    cities = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            c = cities.setdefault(row["city"], {
                "name": row["city"], "country": row["country"], "region": row["region"],
                "months": [None] * 12,
            })
            c["months"][int(row["month"]) - 1] = {
                "high": float(row["avg_high_c"]), "low": float(row["avg_low_c"]),
                "rain_mm": float(row["rain_mm"]), "rain_days": float(row["rain_days"]),
                "humidity": float(row["humidity_pct"]),
            }
    return cities


CLIMATE = load_climate()


def search_cities(term):
    """Alias lookup, then substring match, then fuzzy (typo-tolerant) match."""
    key = ALIASES.get(term.lower().strip(), term).lower()
    names = list(CLIMATE)
    hits = [n for n in names if key in n.lower()]
    if not hits:
        lowered = {n.lower(): n for n in names}
        hits = [lowered[m] for m in difflib.get_close_matches(key, list(lowered), n=5, cutoff=0.75)]
    return [CLIMATE[n] for n in hits]


# ---------------------------------------------------------------- analysis
def describe_month(m, days):
    chance = round(min(m["rain_days"] / days, 1) * 100)
    if chance >= 50:
        rain_label = "Very wet (monsoon / rainy season)"
    elif chance >= 25:
        rain_label = "Often rainy"
    elif chance >= 10:
        rain_label = "Occasional rain"
    else:
        rain_label = "Mostly dry"
    if m["high"] >= 38:
        temp_label = "Very hot"
    elif m["high"] >= 32:
        temp_label = "Hot"
    elif m["high"] >= 24:
        temp_label = "Warm"
    elif m["high"] >= 14:
        temp_label = "Mild"
    else:
        temp_label = "Cold"
    icon = "🌧️" if chance >= 50 else "🌦️" if chance >= 25 else "☀️" if m["high"] >= 32 else "⛅" if m["high"] >= 20 else "❄️" if m["high"] < 10 else "🌤️"
    return {"chance": chance, "rain_label": rain_label, "temp_label": temp_label, "icon": icon}


def analyse(city, year):
    max_rain = max(m["rain_mm"] for m in city["months"]) or 1
    rows = []
    for i, m in enumerate(city["months"]):
        days = calendar.monthrange(year, i + 1)[1]
        d = describe_month(m, days)
        rows.append({**m, **d, "month": MONTHS[i], "index": i + 1, "bar": round(m["rain_mm"] / max_rain * 100),
                     "high_r": round(m["high"]), "low_r": round(m["low"])})
    # Best months to visit: comfortable heat and little rain
    scored = sorted(rows, key=lambda r: (abs(r["high"] - 26) / 4 + r["chance"] / 15))
    best = sorted(r["index"] for r in scored[:3])
    hottest = max(rows, key=lambda r: r["high"])
    coldest = min(rows, key=lambda r: r["low"])
    wettest = max(rows, key=lambda r: r["rain_mm"])
    return rows, [MONTHS[i - 1] for i in best], hottest, coldest, wettest


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """create table if not exists recent_searches (
                id integer primary key autoincrement,
                name text not null,
                created_at text not null default current_timestamp)"""
        )


def recent_searches(limit=8):
    rows = get_db().execute("select name from recent_searches order by id desc limit 40").fetchall()
    seen, out = set(), []
    for r in rows:
        if r["name"] in CLIMATE and r["name"] not in seen:
            seen.add(r["name"])
            out.append(CLIMATE[r["name"]])
    return out[:limit]


# ---------------------------------------------------------------- routes
@app.route("/")
def index():
    term = request.args.get("q", "").strip()
    today = date.today()
    name = request.args.get("city", "New Delhi")
    city = CLIMATE.get(name) or CLIMATE["New Delhi"]
    try:
        month = min(max(int(request.args.get("month", today.month)), 1), 12)
    except ValueError:
        month = today.month

    results = search_cities(term) if len(term) > 1 else []
    rows, best, hottest, coldest, wettest = analyse(city, today.year)

    return render_template(
        "index.html", term=term, results=results, city=city, month=month, current=rows[month - 1],
        rows=rows, best=best, hottest=hottest, coldest=coldest, wettest=wettest,
        quick=[CLIMATE[q] for q in QUICK if q in CLIMATE], recent=recent_searches(),
        is_now=(month == today.month), month_name=calendar.month_name[month],
    )


@app.route("/pick", methods=["POST"])
def pick():
    name = request.form.get("name", "")
    if name not in CLIMATE:
        return redirect(url_for("index"))
    db = get_db()
    db.execute("insert into recent_searches (name) values (?)", (name,))
    db.commit()
    return redirect(url_for("index", city=name))


init_db()

if __name__ == "__main__":
    import socket

    port = 5000
    while port < 5020:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                break
        port += 1
    print(f"\n  SkyCast (offline) running -> open http://127.0.0.1:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=True)
