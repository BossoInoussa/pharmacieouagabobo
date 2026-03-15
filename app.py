import os
import math
import datetime
import json
from flask import Flask, render_template, request, jsonify
from pymongo import MongoClient, GEOSPHERE

app = Flask(__name__)

# MONGO_URI = os.environ.get("MONGO_URL")
# MONGO_URI = os.environ.get("MONGO_URL", "NOT_FOUND")
# print(f"[DB] MONGO_URI = {MONGO_URI}")
MONGO_URI = os.environ.get("MONGO_URL", "mongodb://localhost:27017/")

DB_NAME   = "pharmacies_bf"
_client   = None

REFERENCE_DATE  = datetime.datetime(2025, 3, 8, 12, 0, 0)
REFERENCE_GROUP = 1


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI)
    return _client[DB_NAME]


def auto_init_db():
    try:
        db  = get_db()
        col = db["pharmacies"]

        if col.count_documents({}) > 0:
            print(f"[DB] Base deja initialisee ({col.count_documents({})} pharmacies).")
            return

        print("[DB] Base vide — import automatique...")

        base_dirs = [os.path.dirname(os.path.abspath(__file__)), "/app", "."]
        files = [
            ("pharmaciesOuaga.json", "Ouagadougou"),
            ("pharmaciesBobo.json",  "Bobo-Dioulasso"),
        ]

        all_docs = []
        for filename, ville in files:
            for base in base_dirs:
                filepath = os.path.join(base, filename)
                if os.path.exists(filepath):
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    pharmacies = data.get("pharmacies", [])
                    print(f"[DB]   {ville}: {len(pharmacies)} pharmacies")
                    for p in pharmacies:
                        lon = p["geojson"]["coordinates"][0]
                        lat = p["geojson"]["coordinates"][1]
                        all_docs.append({
                            "id":            p["id"],
                            "nom":           p["nom"],
                            "ville":         p.get("ville", ville),
                            "telephone":     p.get("telephone", ""),
                            "adresse":       p.get("adresse_description", ""),
                            "groupe_garde":  p.get("groupe_garde"),
                            "ouvert_24h":    p.get("ouvert_24h", False),
                            "precision_gps": p.get("precision_gps", "estimee"),
                            "source":        p.get("source", "Google Maps / OSM"),
                            "location": {
                                "type": "Point",
                                "coordinates": [lon, lat]
                            },
                        })
                    break

        if not all_docs:
            print("[DB] Aucune pharmacie trouvee.")
            return

        col.drop()
        col.create_index([("location", GEOSPHERE)])
        col.insert_many(all_docs)
        print(f"[DB] {len(all_docs)} pharmacies importees avec succes.")

        cfg = db["garde_config"]
        cfg.drop()
        cfg.insert_one({
            "reference_date":  REFERENCE_DATE,
            "reference_group": REFERENCE_GROUP,
            "nb_groupes":      4,
        })
        print("[DB] Configuration de garde enregistree.")

    except Exception as e:
        print(f"[DB] Erreur : {e}")


def get_current_garde_group(t):
    db     = get_db()
    config = db["garde_config"].find_one()
    if config:
        ref_date  = config["reference_date"]
        ref_group = config["reference_group"]
        nb        = config["nb_groupes"]
    else:
        ref_date  = REFERENCE_DATE
        ref_group = REFERENCE_GROUP
        nb        = 4
    delta_seconds = (t - ref_date).total_seconds()
    weeks_elapsed = int(delta_seconds // (7 * 24 * 3600))
    return ((ref_group - 1 + weeks_elapsed) % nb) + 1


def is_pharmacy_open(pharmacy, t, garde_group):
    if pharmacy.get("ouvert_24h"):
        return True, False, "Ouvert 24h/24"
    groupe   = pharmacy.get("groupe_garde")
    de_garde = (groupe == garde_group)
    if de_garde:
        return True, True, "De garde — ouvert 24h/24"
    weekday = t.weekday()
    h_min   = t.hour * 60 + t.minute
    if weekday == 6:
        ouvert = False
    else:
        matin = (7*60+30 <= h_min <= 12*60+30)
        aprem = (15*60   <= h_min <= 18*60+30)
        ouvert = matin or aprem
    return ouvert, False, "Lun-Sam : 7h30-12h30 / 15h-18h30"


def haversine_meters(lon1, lat1, lon2, lat2):
    R      = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp     = math.radians(lat2 - lat1)
    dl     = math.radians(lon2 - lon1)
    a      = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def getLocations(ex_lon, ex_lat, t):
    db          = get_db()
    col         = db["pharmacies"]
    garde_group = get_current_garde_group(t)
    results     = []
    for p in col.find():
        coords   = p["location"]["coordinates"]
        lon, lat = coords[0], coords[1]
        dist     = haversine_meters(ex_lon, ex_lat, lon, lat)
        ouvert, de_garde, horaire = is_pharmacy_open(p, t, garde_group)
        results.append({
            "nom":       p["nom"],
            "source":    p.get("source", "Google Maps / OSM"),
            "ouverture": horaire,
            "adresse":   p.get("adresse", ""),
            "details":   p.get("adresse", ""),
            "telephone": p.get("telephone", ""),
            "distance":  round(dist),
            "lon":       lon,
            "lat":       lat,
            "ouvert":    ouvert,
            "deGarde":   de_garde,
            "groupe":    p.get("groupe_garde"),
            "ville":     p.get("ville", ""),
        })
    def sort_key(loc):
        if loc["ouvert"] and loc["deGarde"]: return (0, loc["distance"])
        if loc["ouvert"]:                    return (1, loc["distance"])
        return                                      (2, loc["distance"])
    results.sort(key=sort_key)
    return results


@app.route('/')
def index():
    now            = datetime.datetime.now()
    garde_group    = get_current_garde_group(now)
    days_until_sat = (5 - now.weekday()) % 7
    if days_until_sat == 0 and now.hour >= 12:
        days_until_sat = 7
    next_garde = (now + datetime.timedelta(days=days_until_sat)).replace(hour=12, minute=0, second=0)
    hours_left = int((next_garde - now).total_seconds() // 3600)
    return render_template('index.html', garde_group=garde_group, hours_left=hours_left)


@app.route('/locate-items/<lon>/<lat>')
def locateItems(lon, lat):
    autofilter  = request.args.get('filter', '')
    locations   = getLocations(float(lon), float(lat), datetime.datetime.now())
    now         = datetime.datetime.now()
    garde_group = get_current_garde_group(now)
    return render_template('map-show.html',
                           longitude=lon, latitude=lat,
                           locations=locations,
                           garde_group=garde_group,
                           autofilter=autofilter)


@app.route('/api/locations/<lon>/<lat>')
def api_locations(lon, lat):
    return jsonify(getLocations(float(lon), float(lat), datetime.datetime.now()))


@app.route('/api/garde')
def api_garde():
    now = datetime.datetime.now()
    return jsonify({"groupe": get_current_garde_group(now), "timestamp": now.isoformat()})


@app.route('/api/status')
def api_status():
    db    = get_db()
    count = db["pharmacies"].count_documents({})
    garde = get_current_garde_group(datetime.datetime.now())
    return jsonify({"pharmacies": count, "groupe_garde": garde, "status": "ok"})


# with app.app_context():
#     auto_init_db()
@app.before_request
def init_on_first_request():
    global _db_initialized
    if not globals().get('_db_initialized'):
        auto_init_db()
        globals()['_db_initialized'] = True

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
