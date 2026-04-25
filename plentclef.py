import os
import torch
import open_clip
import faiss
from PIL import Image
from collections import defaultdict

class PlentClefIndex():
    def __init__(self, model_name, index_path,index_cache):
        self.model, self.preprocess, self.tokenizer = open_clip.create_model_and_transforms(
            model_name=model_name,
            pretrained="laion2b_s34b_b79k"
        )
        self.index = faiss.read_index(index_path)
        self.model.eval()
        data = torch.load(index_cache, map_location="cpu")
        self.plantclef_image_embeddings = data["embeddings"]
        self.plantclef_labels = data["labels"]

    def embed_image(self,path):
        img = self.preprocess(Image.open(path).convert("RGB")).unsqueeze(0)# Move image to the same device as the model
        with torch.no_grad():
            e = self.model.encode_image(img)
            e = e / e.norm(dim=-1, keepdim=True)
        return e.cpu().numpy().astype("float32")

    def search(self,path, labels, k=5):
        q = self.embed_image(path)
        sims, idxs = self.index.search(q, k)  # [1, k]

        aggregated_results = defaultdict(lambda: {'score_sum': 0.0, 'image_paths': []})

        for score, idx in zip(sims[0], idxs[0]):
            species_label = labels[idx]
            aggregated_results[species_label]['score_sum'] += score # Append the image path

        # Convert aggregated results to a list of (category, total_score, image_paths_list) tuples
        final_results = []
        for category, data in aggregated_results.items():
            final_results.append((category, data['score_sum'], data['image_paths']))

        # Sort by total score in descending order
        final_results.sort(key=lambda x: x[1], reverse=True)

        return final_results
