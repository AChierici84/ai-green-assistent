#!/usr/bin/env python3
"""Enrich care profile for specific species using OpenAI."""

import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

from build_plants_sqlite import extract_plant_profile_generic, upsert_plant

load_dotenv()

db_path = Path("data/plants.db")
model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
client = OpenAI()

species_to_enrich = ["Quercus phellos", "Ulmus parvifolia"]

conn = sqlite3.connect(db_path)

for species in species_to_enrich:
    print(f"\n[*] Enriching {species}...")
    try:
        # Extract profile using OpenAI generic function
        profile = extract_plant_profile_generic(client, model, species, {})
        print(f"    Profile extracted: {profile}")
        
        # Update DB with indexed=1 and the extracted profile
        upsert_plant(conn, species, indexed=True, profile=profile)
        print(f"    ✓ {species} updated in DB")
    except Exception as e:
        print(f"    ✗ Error enriching {species}: {e}")

conn.commit()
conn.close()

# Verify the update
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("""
    SELECT species_name, indexed, annaffiatura_gg, luce, temperatura, umidita
    FROM plants WHERE species_name IN (?, ?)
""", ("Quercus phellos", "Ulmus parvifolia"))
rows = cur.fetchall()
conn.close()

print("\n[✓] Final DB state:")
for name, indexed, ann_gg, luce, temp, umid in rows:
    print(f"  {name}: indexed={indexed}, annaffiatura_gg={ann_gg}, luce={luce}, temperatura={temp}, umidita={umid}")
