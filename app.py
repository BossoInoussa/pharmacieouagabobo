import os, math, datetime, json, secrets
from functools import wraps
from bson import ObjectId
from flask import (Flask, render_template, request, jsonify,
                   send_from_directory, session, redirect, url_for, flash)
from pymongo import MongoClient, GEOSPHERE

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

ADMIN_USER     = os.environ.get("ADMIN_USER",     "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD",  "pharmabf2026")
DB_NAME        = "pharmacies_bf"
_client        = None
_db_initialized = False

GARDE_CONFIG_PAR_VILLE = {
    "Ouagadougou": {
        "reference_date":  datetime.datetime(2026, 3, 14, 12, 0, 0),
        "reference_group": 3, "nb_groupes": 4,
    },
    "Bobo-Dioulasso": {
        "reference_date":  datetime.datetime(2026, 3, 14, 12, 0, 0),
        "reference_group": 2, "nb_groupes": 4,
    },
}
GARDE_CONFIG_DEFAULT = {
    "reference_date": datetime.datetime(2026, 3, 14, 12, 0, 0),
    "reference_group": 1, "nb_groupes": 4,
}

def get_db():
    global _client
    if _client is None:
        uri = os.environ.get("MONGO_URL",
              "mongodb://mongo:lgaxccbQOSQWniPWCLtIKYcrYjeLVITm@mongodb.railway.internal:27017")
        _client = MongoClient(uri)
    return _client[DB_NAME]

def auto_init_db():
    try:
        db = get_db(); col = db["pharmacies"]
        if col.count_documents({}) > 0: return
        base_dirs = [os.path.dirname(os.path.abspath(__file__)), "/app", "."]
        all_docs = []
        for filename, ville in [("pharmaciesOuaga.json","Ouagadougou"),("pharmaciesBobo.json","Bobo-Dioulasso")]:
            for base in base_dirs:
                fp = os.path.join(base, filename)
                if os.path.exists(fp):
                    data = json.load(open(fp, encoding="utf-8"))
                    for p in data.get("pharmacies", []):
                        lon,lat = p["geojson"]["coordinates"]
                        all_docs.append({"id":p["id"],"nom":p["nom"],"ville":p.get("ville",ville),
                            "telephone":p.get("telephone",""),"adresse":p.get("adresse_description",""),
                            "groupe_garde":p.get("groupe_garde"),"ouvert_24h":p.get("ouvert_24h",False),
                            "precision_gps":p.get("precision_gps","estimee"),
                            "source":p.get("source","Google Maps / OSM"),
                            "location":{"type":"Point","coordinates":[lon,lat]}})
                    break
        if all_docs:
            col.drop(); col.create_index([("location",GEOSPHERE)]); col.insert_many(all_docs)
            print(f"[DB] {len(all_docs)} pharmacies importees.")
    except Exception as e:
        print(f"[DB] Erreur : {e}")

def get_garde_group_pour_ville(ville, t):
    cfg = GARDE_CONFIG_PAR_VILLE.get(ville, GARDE_CONFIG_DEFAULT)
    weeks = int((t - cfg["reference_date"]).total_seconds() // (7*24*3600))
    return ((cfg["reference_group"]-1 + weeks) % cfg["nb_groupes"]) + 1

def is_pharmacy_open(p, t):
    if p.get("ouvert_24h"): return True, False, "Ouvert 24h/24"
    ville = p.get("ville","Ouagadougou")
    gg = get_garde_group_pour_ville(ville, t)
    if p.get("groupe_garde") == gg: return True, True, "De garde — ouvert 24h/24"
    wd = t.weekday(); hm = t.hour*60+t.minute
    if wd==6: return False, False, "Lun-Sam : 7h30-12h30 / 15h-18h30"
    ok = (7*60+30<=hm<=12*60+30) or (15*60<=hm<=18*60+30)
    return ok, False, "Lun-Sam : 7h30-12h30 / 15h-18h30"

def haversine_meters(lon1,lat1,lon2,lat2):
    R=6371000; p1,p2=math.radians(lat1),math.radians(lat2)
    dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R*2*math.atan2(math.sqrt(a),math.sqrt(1-a))

def getLocations(ex_lon,ex_lat,t):
    results=[]
    for p in get_db()["pharmacies"].find():
        lon,lat = p["location"]["coordinates"]
        dist = haversine_meters(ex_lon,ex_lat,lon,lat)
        ouvert,de_garde,horaire = is_pharmacy_open(p,t)
        results.append({"nom":p["nom"],"source":p.get("source",""),"ouverture":horaire,
            "adresse":p.get("adresse",""),"details":p.get("adresse",""),
            "telephone":p.get("telephone",""),"distance":round(dist),
            "lon":lon,"lat":lat,"ouvert":ouvert,"deGarde":de_garde,
            "groupe":p.get("groupe_garde"),"ville":p.get("ville",""),
            "_id":str(p["_id"])})
    results.sort(key=lambda l:(0 if l["ouvert"] and l["deGarde"] else 1 if l["ouvert"] else 2, l["distance"]))
    return results

# ── Auth ─────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def deco(*a,**kw):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*a,**kw)
    return deco

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    error = None
    if request.method=="POST":
        if request.form.get("username")==ADMIN_USER and request.form.get("password")==ADMIN_PASSWORD:
            session["admin_logged_in"]=True
            return redirect(url_for("admin_dashboard"))
        error = "Identifiants incorrects"
    return render_template("admin_login.html", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.clear(); return redirect(url_for("admin_login"))

# ── Admin pages ──────────────────────────────────────────────────
@app.route("/admin")
@login_required
def admin_dashboard():
    db=get_db(); now=datetime.datetime.now()
    stats={"total":db["pharmacies"].count_documents({}),"ouaga":db["pharmacies"].count_documents({"ville":"Ouagadougou"}),
           "bobo":db["pharmacies"].count_documents({"ville":"Bobo-Dioulasso"}),"garde24h":db["pharmacies"].count_documents({"ouvert_24h":True}),
           "groupe_ouaga":get_garde_group_pour_ville("Ouagadougou",now),"groupe_bobo":get_garde_group_pour_ville("Bobo-Dioulasso",now)}
    for g in range(1,5): stats[f"gr{g}"]=db["pharmacies"].count_documents({"groupe_garde":g})
    return render_template("admin.html", stats=stats)

# ── Admin API CRUD ───────────────────────────────────────────────
@app.route("/admin/pharmacies")
@login_required
def admin_pharmacies():
    db=get_db(); ville=request.args.get("ville",""); groupe=request.args.get("groupe",""); q=request.args.get("q","")
    query={}
    if ville: query["ville"]=ville
    if groupe:
        try: query["groupe_garde"]=int(groupe)
        except: pass
    if q: query["$or"]=[{"nom":{"$regex":q,"$options":"i"}},{"adresse":{"$regex":q,"$options":"i"}}]
    pharmacies=list(db["pharmacies"].find(query).sort("nom",1))
    for p in pharmacies:
        p["_id"]=str(p["_id"])
        p["lon"]=p["location"]["coordinates"][0]
        p["lat"]=p["location"]["coordinates"][1]
    return jsonify(pharmacies)

@app.route("/admin/api/pharmacie", methods=["POST"])
@login_required
def api_create_pharmacie():
    try:
        data=request.get_json(); db=get_db(); col=db["pharmacies"]
        for f in ["nom","ville","lon","lat"]:
            if not str(data.get(f,"")):return jsonify({"ok":False,"error":f"Requis: {f}"}),400
        lon,lat=float(data["lon"]),float(data["lat"])
        doc={"nom":data["nom"].strip(),"ville":data["ville"].strip(),"telephone":data.get("telephone","").strip(),
             "adresse":data.get("adresse","").strip(),"groupe_garde":int(data["groupe_garde"]) if data.get("groupe_garde") else None,
             "ouvert_24h":bool(data.get("ouvert_24h",False)),"precision_gps":data.get("precision_gps","elevee"),
             "source":data.get("source","Admin"),"location":{"type":"Point","coordinates":[lon,lat]}}
        r=col.insert_one(doc)
        return jsonify({"ok":True,"id":str(r.inserted_id)})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/admin/api/pharmacie/<pid>", methods=["GET"])
@login_required
def api_get_pharmacie(pid):
    try:
        p=get_db()["pharmacies"].find_one({"_id":ObjectId(pid)})
        if not p: return jsonify({"ok":False,"error":"Introuvable"}),404
        p["_id"]=str(p["_id"]); p["lon"]=p["location"]["coordinates"][0]; p["lat"]=p["location"]["coordinates"][1]
        return jsonify({"ok":True,"pharmacie":p})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/admin/api/pharmacie/<pid>", methods=["PUT"])
@login_required
def api_update_pharmacie(pid):
    try:
        data=request.get_json(); lon,lat=float(data["lon"]),float(data["lat"])
        upd={"$set":{"nom":data["nom"].strip(),"ville":data["ville"].strip(),"telephone":data.get("telephone","").strip(),
             "adresse":data.get("adresse","").strip(),"groupe_garde":int(data["groupe_garde"]) if data.get("groupe_garde") else None,
             "ouvert_24h":bool(data.get("ouvert_24h",False)),"precision_gps":data.get("precision_gps","elevee"),
             "source":data.get("source","Admin"),"location":{"type":"Point","coordinates":[lon,lat]}}}
        r=get_db()["pharmacies"].update_one({"_id":ObjectId(pid)},upd)
        return jsonify({"ok":True}) if r.matched_count else (jsonify({"ok":False,"error":"Introuvable"}),404)
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/admin/api/pharmacie/<pid>", methods=["DELETE"])
@login_required
def api_delete_pharmacie(pid):
    try:
        r=get_db()["pharmacies"].delete_one({"_id":ObjectId(pid)})
        return jsonify({"ok":True}) if r.deleted_count else (jsonify({"ok":False,"error":"Introuvable"}),404)
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/admin/api/garde", methods=["GET","POST"])
@login_required
def api_admin_garde():
    now=datetime.datetime.now()
    if request.method=="GET":
        return jsonify({"ok":True,
            "groupe_actuel":{"Ouagadougou":get_garde_group_pour_ville("Ouagadougou",now),
                             "Bobo-Dioulasso":get_garde_group_pour_ville("Bobo-Dioulasso",now)}})
    data=request.get_json()
    for ville,key in [("Ouagadougou","ouagadougou"),("Bobo-Dioulasso","bobo_dioulasso")]:
        if f"{key}_groupe" in data:
            GARDE_CONFIG_PAR_VILLE[ville]["reference_group"]=int(data[f"{key}_groupe"])
            GARDE_CONFIG_PAR_VILLE[ville]["reference_date"]=now
    return jsonify({"ok":True,"message":"Configuration mise à jour"})

@app.route("/admin/api/stats")
@login_required
def api_admin_stats():
    db=get_db(); now=datetime.datetime.now()
    return jsonify({"total":db["pharmacies"].count_documents({}),"ouaga":db["pharmacies"].count_documents({"ville":"Ouagadougou"}),
        "bobo":db["pharmacies"].count_documents({"ville":"Bobo-Dioulasso"}),"garde24h":db["pharmacies"].count_documents({"ouvert_24h":True}),
        "groupe_ouaga":get_garde_group_pour_ville("Ouagadougou",now),"groupe_bobo":get_garde_group_pour_ville("Bobo-Dioulasso",now),
        "par_groupe":{str(g):db["pharmacies"].count_documents({"groupe_garde":g}) for g in range(1,5)}})

# ── Public routes ────────────────────────────────────────────────
@app.route("/")
def index():
    now=datetime.datetime.now()
    go=get_garde_group_pour_ville("Ouagadougou",now); gb=get_garde_group_pour_ville("Bobo-Dioulasso",now)
    dsat=(5-now.weekday())%7
    if dsat==0 and now.hour>=12: dsat=7
    ng=(now+datetime.timedelta(days=dsat)).replace(hour=12,minute=0,second=0)
    hl=int((ng-now).total_seconds()//3600)
    return render_template("index.html",groupe_ouaga=go,groupe_bobo=gb,hours_left=hl)

@app.route("/locate-items/<lon>/<lat>")
def locateItems(lon,lat):
    af=request.args.get("filter",""); vf=request.args.get("ville","")
    now=datetime.datetime.now()
    locs=getLocations(float(lon),float(lat),now)
    go=get_garde_group_pour_ville("Ouagadougou",now); gb=get_garde_group_pour_ville("Bobo-Dioulasso",now)
    MC={"ouaga":{"lat":12.3641,"lon":-1.5334,"zoom":13},"bobo":{"lat":11.1775,"lon":-4.2964,"zoom":13}}
    return render_template("map-show.html",longitude=lon,latitude=lat,locations=locs,
        groupe_ouaga=go,groupe_bobo=gb,autofilter=af,ville_filtre=vf,map_center=MC.get(vf))

@app.route("/api/locations/<lon>/<lat>")
def api_locations(lon,lat): return jsonify(getLocations(float(lon),float(lat),datetime.datetime.now()))

@app.route("/api/garde")
def api_garde():
    now=datetime.datetime.now()
    return jsonify({"Ouagadougou":get_garde_group_pour_ville("Ouagadougou",now),
        "Bobo-Dioulasso":get_garde_group_pour_ville("Bobo-Dioulasso",now),"timestamp":now.isoformat()})

@app.route("/api/status")
def api_status():
    db=get_db(); now=datetime.datetime.now()
    return jsonify({"pharmacies":db["pharmacies"].count_documents({}),"garde_ouaga":get_garde_group_pour_ville("Ouagadougou",now),
        "garde_bobo":get_garde_group_pour_ville("Bobo-Dioulasso",now),"status":"ok"})

@app.route("/sw.js")
def sw(): return send_from_directory("static","sw.js",mimetype="application/javascript")

@app.before_request
def init_on_first_request():
    global _db_initialized
    if not _db_initialized: auto_init_db(); _db_initialized=True

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
