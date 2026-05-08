const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
let authToken = "";

function buildUrl(path) {
  return `${API_BASE}${path}`;
}

export function setAuthToken(token) {
  authToken = token || "";
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (authToken) {
    headers.set("Authorization", `Bearer ${authToken}`);
  }

  return fetch(buildUrl(path), {
    ...options,
    headers
  });
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

  const response = await apiFetch(`/search?k=${k}`, {
    method: "POST",
    body: formData
  });
  return parseResponse(response);
}

export async function getPlantCard(name) {
  const encoded = encodeURIComponent(name);
  const response = await apiFetch(`/plant/${encoded}?lang=it`);
  return parseResponse(response);
}

export async function getPlantProfile(name) {
  const encoded = encodeURIComponent(name);
  const response = await apiFetch(`/plant/${encoded}/profile`);
  return parseResponse(response);
}

export async function getSpeciesPreviews(speciesNames) {
  if (!speciesNames?.length) {
    return { previews: {} };
  }

  const params = new URLSearchParams();
  speciesNames.forEach((name) => params.append("names", name));
  const response = await apiFetch(`/species/previews?${params.toString()}`);
  return parseResponse(response);
}

export async function getSpeciesCommonNames(speciesNames) {
  if (!speciesNames?.length) {
    return { common_names: {} };
  }

  const params = new URLSearchParams();
  speciesNames.forEach((name) => params.append("names", name));
  const response = await apiFetch(`/species/common-names?${params.toString()}`);
  return parseResponse(response);
}

export async function askPlantCare(plantName, question) {
  const response = await apiFetch("/chat/plant-care", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plant_name: plantName, question, lang: "it" })
  });
  return parseResponse(response);
}

export async function verifyGoogleToken(idToken) {
  const response = await apiFetch("/auth/google", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token: idToken })
  });
  return parseResponse(response);
}

export async function saveMyPlant(plantName, userGivenName) {
  const response = await apiFetch("/user/plants", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plant_name: plantName, user_given_name: userGivenName })
  });
  return parseResponse(response);
}

export async function getMyPlants() {
  const response = await apiFetch("/user/plants");
  return parseResponse(response);
}

export async function deleteMyPlant(plantId) {
  const response = await apiFetch(`/user/plants/${plantId}`, {
    method: "DELETE"
  });
  return parseResponse(response);
}

export async function updateMyPlantFirstWaterDate(plantId, firstWateringDate) {
  const response = await apiFetch(`/user/plants/${plantId}/first-watering-date`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ first_watering_date: firstWateringDate })
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
