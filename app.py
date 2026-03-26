import os, math, datetime, json, secrets
from functools import wraps
from bson import ObjectId
from flask import (Flask, render_template, request, jsonify,
                   send_from_directory, session, redirect, url_for)
from pymongo import MongoClient, GEOSPHERE

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
ADMIN_USER     = os.environ.get("ADMIN_USER",     "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD",  "pharmabf2026")
DB_NAME = "pharmacies_bf"
_client = None
_db_initialized = False

GARDE_CONFIG = {
    "Ouagadougou":    {"ref_date":datetime.datetime(2026,3,14,12,0,0),"ref_group":3,"nb":4},
    "Bobo-Dioulasso": {"ref_date":datetime.datetime(2026,3,14,12,0,0),"ref_group":2,"nb":4},
}

def get_db():
    global _client
    if _client is None:
        uri = os.environ.get("MONGO_URL","mongodb://mongo:lgaxccbQOSQWniPWCLtIKYcrYjeLVITm@mongodb.railway.internal:27017")
        _client = MongoClient(uri)
    return _client[DB_NAME]

def auto_init_db():
    try:
        db=get_db(); col=db["pharmacies"]
        if col.count_documents({})>0: return
        base_dirs=[os.path.dirname(os.path.abspath(__file__)),"/app","."]
        all_docs=[]
        for fname,ville in [("pharmaciesOuaga.json","Ouagadougou"),("pharmaciesBobo.json","Bobo-Dioulasso")]:
            for base in base_dirs:
                fp=os.path.join(base,fname)
                if os.path.exists(fp):
                    data=json.load(open(fp,encoding="utf-8"))
                    for p in data.get("pharmacies",[]):
                        lon,lat=p["geojson"]["coordinates"]
                        all_docs.append({"id":p["id"],"nom":p["nom"],"ville":p.get("ville",ville),
                            "telephone":p.get("telephone",""),"adresse":p.get("adresse_description",""),
                            "groupe_garde":p.get("groupe_garde"),"ouvert_24h":p.get("ouvert_24h",False),
                            "source":p.get("source","Google Maps / OSM"),
                            "location":{"type":"Point","coordinates":[lon,lat]}})
                    break
        if all_docs:
            col.drop(); col.create_index([("location",GEOSPHERE)]); col.insert_many(all_docs)
            print(f"[DB] {len(all_docs)} pharmacies importees.")
    except Exception as e: print(f"[DB] Erreur: {e}")

def garde_group(ville,t):
    cfg=GARDE_CONFIG.get(ville,GARDE_CONFIG["Ouagadougou"])
    weeks=int((t-cfg["ref_date"]).total_seconds()//(7*24*3600))
    return ((cfg["ref_group"]-1+weeks)%cfg["nb"])+1

def is_open(p,t):
    if p.get("ouvert_24h"): return True,False,"Ouvert 24h/24"
    gg=garde_group(p.get("ville","Ouagadougou"),t)
    if p.get("groupe_garde")==gg: return True,True,"De garde — ouvert 24h/24"
    wd=t.weekday(); hm=t.hour*60+t.minute
    if wd==6: return False,False,"Lun-Sam : 7h30-12h30 / 15h-18h30"
    ok=(7*60+30<=hm<=12*60+30)or(15*60<=hm<=18*60+30)
    return ok,False,"Lun-Sam : 7h30-12h30 / 15h-18h30"

def haversine(lon1,lat1,lon2,lat2):
    R=6371000;p1,p2=math.radians(lat1),math.radians(lat2)
    dp=math.radians(lat2-lat1);dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R*2*math.atan2(math.sqrt(a),math.sqrt(1-a))

def getLocations(ex_lon,ex_lat,t):
    results=[]
    for p in get_db()["pharmacies"].find():
        lon,lat=p["location"]["coordinates"]
        ouvert,de_garde,horaire=is_open(p,t)
        results.append({"_id":str(p["_id"]),"nom":p["nom"],"source":p.get("source",""),
            "ouverture":horaire,"adresse":p.get("adresse",""),"details":p.get("adresse",""),
            "telephone":p.get("telephone",""),"distance":round(haversine(ex_lon,ex_lat,lon,lat)),
            "lon":lon,"lat":lat,"ouvert":ouvert,"deGarde":de_garde,
            "groupe":p.get("groupe_garde"),"ville":p.get("ville","")})
    results.sort(key=lambda l:(0 if l["ouvert"]and l["deGarde"] else 1 if l["ouvert"] else 2,l["distance"]))
    return results

def login_required(f):
    @wraps(f)
    def deco(*a,**kw):
        if not session.get("admin_ok"): return redirect(url_for("admin_login"))
        return f(*a,**kw)
    return deco

@app.route("/admin/login",methods=["GET","POST"])
def admin_login():
    error=None
    if request.method=="POST":
        if request.form.get("username")==ADMIN_USER and request.form.get("password")==ADMIN_PASSWORD:
            session["admin_ok"]=True; return redirect(url_for("admin_dashboard"))
        error="Identifiants incorrects"
    return render_template("admin_login.html",error=error)

@app.route("/admin/logout")
def admin_logout():
    session.clear(); return redirect(url_for("admin_login"))

@app.route("/admin")
@login_required
def admin_dashboard():
    db=get_db(); now=datetime.datetime.now()
    stats={"total":db["pharmacies"].count_documents({}),"ouaga":db["pharmacies"].count_documents({"ville":"Ouagadougou"}),
           "bobo":db["pharmacies"].count_documents({"ville":"Bobo-Dioulasso"}),"h24":db["pharmacies"].count_documents({"ouvert_24h":True}),
           "go":garde_group("Ouagadougou",now),"gb":garde_group("Bobo-Dioulasso",now)}
    for g in range(1,5): stats[f"gr{g}"]=db["pharmacies"].count_documents({"groupe_garde":g})
    return render_template("admin.html",stats=stats)

@app.route("/admin/pharmacies")
@login_required
def admin_pharmacies():
    db=get_db();q={};v=request.args.get("ville","");g=request.args.get("groupe","");s=request.args.get("q","")
    if v: q["ville"]=v
    if g:
        try: q["groupe_garde"]=int(g)
        except: pass
    if s: q["$or"]=[{"nom":{"$regex":s,"$options":"i"}},{"adresse":{"$regex":s,"$options":"i"}}]
    docs=list(db["pharmacies"].find(q).sort("nom",1))
    for d in docs:
        d["_id"]=str(d["_id"]); d["lon"]=d["location"]["coordinates"][0]; d["lat"]=d["location"]["coordinates"][1]
    return jsonify(docs)

@app.route("/admin/api/pharmacie",methods=["POST"])
@login_required
def api_create():
    try:
        data=request.get_json()or{}
        for f in ["nom","ville","lon","lat"]:
            if not str(data.get(f,"")).strip(): return jsonify({"ok":False,"error":f"Requis: {f}"}),400
        doc={"nom":data["nom"].strip(),"ville":data["ville"].strip(),"telephone":data.get("telephone","").strip(),
             "adresse":data.get("adresse","").strip(),"groupe_garde":int(data["groupe_garde"]) if data.get("groupe_garde") else None,
             "ouvert_24h":bool(data.get("ouvert_24h",False)),"source":data.get("source","Admin"),
             "location":{"type":"Point","coordinates":[float(data["lon"]),float(data["lat"])]}}
        r=get_db()["pharmacies"].insert_one(doc)
        return jsonify({"ok":True,"id":str(r.inserted_id)})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/admin/api/pharmacie/<pid>",methods=["GET"])
@login_required
def api_get(pid):
    try:
        p=get_db()["pharmacies"].find_one({"_id":ObjectId(pid)})
        if not p: return jsonify({"ok":False,"error":"Introuvable"}),404
        p["_id"]=str(p["_id"]); p["lon"]=p["location"]["coordinates"][0]; p["lat"]=p["location"]["coordinates"][1]
        return jsonify({"ok":True,"pharmacie":p})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/admin/api/pharmacie/<pid>",methods=["PUT"])
@login_required
def api_update(pid):
    try:
        data=request.get_json()or{}
        upd={"$set":{"nom":data["nom"].strip(),"ville":data["ville"].strip(),"telephone":data.get("telephone","").strip(),
             "adresse":data.get("adresse","").strip(),"groupe_garde":int(data["groupe_garde"]) if data.get("groupe_garde") else None,
             "ouvert_24h":bool(data.get("ouvert_24h",False)),"source":data.get("source","Admin"),
             "location":{"type":"Point","coordinates":[float(data["lon"]),float(data["lat"])]}}}
        r=get_db()["pharmacies"].update_one({"_id":ObjectId(pid)},upd)
        return jsonify({"ok":r.matched_count>0})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/admin/api/pharmacie/<pid>",methods=["DELETE"])
@login_required
def api_delete(pid):
    try:
        r=get_db()["pharmacies"].delete_one({"_id":ObjectId(pid)})
        return jsonify({"ok":r.deleted_count>0})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/admin/api/garde",methods=["GET","POST"])
@login_required
def api_garde_admin():
    now=datetime.datetime.now()
    if request.method=="GET":
        return jsonify({"ok":True,"go":garde_group("Ouagadougou",now),"gb":garde_group("Bobo-Dioulasso",now)})
    data=request.get_json()or{}
    if "go" in data: GARDE_CONFIG["Ouagadougou"]["ref_group"]=int(data["go"]); GARDE_CONFIG["Ouagadougou"]["ref_date"]=now
    if "gb" in data: GARDE_CONFIG["Bobo-Dioulasso"]["ref_group"]=int(data["gb"]); GARDE_CONFIG["Bobo-Dioulasso"]["ref_date"]=now
    return jsonify({"ok":True})

@app.route("/admin/api/stats")
@login_required
def api_stats():
    db=get_db(); now=datetime.datetime.now()
    return jsonify({"total":db["pharmacies"].count_documents({}),"ouaga":db["pharmacies"].count_documents({"ville":"Ouagadougou"}),
        "bobo":db["pharmacies"].count_documents({"ville":"Bobo-Dioulasso"}),"h24":db["pharmacies"].count_documents({"ouvert_24h":True}),
        "go":garde_group("Ouagadougou",now),"gb":garde_group("Bobo-Dioulasso",now),
        "par_groupe":{str(g):db["pharmacies"].count_documents({"groupe_garde":g}) for g in range(1,5)}})

@app.route("/")
def index():
    now=datetime.datetime.now(); go=garde_group("Ouagadougou",now); gb=garde_group("Bobo-Dioulasso",now)
    dsat=(5-now.weekday())%7
    if dsat==0 and now.hour>=12: dsat=7
    ng=(now+datetime.timedelta(days=dsat)).replace(hour=12,minute=0,second=0)
    hl=int((ng-now).total_seconds()//3600)
    return render_template("index.html",groupe_ouaga=go,groupe_bobo=gb,hours_left=hl)

@app.route("/locate-items/<lon>/<lat>")
def locateItems(lon,lat):
    af=request.args.get("filter",""); vf=request.args.get("ville",""); now=datetime.datetime.now()
    locs=getLocations(float(lon),float(lat),now)
    MC={"ouaga":{"lat":12.3641,"lon":-1.5334,"zoom":13},"bobo":{"lat":11.1775,"lon":-4.2964,"zoom":13}}
    return render_template("map-show.html",longitude=lon,latitude=lat,locations=locs,
        groupe_ouaga=garde_group("Ouagadougou",now),groupe_bobo=garde_group("Bobo-Dioulasso",now),
        autofilter=af,ville_filtre=vf,map_center=MC.get(vf))

@app.route("/api/locations/<lon>/<lat>")
def api_locations(lon,lat): return jsonify(getLocations(float(lon),float(lat),datetime.datetime.now()))

@app.route("/api/garde")
def api_garde():
    now=datetime.datetime.now()
    return jsonify({"Ouagadougou":garde_group("Ouagadougou",now),"Bobo-Dioulasso":garde_group("Bobo-Dioulasso",now),"timestamp":now.isoformat()})

@app.route("/api/status")
def api_status():
    db=get_db(); now=datetime.datetime.now()
    return jsonify({"pharmacies":db["pharmacies"].count_documents({}),"garde_ouaga":garde_group("Ouagadougou",now),"garde_bobo":garde_group("Bobo-Dioulasso",now),"status":"ok"})

@app.route("/sw.js")
def sw(): return send_from_directory("static","sw.js",mimetype="application/javascript")

@app.before_request
def init_once():
    global _db_initialized
    if not _db_initialized: auto_init_db(); _db_initialized=True

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
