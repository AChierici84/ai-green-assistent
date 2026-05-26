function getApiBase() {
  if (import.meta.env.VITE_API_BASE) {
    return import.meta.env.VITE_API_BASE;
  }
  if (window.location.port === "5173") {
    return "http://localhost:8000";
  }
  return "";
}

const API_BASE = getApiBase();
let authToken = "";
let unauthorizedHandler = null;

function buildUrl(path) {
  return `${API_BASE}${path}`;
}

export function setAuthToken(token) {
  authToken = token || "";
}

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = typeof handler === "function" ? handler : null;
}

async function apiFetch(path, options = {}) {
  async function doFetch() {
    const headers = new Headers(options.headers || {});
    if (authToken) {
      headers.set("Authorization", `Bearer ${authToken}`);
    }

    return fetch(buildUrl(path), {
      ...options,
      headers
    });
  }

  const response = await doFetch();

  if (
    response.status === 401
    && !options.__skipAuthRefresh
    && unauthorizedHandler
  ) {
    const refreshed = await unauthorizedHandler();
    if (refreshed) {
      return doFetch();
    }
  }

  return response;
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

export async function getPlantCard(name, options = {}) {
  const encoded = encodeURIComponent(name);
  const refreshCache = Boolean(options?.refreshCache);
  const query = refreshCache ? "?lang=it&refresh_cache=1" : "?lang=it";
  const response = await apiFetch(`/plant/${encoded}${query}`);
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

export async function getSpeciesBuildStatus(speciesName) {
  const encoded = encodeURIComponent(speciesName);
  const response = await apiFetch(`/species/${encoded}/build-status`);
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


export async function uploadMyPlantPhoto(plantId, file) {
  const formData = new FormData();
  formData.append("file", file);
  // Nessun header custom: Authorization viene gestito da apiFetch
  const response = await apiFetch(`/user/plants/${plantId}/photo`, {
    method: "POST",
    body: formData,
  });
  return parseResponse(response);
}

export async function getAdminConsoleData(limit = 300, chartDays = 30) {
  const safeLimit = Math.max(1, Math.min(1000, Number(limit) || 300));
  const normalizedDays = Number(chartDays);
  const safeChartDays = [7, 30, 90].includes(normalizedDays) ? normalizedDays : 30;
  const response = await apiFetch(`/admin/console?limit=${safeLimit}&chart_days=${safeChartDays}`);
  return parseResponse(response);
}

export async function triggerAdminSpeciesBuild(speciesName, forceRebuild = false) {
  const response = await apiFetch("/admin/species/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      species_name: String(speciesName || "").trim(),
      force_rebuild: Boolean(forceRebuild),
    }),
  });
  return parseResponse(response);
}

export async function getApiHealth() {
  const response = await apiFetch("/health");
  return parseResponse(response);
}

export async function logRecognition(payload) {
  const response = await apiFetch("/recognitions/log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chosen_species: payload?.chosen_species || "",
      used_openai: Boolean(payload?.used_openai),
      image_url: payload?.image_url || null,
      recognition_ms: Number.isFinite(Number(payload?.recognition_ms))
        ? Math.max(0, Math.round(Number(payload?.recognition_ms)))
        : null,
    }),
  });
  return parseResponse(response);
}

export function toAbsoluteImage(urlOrPath) {
  if (!urlOrPath) {
    return "";
  }
  // Gestione Google Drive: usa il proxy se l'URL è di tipo drive.google.com/uc?export=view&id=...
  const driveMatch = String(urlOrPath).match(/https?:\/\/drive\.google\.com\/uc\?[^#]*[?&]id=([a-zA-Z0-9_-]{10,})/);
  if (driveMatch && driveMatch[1]) {
    return buildUrl(`/proxy/drive-image/${driveMatch[1]}`);
  }
  if (urlOrPath.startsWith("http://") || urlOrPath.startsWith("https://")) {
    return urlOrPath;
  }
  return buildUrl(urlOrPath);
}

export function toOptimizedImage(urlOrPath, width = 640) {
  const absolute = toAbsoluteImage(urlOrPath);
  // Keep original URL to avoid broken thumbnails for non-standard Wikimedia assets.
  return absolute;
}
