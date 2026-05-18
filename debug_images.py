from build_plant_rag import fetch_wiki_image_urls, fetch_wiki_extract

test_species = [
    ("Abies alba", "it"),
    ("Thymus vulgaris", "it"),
]

for species, lang in test_species:
    # Check if page exists
    extract = fetch_wiki_extract(species, lang)
    print(f"\n{species}:")
    print(f"  Page found: {extract is not None}")
    if extract:
        print(f"  Title: {extract[0]}")
    
    # Try to fetch images
    images = fetch_wiki_image_urls(species, lang)
    print(f"  Images: {len(images)} found")
    if images:
        for img in images[:1]:
            print(f"    - {img[-60:]}")
