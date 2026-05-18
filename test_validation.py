from clean_invalid_images import IMAGE_SKIP_EXTENSIONS, is_valid_image_url
import logging

logging.basicConfig(level=logging.DEBUG)

url = "https://upload.wikimedia.org/wikipedia/commons/d/d5/Acanthus_mollis.jpg"
url_lower = url.lower()

print(f"URL: {url}")
print(f"URL lower: {url_lower}")
print(f"SKIP_EXTENSIONS: {IMAGE_SKIP_EXTENSIONS}")

for ext in IMAGE_SKIP_EXTENSIONS:
    result = url_lower.endswith(ext)
    print(f"  ends with '{ext}'? {result}")

print(f"\nis_valid_image_url(url): {is_valid_image_url(url)}")
