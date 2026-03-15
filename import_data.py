"""
Script d'importation des pharmacies dans MongoDB.
Exécuter une seule fois : python import_data.py
"""

import json
from pymongo import MongoClient, GEOSPHERE
from datetime import datetime

# === CONFIGURATION ===
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "pharmacies_bf"

# Date de référence : le samedi 8 mars 2025 à 12h00,
# le groupe 1 commence sa semaine de garde.
# Ajustez si vous connaissez la vraie date de référence.
REFERENCE_DATE = datetime(2025, 3, 8, 12, 0, 0)
REFERENCE_GROUP = 1


def load_pharmacies():
    all_pharmacies = []

    files = [
        ("pharmaciesOuaga.json", "Ouagadougou"),
        ("pharmaciesBobo.json",  "Bobo-Dioulasso"),
    ]

    for filename, ville in files:
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            pharmacies = data.get("pharmacies", [])
            print(f"  {ville}: {len(pharmacies)} pharmacies chargées depuis {filename}")
            all_pharmacies.extend(pharmacies)
        except FileNotFoundError:
            print(f"  ATTENTION : fichier {filename} introuvable.")

    return all_pharmacies


def build_document(p):
    """Transforme une entrée JSON en document MongoDB."""
    lon = p["geojson"]["coordinates"][0]
    lat = p["geojson"]["coordinates"][1]

    return {
        "id":          p["id"],
        "nom":         p["nom"],
        "ville":       p.get("ville", "Inconnue"),
        "telephone":   p.get("telephone", ""),
        "adresse":     p.get("adresse_description", ""),
        "groupe_garde":p.get("groupe_garde"),          # 1-4 ou None
        "ouvert_24h":  p.get("ouvert_24h", False),     # pour pharmacies hors Ouaga/Bobo
        "precision_gps": p.get("precision_gps", "estimée"),
        "source":      p.get("source", "Google Maps / OSM"),
        # GeoJSON 2dsphere pour requêtes géospatiales
        "location": {
            "type": "Point",
            "coordinates": [lon, lat]
        },
        "updated_at": datetime.utcnow(),
    }


def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col = db["pharmacies"]

    # Supprimer les anciennes données
    col.drop()
    print("Collection 'pharmacies' réinitialisée.")

    # Créer l'index géospatial
    col.create_index([("location", GEOSPHERE)])
    print("Index géospatial 2dsphere créé.")

    # Charger et insérer
    pharmacies = load_pharmacies()
    if not pharmacies:
        print("Aucune pharmacie à importer.")
        return

    docs = [build_document(p) for p in pharmacies]
    col.insert_many(docs)
    print(f"\n✅ {len(docs)} pharmacies importées dans '{DB_NAME}.pharmacies'.")

    # Stocker la configuration de garde
    config_col = db["garde_config"]
    config_col.drop()
    config_col.insert_one({
        "reference_date":  REFERENCE_DATE,
        "reference_group": REFERENCE_GROUP,
        "nb_groupes":      4,
        "note": (
            "La semaine de garde commence le samedi à 12h00 "
            "et dure 7 jours. 4 groupes tournants."
        ),
    })
    print("✅ Configuration de garde enregistrée.")

    client.close()


if __name__ == "__main__":
    main()
