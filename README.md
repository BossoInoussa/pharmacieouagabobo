# PharmaBF — Guide d'installation

## Prérequis
- Python 3.9+
- MongoDB (installé et actif sur localhost:27017)
- pip

## Installation

```bash
# 1. Créer un environnement virtuel
python -m venv venv
source venv/bin/activate       # Linux/macOS
venv\Scripts\activate          # Windows

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Placer les fichiers JSON dans le même dossier
#    pharmaciesOuaga.json
#    pharmaciesBobo.json

# 4. Importer les données dans MongoDB (une seule fois)
python import_data.py

# 5. Lancer l'application
python app.py
```

Ouvrir http://localhost:5000 dans le navigateur.

---

## Structure du projet

```
pharmacie_bf/
├── app.py              # Serveur Flask + logique métier
├── import_data.py      # Import MongoDB (run once)
├── requirements.txt
├── pharmaciesOuaga.json
├── pharmaciesBobo.json
├── static/
│   └── image/
│       └── home.png
└── templates/
    ├── index.html      # Page d'accueil
    └── map-show.html   # Carte + liste des résultats
```

---

## Logique de garde

- **4 groupes** tournants, une semaine chacun
- **Début de semaine** : samedi à 12h00
- Pendant leur semaine de garde, les pharmacies du groupe actif sont **ouvertes 24h/24**
- Les autres pharmacies suivent les horaires normaux : **Lun–Sam 7h30–12h30 / 15h–18h30**

### Ajuster le groupe de référence
Dans `import_data.py`, modifier :
```python
REFERENCE_DATE  = datetime(2025, 3, 8, 12, 0, 0)   # Samedi de référence
REFERENCE_GROUP = 1                                   # Groupe actif ce samedi-là
```

---

## API JSON

```
GET /api/locations/<lon>/<lat>
```
Retourne la liste JSON des pharmacies triées par distance.

---

## Sources des données
- Google Maps Places API
- OpenStreetMap (OSM)
- ANAC Burkina Faso
- Orange Burkina Faso
