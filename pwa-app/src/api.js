const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function buildUrl(path) {
  return `${API_BASE}${path}`;
}

async function parseResponse(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data.detail || "Errore durante la chiamata API";
    throw new Error(message);
  }
  return data;
}

export async function searchPlantImage(file, k = 5) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(buildUrl(`/search?k=${k}`), {
    method: "POST",
    body: formData
  });
  return parseResponse(response);
}

export async function getPlantCard(name) {
  const encoded = encodeURIComponent(name);
  const response = await fetch(buildUrl(`/plant/${encoded}?lang=it`));
  return parseResponse(response);
}

export async function getPlantProfile(name) {
  const encoded = encodeURIComponent(name);
  const response = await fetch(buildUrl(`/plant/${encoded}/profile`));
  return parseResponse(response);
}

export async function askPlantCare(plantName, question) {
  const response = await fetch(buildUrl("/chat/plant-care"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plant_name: plantName, question, lang: "it" })
  });
  return parseResponse(response);
}

export function toAbsoluteImage(urlOrPath) {
  if (!urlOrPath) {
    return "";
  }
  if (urlOrPath.startsWith("http://") || urlOrPath.startsWith("https://")) {
    return urlOrPath;
  }
  return buildUrl(urlOrPath);
}
