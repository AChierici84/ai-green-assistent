#!/usr/bin/env python3
"""Find plants with no images in the RAG database."""

import json
from pathlib import Path
import chromadb

from build_plant_rag import COLLECTION_NAME, RAG_DIR

client = chromadb.PersistentClient(path=str(RAG_DIR))
collection = client.get_collection(name=COLLECTION_NAME)

# Get all metadata
results = collection.get(include=["metadatas"])
metadatas = results.get("metadatas", []) or []

species_images = {}
for meta in metadatas:
    if meta:
        species_name = meta.get("species_name", "").strip()
        if not species_name:
            continue
        
        if species_name not in species_images:
            image_paths = json.loads(meta.get("image_paths", "[]") or "[]")
            species_images[species_name] = image_paths

# Filter species without images
no_images = {s: imgs for s, imgs in species_images.items() if not imgs}
has_images = {s: imgs for s, imgs in species_images.items() if imgs}

print(f"Specie con immagini: {len(has_images)}")
print(f"Specie SENZA immagini: {len(no_images)}\n")

if no_images:
    print("Specie senza immagini:")
    for species in sorted(no_images.keys())[:20]:  # Show first 20
        print(f"  - {species}")
    if len(no_images) > 20:
        print(f"  ... e {len(no_images) - 20} altre")
