import { useEffect, useMemo, useRef, useState } from "react";
import { GoogleLogin } from "@react-oauth/google";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  askPlantCare,
  getAdminConsoleData,
  deleteMyPlant,
  getMyPlants,
  getPlantCard,
  getPlantProfile,
  getSpeciesBuildStatus,
  saveMyPlant,
  setAuthToken,
  getSpeciesCommonNames,
  getSpeciesPreviews,
  searchPlantImage,
  updateMyPlantFirstWaterDate,
  uploadMyPlantPhoto,
  verifyGoogleToken,
  setUnauthorizedHandler,
  toAbsoluteImage,
  toOptimizedImage,
  logRecognition
} from "./api";

const AUTH_STORAGE_KEY = "clorofilla-auth";

const PROFILE_LABELS = {
  annaffiatura_gg: "Annaffiatura",
  annaffiatura_time: "Momento annaffiatura",
  luce: "Luce",
  temperatura: "Temperatura",
  umidita: "Umidita",
  altezza_media: "Altezza media",
  pulizia: "Pulizia",
  terriccio: "Terriccio",
  concimazione: "Concimazione",
  prevenzione: "Prevenzione"
};

const PROFILE_ICONS = {
  annaffiatura_gg: "\u{1F4A7}",
  annaffiatura_time: "\u{23F0}",
  luce: "\u{2600}\u{FE0F}",
  temperatura: "\u{1F321}\u{FE0F}",
  umidita: "\u{1F4A8}",
  altezza_media: "\u{1F332}",
  pulizia: "\u{1F9F9}",
  terriccio: "\u{1F331}",
  concimazione: "\u{1F9EA}",
  prevenzione: "\u{1F6E1}\u{FE0F}"
};

const PROFILE_DESCRIPTIONS = {
  annaffiatura_gg: "Ogni quanti giorni annaffiare la pianta.",
  annaffiatura_time: "Il momento migliore della giornata per annaffiare.",
  luce: "Tipo di esposizione alla luce solare consigliata.",
  temperatura: "Intervallo di temperatura ideale per la crescita.",
  umidita: "Livello di umidità ambientale preferito.",
  altezza_media: "Altezza media raggiunta dalla pianta adulta.",
  pulizia: "Frequenza e modalità di pulizia delle foglie.",
  terriccio: "Tipo di substrato o terriccio consigliato.",
  concimazione: "Frequenza e tipo di concimazione raccomandati.",
  prevenzione: "Principali parassiti e malattie da prevenire."
};

function shouldAutoSelectTopResult(results) {
  if (!results || results.length === 0) {
    return false;
  }
  if (results.length === 1) {
    return true;
  }

  const [top, second] = results;
  const topScore = Number(top?.displayScore ?? top?.score ?? 0);
  const secondScore = Number(second?.displayScore ?? second?.score ?? 0);

  return topScore - secondScore >= 0.08;
}

function normalizeSearchResults(rawResults) {
  const sorted = [...(rawResults || [])].sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  if (!sorted.length) {
    return [];
  }

  const scores = sorted.map((item) => Number(item?.score ?? 0));
  const maxScore = Math.max(...scores);

  return sorted.map((item) => {
    const score = Number(item?.score ?? 0);
    // Keep bars readable by always scaling against the best result in the current list.
    const displayScore = maxScore > 0 ? Math.max(0, Math.min(1, score / maxScore)) : 0;

    return {
      ...item,
      displayScore,
    };
  });
}

function parseWateringIntervalDays(intervalDays) {
  let days = Number(intervalDays);
  if (Number.isNaN(days)) {
    const textValue = String(intervalDays || "").toLowerCase();
    const match = textValue.match(/(\d+(?:[.,]\d+)?)/);
    if (match?.[1]) {
      days = Number(match[1].replace(",", "."));
    }
  }

  if (Number.isNaN(days)) {
    return null;
  }

  return Math.max(1, Math.round(days));
}

function formatDurationMs(ms) {
  const value = Number(ms || 0);
  if (!Number.isFinite(value) || value <= 0) {
    return "-";
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)} s`;
  }
  return `${Math.round(value)} ms`;
}

function formatDateYYYYMMDD(dateValue) {
  const year = dateValue.getFullYear();
  const month = String(dateValue.getMonth() + 1).padStart(2, "0");
  const day = String(dateValue.getDate()).padStart(2, "0");
  return `${year}${month}${day}`;
}

function formatISOToInputDate(value) {
  const parsed = new Date(value || "");
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  const year = parsed.getFullYear();
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseJwtPayload(token) {
  try {
    const parts = String(token || "").split(".");
    if (parts.length < 2) {
      return null;
    }
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const json = window.atob(padded);
    return JSON.parse(json);
  } catch {
    return null;
  }
}

export default function App({ googleClientIdConfigured = false }) {
  const [auth, setAuth] = useState(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [isAuthMenuOpen, setIsAuthMenuOpen] = useState(false);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [isDragActive, setIsDragActive] = useState(false);
  const [imageIndex, setImageIndex] = useState(0);
  const fileInputRef = useRef(null);
  const cameraInputRef = useRef(null);

  const [searchResults, setSearchResults] = useState([]);
  const [searchFile, setSearchFile] = useState(null);
  const [speciesPreviews, setSpeciesPreviews] = useState({});
  const [speciesCommonNames, setSpeciesCommonNames] = useState({});
  const [selectedSpecies, setSelectedSpecies] = useState("");
  const [showSpeciesGrid, setShowSpeciesGrid] = useState(true);
  const [expandedProfileKey, setExpandedProfileKey] = useState("");

  const [plantCard, setPlantCard] = useState(null);
  const [plantProfile, setPlantProfile] = useState(null);
  const [draftBuildMessage, setDraftBuildMessage] = useState("");
  const draftPollTimerRef = useRef(null);
  const selectedSpeciesRef = useRef("");

  const [question, setQuestion] = useState("");
  const [chatAnswer, setChatAnswer] = useState("");
  const [userPlantName, setUserPlantName] = useState("");
  const [myPlants, setMyPlants] = useState([]);
  const [isMyPlantsListCollapsed, setIsMyPlantsListCollapsed] = useState(false);
  const [saveStatus, setSaveStatus] = useState("");
  const [activeView, setActiveView] = useState("recognize");
  const [adminConsole, setAdminConsole] = useState(null);
  const [adminChartDays, setAdminChartDays] = useState(30);
  const [lastSearchUsedOpenAI, setLastSearchUsedOpenAI] = useState(false);
  const [lastSearchDurationMs, setLastSearchDurationMs] = useState(null);
  const [selectedMyPlant, setSelectedMyPlant] = useState(null);
  const [myPlantCard, setMyPlantCard] = useState(null);
  const [myPlantProfile, setMyPlantProfile] = useState(null);
  const [wateringSchedule, setWateringSchedule] = useState([]);
  const [wateringPlan, setWateringPlan] = useState(null);
  const [isEditingFirstWaterDate, setIsEditingFirstWaterDate] = useState(false);
  const [firstWaterDateInput, setFirstWaterDateInput] = useState("");
  const [deletingPlantId, setDeletingPlantId] = useState(null);
  const [uploadingPhotoId, setUploadingPhotoId] = useState(null);
  const plantPhotoInputRef = useRef(null);
  const plantPhotoTargetIdRef = useRef(null);
  const authMenuRef = useRef(null);
  const refreshPromiseRef = useRef(null);
  const lastSearchDurationRef = useRef(null);

  const [busy, setBusy] = useState({
    search: false,
    plant: false,
    chat: false,
    savePlant: false,
    myPlants: false,
    myPlantDetail: false,
    adminConsole: false,
    updateFirstWaterDate: false
  });
  const [error, setError] = useState("");
  const [searchStepIndex, setSearchStepIndex] = useState(0);

  const searchSteps = ["Leggo i dettagli della foglia", "Confronto con le specie note", "Preparo i risultati migliori"];

  const activeChatPlantName = activeView === "my-plants"
    ? (selectedMyPlant?.plant_name || "")
    : "";

  useEffect(() => {
    selectedSpeciesRef.current = selectedSpecies;
  }, [selectedSpecies]);

  useEffect(() => {
    return () => {
      if (draftPollTimerRef.current) {
        window.clearInterval(draftPollTimerRef.current);
        draftPollTimerRef.current = null;
      }
    };
  }, []);

  function stopDraftPolling() {
    if (draftPollTimerRef.current) {
      window.clearInterval(draftPollTimerRef.current);
      draftPollTimerRef.current = null;
    }
  }

  function startDraftPolling(speciesName) {
    stopDraftPolling();
    let attempts = 0;
    const maxAttempts = 45;

    draftPollTimerRef.current = window.setInterval(async () => {
      attempts += 1;
      if (selectedSpeciesRef.current.toLowerCase() !== speciesName.toLowerCase()) {
        stopDraftPolling();
        return;
      }

      try {
        const statusData = await getSpeciesBuildStatus(speciesName);
        const status = statusData?.status?.status || "not_started";

        if (statusData?.ready || status === "completed") {
          const [card, profile] = await Promise.all([
            getPlantCard(speciesName),
            getPlantProfile(speciesName).catch(() => null)
          ]);
          if (selectedSpeciesRef.current.toLowerCase() === speciesName.toLowerCase()) {
            setPlantCard(card);
            setPlantProfile(profile);
            setDraftBuildMessage("Scheda aggiornata con i contenuti completi.");
            setSearchResults((prev) => prev.map((item) => (
              item.species.toLowerCase() === speciesName.toLowerCase()
                ? { ...item, is_draft: false }
                : item
            )));
          }
          stopDraftPolling();
          return;
        }

        if (status === "failed") {
          const reason = statusData?.status?.error || "Aggiornamento non riuscito.";
          setDraftBuildMessage(`Scheda ancora in costruzione. ${reason}`);
          stopDraftPolling();
          return;
        }

        setDraftBuildMessage("Scheda in costruzione: sto aggiornando dati, immagini e knowledge base...");
      } catch {
        // Ignore transient polling errors; next tick will retry.
      }

      if (attempts >= maxAttempts) {
        setDraftBuildMessage("Scheda ancora in preparazione. Riprova tra poco.");
        stopDraftPolling();
      }
    }, 4000);
  }
  const canAsk = Boolean(activeChatPlantName) && question.trim().length > 2;
  const isLoggedIn = Boolean(auth?.idToken);
  const isAdmin = Boolean(auth?.user?.is_admin);

  useEffect(() => {
    if (!isLoggedIn) {
      setIsAuthMenuOpen(false);
    }
  }, [isLoggedIn]);

  useEffect(() => {
    function handleDocumentMouseDown(event) {
      if (!authMenuRef.current || authMenuRef.current.contains(event.target)) {
        return;
      }
      setIsAuthMenuOpen(false);
    }

    function handleDocumentKeyDown(event) {
      if (event.key === "Escape") {
        setIsAuthMenuOpen(false);
      }
    }

    document.addEventListener("mousedown", handleDocumentMouseDown);
    document.addEventListener("keydown", handleDocumentKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleDocumentMouseDown);
      document.removeEventListener("keydown", handleDocumentKeyDown);
    };
  }, []);

  const isSelectedDraft = plantCard?.source === "db_draft" || plantProfile?.indexed === false;
  const galleryImages = useMemo(() => {
    if (!plantCard?.images?.length) {
      return [];
    }
    return plantCard.images.map((imgUrl) => toOptimizedImage(imgUrl, 720));
  }, [plantCard]);

  const activeImage = galleryImages.length ? galleryImages[imageIndex % galleryImages.length] : "";
  const secondaryImage = galleryImages.length > 1
    ? galleryImages[(imageIndex + 1) % galleryImages.length]
    : "";
  const myPlantUserPhotos = useMemo(() => {
    if (!selectedMyPlant) {
      return [];
    }

    const raw = Array.isArray(selectedMyPlant.user_photos) && selectedMyPlant.user_photos.length
      ? selectedMyPlant.user_photos
      : (selectedMyPlant.user_photo_url ? [selectedMyPlant.user_photo_url] : []);

    return raw
      .map((url) => toAbsoluteImage(String(url || "").trim()))
      .filter(Boolean);
  }, [selectedMyPlant]);

  const wateringMonthCalendar = useMemo(() => {
    if (!wateringSchedule.length) {
      return null;
    }

    const now = new Date();
    const year = now.getFullYear();
    const month = now.getMonth();
    const firstDay = new Date(year, month, 1);
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    // Convert JS Sunday-first index to Monday-first grid.
    const leadingEmptyCells = (firstDay.getDay() + 6) % 7;

    const highlightedDays = new Set(
      wateringSchedule
        .filter((dateValue) => dateValue.getFullYear() === year && dateValue.getMonth() === month)
        .map((dateValue) => dateValue.getDate())
    );

    const cells = [];
    for (let i = 0; i < leadingEmptyCells; i += 1) {
      cells.push({ key: `empty-${i}`, day: null, isToday: false, isHighlighted: false });
    }

    for (let day = 1; day <= daysInMonth; day += 1) {
      const isToday = day === now.getDate();
      cells.push({
        key: `day-${day}`,
        day,
        isToday,
        isHighlighted: highlightedDays.has(day)
      });
    }

    return {
      monthLabel: firstDay.toLocaleDateString("it-IT", { month: "long", year: "numeric" }),
      weekdays: ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
      cells,
      highlightedCount: highlightedDays.size
    };
  }, [wateringSchedule]);

  const myPlantProfileEntries = useMemo(() => {
    if (!myPlantProfile) {
      return [];
    }

    const formatProfileValue = (key, value) => {
      if (key === "annaffiatura_gg") {
        const numeric = Number(value);
        if (!Number.isNaN(numeric)) {
          return `${numeric} ${numeric === 1 ? "giorno" : "giorni"}`;
        }
      }
      return value;
    };

    return Object.entries(PROFILE_LABELS)
      .map(([key, label]) => ({
        key,
        label,
        icon: PROFILE_ICONS[key] || "\u{2139}\u{FE0F}",
        desc: PROFILE_DESCRIPTIONS[key] || "",
        value: formatProfileValue(key, myPlantProfile[key])
      }))
      .filter((entry) => entry.value !== null && entry.value !== "");
  }, [myPlantProfile]);

  async function refreshGoogleSessionSilently() {
    if (refreshPromiseRef.current) {
      return refreshPromiseRef.current;
    }

    const currentToken = auth?.idToken || "";
    const payload = parseJwtPayload(currentToken);
    const clientId = String(payload?.aud || "").trim();

    if (!clientId || !window.google?.accounts?.id) {
      return false;
    }

    const refreshPromise = new Promise((resolve) => {
      let done = false;
      const finish = (ok) => {
        if (done) {
          return;
        }
        done = true;
        resolve(ok);
      };

      const timeoutId = window.setTimeout(() => finish(false), 8000);

      try {
        window.google.accounts.id.initialize({
          client_id: clientId,
          auto_select: true,
          cancel_on_tap_outside: false,
          callback: async (credentialResponse) => {
            const idToken = credentialResponse?.credential || "";
            if (!idToken) {
              window.clearTimeout(timeoutId);
              finish(false);
              return;
            }

            try {
              const data = await verifyGoogleToken(idToken);
              const nextAuth = {
                idToken,
                user: {
                  ...(data.user || auth?.user || {}),
                  is_admin: Boolean(data?.is_admin),
                },
                expiresAt: data.expires_at || null
              };
              setAuth(nextAuth);
              setAuthToken(idToken);
              window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(nextAuth));
              window.clearTimeout(timeoutId);
              finish(true);
            } catch {
              window.clearTimeout(timeoutId);
              finish(false);
            }
          }
        });

        window.google.accounts.id.prompt((notification) => {
          if (notification.isNotDisplayed() || notification.isSkippedMoment()) {
            window.clearTimeout(timeoutId);
            finish(false);
          }
        });
      } catch {
        window.clearTimeout(timeoutId);
        finish(false);
      }
    });

    refreshPromiseRef.current = refreshPromise;
    try {
      return await refreshPromise;
    } finally {
      refreshPromiseRef.current = null;
    }
  }

  useEffect(() => {
    const raw = window.localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) {
      setAuthToken("");
      return;
    }

    try {
      const saved = JSON.parse(raw);
      if (saved?.idToken) {
        setAuth(saved);
        setAuthToken(saved.idToken);
      } else {
        setAuthToken("");
      }
    } catch {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      setAuthToken("");
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(async () => {
      const refreshed = await refreshGoogleSessionSilently();
      if (!refreshed) {
        setError("Sessione Google scaduta. Tocca Accedi per rinnovarla.");
      }
      return refreshed;
    });

    return () => setUnauthorizedHandler(null);
  }, [auth?.idToken]);

  useEffect(() => {
    const exp = Number(auth?.expiresAt || 0);
    if (!exp) {
      return undefined;
    }

    const refreshBeforeMs = 2 * 60 * 1000;
    const delayMs = (exp * 1000) - Date.now() - refreshBeforeMs;
    if (delayMs <= 0) {
      return undefined;
    }

    const timerId = window.setTimeout(() => {
      refreshGoogleSessionSilently();
    }, delayMs);

    return () => window.clearTimeout(timerId);
  }, [auth?.expiresAt, auth?.idToken]);

  useEffect(() => {
    setExpandedProfileKey("");
  }, [plantProfile]);

  useEffect(() => {
    if (!isLoggedIn) {
      setMyPlants([]);
      setAdminConsole(null);
      return;
    }

    loadMyPlants();
  }, [isLoggedIn]);

  useEffect(() => {
    if (activeView === "admin" && (!isLoggedIn || !isAdmin)) {
      setActiveView("recognize");
    }
  }, [activeView, isAdmin, isLoggedIn]);

  useEffect(() => {
    if (!busy.search) {
      setSearchStepIndex(0);
      return;
    }

    const timer = setInterval(() => {
      setSearchStepIndex((prev) => (prev + 1) % searchSteps.length);
    }, 1200);

    return () => clearInterval(timer);
  }, [busy.search, searchSteps.length]);

  const profileEntries = useMemo(() => {
    if (!plantProfile) {
      return [];
    }

    const formatProfileValue = (key, value) => {
      if (key === "annaffiatura_gg") {
        const numeric = Number(value);
        if (!Number.isNaN(numeric)) {
          return `${numeric} ${numeric === 1 ? "giorno" : "giorni"}`;
        }
      }
      return value;
    };

    return Object.entries(PROFILE_LABELS)
      .map(([key, label]) => ({
        key,
        label,
        icon: PROFILE_ICONS[key] || "\u{2139}\u{FE0F}",
        desc: PROFILE_DESCRIPTIONS[key] || "",
        value: formatProfileValue(key, plantProfile[key])
      }))
      .filter((entry) => entry.value !== null && entry.value !== "");
  }, [plantProfile]);

  function applySelectedFile(nextFile) {
    setFile(nextFile);
    setSelectedSpecies("");
    setSearchResults([]);
    setSearchFile(null);
    setSpeciesPreviews({});
    setSpeciesCommonNames({});
    setShowSpeciesGrid(true);
    setPlantCard(null);
    setPlantProfile(null);
    setQuestion("");
    setChatAnswer("");
    setUserPlantName("");
    setSaveStatus("");
    setLastSearchUsedOpenAI(false);
    setLastSearchDurationMs(null);
    lastSearchDurationRef.current = null;
    setImageIndex(0);
    setError("");

    if (nextFile) {
      setPreview(URL.createObjectURL(nextFile));
    } else {
      setPreview("");
    }
  }

  function onFileChange(event) {
    const nextFile = event.target.files?.[0] || null;
    applySelectedFile(nextFile);
  }

  function onDropFile(event) {
    event.preventDefault();
    setIsDragActive(false);
    const nextFile = event.dataTransfer?.files?.[0] || null;
    applySelectedFile(nextFile);
  }

  function onDragOver(event) {
    event.preventDefault();
    setIsDragActive(true);
  }

  function onDragLeave(event) {
    event.preventDefault();
    setIsDragActive(false);
  }

  function openFileDialog() {
    fileInputRef.current?.click();
  }

  function openCameraDialog() {
    cameraInputRef.current?.click();
  }

  async function handleSearch(event) {
    event.preventDefault();
    if (!file) {
      setError("Carica prima un'immagine della pianta.");
      return;
    }

    setError("");
    setSearchResults([]);
    setSearchFile(file);
    setSpeciesPreviews({});
    setSpeciesCommonNames({});
    setSelectedSpecies("");
    setShowSpeciesGrid(true);
    setPlantCard(null);
    setPlantProfile(null);
    setChatAnswer("");
    setBusy((prev) => ({ ...prev, search: true }));

    try {
      const data = await searchPlantImage(file, 5);
      setLastSearchUsedOpenAI(Boolean(data?.gpt_fallback_used));
      const nextDurationMs = Number.isFinite(Number(data?.recognition_ms)) ? Number(data.recognition_ms) : null;
      setLastSearchDurationMs(nextDurationMs);
      lastSearchDurationRef.current = nextDurationMs;
      const results = normalizeSearchResults(data.results || []);
      setSearchResults(results);
      const speciesNames = results.map((item) => item.species);
      const [previewData, commonNameData] = await Promise.all([
        getSpeciesPreviews(speciesNames),
        getSpeciesCommonNames(speciesNames),
      ]);
      setSpeciesPreviews(previewData.previews || {});
      setSpeciesCommonNames(commonNameData.common_names || {});

      if (!results.length) {
        setError("Nessun risultato trovato. Prova con una foto piu nitida.");
      } else if (shouldAutoSelectTopResult(results)) {
        await selectSpecies(results[0].species);
      } else {
        setShowSpeciesGrid(true);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, search: false }));
    }
  }

  async function loadMoreSpecies() {
    if (!searchFile || busy.moreSpecies) return;
    setBusy((prev) => ({ ...prev, moreSpecies: true }));
    try {
      const nextK = searchResults.length + 5;
      const data = await searchPlantImage(searchFile, nextK);
      const allResults = normalizeSearchResults(data.results || []);
      const existingNames = new Set(searchResults.map((r) => r.species));
      const newResults = allResults.filter((r) => !existingNames.has(r.species));
      if (!newResults.length) return;
      const newSpeciesNames = newResults.map((r) => r.species);
      const [previewData, commonNameData] = await Promise.all([
        getSpeciesPreviews(newSpeciesNames),
        getSpeciesCommonNames(newSpeciesNames),
      ]);
      setSpeciesPreviews((prev) => ({ ...prev, ...(previewData.previews || {}) }));
      setSpeciesCommonNames((prev) => ({ ...prev, ...(commonNameData.common_names || {}) }));
      setSearchResults((prev) => [...prev, ...newResults]);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, moreSpecies: false }));
    }
  }

  async function selectSpecies(speciesName) {
    stopDraftPolling();
    setSelectedSpecies(speciesName);
    setShowSpeciesGrid(false);
    setPlantCard(null);
    setPlantProfile(null);
    setDraftBuildMessage("");
    setChatAnswer("");
    setUserPlantName("");
    setSaveStatus("");
    setQuestion("");
    setImageIndex(0);
    setError("");
    setBusy((prev) => ({ ...prev, plant: true }));

    try {
      const [card, profile] = await Promise.all([
        getPlantCard(speciesName),
        getPlantProfile(speciesName).catch(() => null)
      ]);
      setPlantCard(card);
      setPlantProfile(profile);

      try {
        await logRecognition({
          chosen_species: speciesName,
          used_openai: lastSearchUsedOpenAI,
          image_url: null,
          recognition_ms: lastSearchDurationRef.current ?? lastSearchDurationMs,
        });
      } catch {
        // Logging failure should not block user flow.
      }

      const isDraft = card?.source === "db_draft" || profile?.indexed === false;
      if (isDraft) {
        setDraftBuildMessage("Scheda in costruzione: avvio aggiornamento automatico...");
        startDraftPolling(speciesName);
      } else {
        setSearchResults((prev) => prev.map((item) => (
          item.species.toLowerCase() === speciesName.toLowerCase()
            ? { ...item, is_draft: false }
            : item
        )));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, plant: false }));
    }
  }

  async function handleQuestion(event) {
    event.preventDefault();
    if (!isLoggedIn) {
      setError("Accedi con Google per fare domande sulla cura.");
      return;
    }
    if (!activeChatPlantName) {
      setError("Seleziona prima una pianta.");
      return;
    }
    if (!canAsk) {
      return;
    }

    setError("");
    setBusy((prev) => ({ ...prev, chat: true }));

    try {
      const data = await askPlantCare(activeChatPlantName, question.trim());
      setChatAnswer(data.answer || "Nessuna risposta disponibile.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, chat: false }));
    }
  }

  async function loadMyPlants() {
    setBusy((prev) => ({ ...prev, myPlants: true }));
    try {
      const data = await getMyPlants();
      const items = data.items || [];
      setMyPlants(items);
      if (!items.length) {
        setIsMyPlantsListCollapsed(false);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, myPlants: false }));
    }
  }

  async function handleSavePlant(event) {
    event.preventDefault();
    if (!isLoggedIn) {
      setError("Accedi con Google per salvare la pianta.");
      return;
    }
    if (!selectedSpecies) {
      setError("Seleziona prima una specie da salvare.");
      return;
    }

    const trimmed = userPlantName.trim();
    if (!trimmed) {
      setError("Inserisci un nome per la tua pianta.");
      return;
    }

    setError("");
    setSaveStatus("");
    setBusy((prev) => ({ ...prev, savePlant: true }));
    try {
      const data = await saveMyPlant(selectedSpecies, trimmed);
      const saved = data.saved || null;
      let photoUploaded = false;
      let uploadedImageUrl = "";
      if (saved?.id && file) {
        try {
          const uploadData = await uploadMyPlantPhoto(saved.id, file);
          uploadedImageUrl = String(uploadData?.updated?.user_photo_url || "").trim();
          photoUploaded = true;
        } catch {
          photoUploaded = false;
        }
      }

      if (selectedSpecies) {
        try {
          await logRecognition({
            chosen_species: selectedSpecies,
            used_openai: lastSearchUsedOpenAI,
            image_url: uploadedImageUrl || null,
            recognition_ms: lastSearchDurationRef.current ?? lastSearchDurationMs,
          });
        } catch {
          // Logging failure should not block save flow.
        }
      }

      if (saved) {
        setSaveStatus(
          photoUploaded
            ? `Salvata: ${saved.user_given_name} (foto associata)`
            : `Salvata: ${saved.user_given_name}`
        );
      } else {
        setSaveStatus("Pianta salvata.");
      }
      setUserPlantName("");
      await loadMyPlants();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, savePlant: false }));
    }
  }

  async function handleDeleteMyPlant(item, event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }

    if (!item?.id) {
      return;
    }

    const confirmed = window.confirm(`Eliminare la pianta salvata "${item.user_given_name}"?`);
    if (!confirmed) {
      return;
    }

    setError("");
    setDeletingPlantId(item.id);
    try {
      await deleteMyPlant(item.id);
      if (selectedMyPlant?.id === item.id) {
        setSelectedMyPlant(null);
        setIsMyPlantsListCollapsed(false);
        setMyPlantCard(null);
        setMyPlantProfile(null);
        setWateringSchedule([]);
        setWateringPlan(null);
        setIsEditingFirstWaterDate(false);
        setFirstWaterDateInput("");
      }
      await loadMyPlants();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeletingPlantId(null);
    }
  }

  function buildWateringSchedule(startIso, intervalDays, totalEvents = 16) {
    const start = new Date(startIso || "");
    const days = parseWateringIntervalDays(intervalDays);

    if (Number.isNaN(start.getTime()) || days === null || days <= 0) {
      return [];
    }

    const schedule = [];
    for (let i = 0; i < totalEvents; i += 1) {
      const date = new Date(start);
      date.setDate(date.getDate() + (i * days));
      schedule.push(date);
    }
    return schedule;
  }

  async function openMyPlantDetails(item) {
    if (!item?.plant_name) {
      return;
    }

    setError("");
    setQuestion("");
    setChatAnswer("");
    setSelectedSpecies(item.plant_name);
    setSelectedMyPlant(item);
    setIsMyPlantsListCollapsed(true);
    setMyPlantCard(null);
    setMyPlantProfile(null);
    setWateringSchedule([]);
    setWateringPlan(null);
    setIsEditingFirstWaterDate(false);
    setFirstWaterDateInput("");
    setBusy((prev) => ({ ...prev, myPlantDetail: true }));

    try {
      const [card, profile] = await Promise.all([
        getPlantCard(item.plant_name),
        getPlantProfile(item.plant_name).catch(() => null)
      ]);
      setMyPlantCard(card);
      setMyPlantProfile(profile);

      const intervalDays = profile?.annaffiatura_gg;
      const schedule = buildWateringSchedule(item.created_at_iso, intervalDays);
      setWateringSchedule(schedule);
      const parsedInterval = parseWateringIntervalDays(intervalDays);
      if (parsedInterval && item?.created_at_iso) {
        setFirstWaterDateInput(formatISOToInputDate(item.created_at_iso));
        setWateringPlan({
          startIso: item.created_at_iso,
          intervalDays: parsedInterval,
          occurrences: 16,
          title: item.user_given_name || item.plant_name || "Innaffiatura",
          annaffiaturaTime: profile?.annaffiatura_time || null
        });
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, myPlantDetail: false }));
    }
  }

  function buildGoogleCalendarRecurringUrl() {
    if (!wateringPlan?.startIso || !wateringPlan?.intervalDays) {
      return "";
    }

    const startDate = new Date(wateringPlan.startIso);
    if (Number.isNaN(startDate.getTime())) {
      return "";
    }

    const eventHour = wateringPlan.annaffiaturaTime === "sera" ? 19 : 8;
    startDate.setUTCHours(eventHour, 0, 0, 0);
    const endDate = new Date(startDate);
    endDate.setUTCHours(eventHour + 1, 0, 0, 0);

    function formatDateTimeGcal(d) {
      return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
    }

    const startToken = formatDateTimeGcal(startDate);
    const endToken = formatDateTimeGcal(endDate);
    const recur = `RRULE:FREQ=DAILY;INTERVAL=${wateringPlan.intervalDays};COUNT=${wateringPlan.occurrences}`;

    const params = new URLSearchParams({
      action: "TEMPLATE",
      text: `Innaffiatura - ${wateringPlan.title}`,
      details: `Promemoria automatico di innaffiatura ogni ${wateringPlan.intervalDays} giorni.`,
      dates: `${startToken}/${endToken}`,
      recur
    });

    return `https://calendar.google.com/calendar/render?${params.toString()}`;
  }

  async function applyFirstWateringDateChange() {
    if (!firstWaterDateInput || !wateringPlan?.intervalDays || !selectedMyPlant?.id) {
      return;
    }

    setError("");
    setBusy((prev) => ({ ...prev, updateFirstWaterDate: true }));

    try {
      const data = await updateMyPlantFirstWaterDate(selectedMyPlant.id, firstWaterDateInput);
      const updated = data.updated || null;
      if (!updated) {
        throw new Error("Aggiornamento data non riuscito.");
      }

      setSelectedMyPlant(updated);
      setMyPlants((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));

      const nextStartIso = updated.created_at_iso;
      const nextSchedule = buildWateringSchedule(nextStartIso, wateringPlan.intervalDays);

      setWateringPlan((prev) => {
        if (!prev) {
          return prev;
        }
        return {
          ...prev,
          startIso: nextStartIso,
        };
      });
      setWateringSchedule(nextSchedule);
      setFirstWaterDateInput(formatISOToInputDate(nextStartIso));
      setIsEditingFirstWaterDate(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, updateFirstWaterDate: false }));
    }
  }

  function prevImage() {
    if (!galleryImages.length) {
      return;
    }
    setImageIndex((prev) => (prev - 1 + galleryImages.length) % galleryImages.length);
  }

  function nextImage() {
    if (!galleryImages.length) {
      return;
    }
    setImageIndex((prev) => (prev + 1) % galleryImages.length);
  }

  async function handleGoogleSuccess(credentialResponse) {
    const idToken = credentialResponse?.credential || "";
    if (!idToken) {
      setError("Login Google non riuscito: token mancante.");
      return;
    }

    setError("");
    setAuthBusy(true);

    try {
      const data = await verifyGoogleToken(idToken);
      const nextAuth = {
        idToken,
        user: {
          ...(data.user || {}),
          is_admin: Boolean(data?.is_admin),
        },
        expiresAt: data.expires_at || null
      };
      setAuth(nextAuth);
      setAuthToken(idToken);
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(nextAuth));
      setActiveView("recognize");
    } catch (err) {
      setAuth(null);
      setAuthToken("");
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      setAdminConsole(null);
      setError(err.message || "Login Google non riuscito.");
    } finally {
      setAuthBusy(false);
    }
  }

  function handleLogout() {
    setIsAuthMenuOpen(false);
    setAuth(null);
    setAuthToken("");
    window.localStorage.removeItem(AUTH_STORAGE_KEY);
    setMyPlants([]);
    setAdminConsole(null);
    setSaveStatus("");
    setActiveView("recognize");
    setSelectedMyPlant(null);
    setMyPlantCard(null);
    setMyPlantProfile(null);
    setWateringSchedule([]);
    setWateringPlan(null);
    setIsEditingFirstWaterDate(false);
    setFirstWaterDateInput("");
    setError("");
  }

  async function loadAdminConsole(chartDays = adminChartDays) {
    if (!isLoggedIn) {
      setError("Accedi con Google per usare AD Console.");
      return;
    }
    if (!isAdmin) {
      setError("Accesso admin non autorizzato.");
      return;
    }

    setError("");
    setBusy((prev) => ({ ...prev, adminConsole: true }));
    try {
      const data = await getAdminConsoleData(500, chartDays);
      setAdminConsole(data || null);
    } catch (err) {
      setError(err.message || "Errore caricamento AD Console.");
    } finally {
      setBusy((prev) => ({ ...prev, adminConsole: false }));
    }
  }

  function openAdminConsole() {
    setIsAuthMenuOpen(false);
    setActiveView("admin");
    loadAdminConsole();
  }

  function applyAdminChartDays(nextDays) {
    const safeDays = [7, 30, 90].includes(Number(nextDays)) ? Number(nextDays) : 30;
    setAdminChartDays(safeDays);
    loadAdminConsole(safeDays);
  }

  function openRegisterPage() {
    setActiveView("register");
    setError("");
  }

  function openPlantPhotoDialog(plantId) {
    plantPhotoTargetIdRef.current = plantId;
    plantPhotoInputRef.current?.click();
  }

  async function handlePlantPhotoFileChange(event) {
    const photoFile = event.target.files?.[0] || null;
    event.target.value = "";
    if (!photoFile || !plantPhotoTargetIdRef.current) {
      return;
    }
    const targetId = plantPhotoTargetIdRef.current;
    plantPhotoTargetIdRef.current = null;
    setUploadingPhotoId(targetId);
    setError("");
    try {
      const data = await uploadMyPlantPhoto(targetId, photoFile);
      const updated = data.updated;
      if (updated) {
        setMyPlants((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
        if (selectedMyPlant?.id === updated.id) {
          setSelectedMyPlant(updated);
        }
      }
    } catch (err) {
      setError(err.message || "Errore durante l'upload della foto.");
    } finally {
      setUploadingPhotoId(null);
    }
  }

  const googleCalendarUrl = buildGoogleCalendarRecurringUrl();

  return (
    <main className="page">
      {/* Hidden input for user plant photo upload */}
      <input
        ref={plantPhotoInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: "none" }}
        onChange={handlePlantPhotoFileChange}
      />
      <section className="hero">
        <div className="hero-inner">
          <div className="hero-topbar">
            <div className="hero-brand-block">
              <div className="hero-brand">
                <img src="/icons/icon-512.svg" alt="Icona Clorofilla" className="hero-logo" />
                <p className="tag">Clorofilla</p>
              </div>
              <h1>Ti aiuta a <span className="hero-highlight">riconoscere</span> e <span className="hero-highlight">curare</span> le tue piante.</h1>
            </div>

            <div className="auth-box">
              {!googleClientIdConfigured && (
                <p className="auth-warning">
                  Configura GOOGLE_CLIENT_ID nello Space per abilitare il login con Google.
                </p>
              )}

              {googleClientIdConfigured && !isLoggedIn && (
                <div className="auth-login">
                  <GoogleLogin
                    onSuccess={handleGoogleSuccess}
                    onError={() => setError("Login Google annullato o non riuscito.")}
                    shape="pill"
                    text="signin_with"
                  />
                </div>
              )}

              {isLoggedIn && (
                <div className="auth-user">
                  <div className="auth-user-panel" ref={authMenuRef}>
                    <div className="auth-user-header">
                      <strong>{auth?.user?.name || "Utente Google"}</strong>
                      <button
                        type="button"
                        className="auth-menu-toggle"
                        aria-label="Apri menu utente"
                        aria-expanded={isAuthMenuOpen}
                        onClick={() => setIsAuthMenuOpen((prev) => !prev)}
                      >
                        <span className="auth-menu-bar" aria-hidden="true" />
                        <span className="auth-menu-bar" aria-hidden="true" />
                        <span className="auth-menu-bar" aria-hidden="true" />
                      </button>
                    </div>
                    {isAuthMenuOpen && (
                      <div className="auth-user-menu" role="menu" aria-label="Menu utente">
                        {isAdmin && (
                          <button type="button" className="auth-user-menu-item" onClick={openAdminConsole}>
                            Console
                          </button>
                        )}
                        <button type="button" className="auth-user-menu-item" onClick={handleLogout}>
                          Esci
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {authBusy && <p className="status">Verifica accesso Google...</p>}
            </div>
          </div>
        </div>
      </section>

      <section className="panel view-menu">
        <button
          type="button"
          className={`menu-btn ${activeView === "recognize" ? "active" : ""}`}
          onClick={() => setActiveView("recognize")}
        >
          Riconosci nuova pianta
        </button>
        {isLoggedIn && (
          <button
            type="button"
            className={`menu-btn ${activeView === "my-plants" ? "active" : ""}`}
            onClick={() => setActiveView("my-plants")}
          >
            Le tue piante
          </button>
        )}
        {!isLoggedIn && (
          <button
            type="button"
            className={`menu-btn ${activeView === "register" ? "active" : ""}`}
            onClick={openRegisterPage}
          >
            Registrati
          </button>
        )}
      </section>

      {activeView === "recognize" ? (
        <section className="panel">
          <form onSubmit={handleSearch} className="upload-form">
            <input
              ref={fileInputRef}
              id="imageInput"
              className="upload-input"
              type="file"
              accept="image/*"
              onChange={onFileChange}
            />
            <input
              ref={cameraInputRef}
              id="cameraInput"
              className="upload-input"
              type="file"
              accept="image/*"
              capture="environment"
              onChange={onFileChange}
            />

            <div
              className={`dropzone ${isDragActive ? "active" : ""} ${busy.search ? "disabled" : ""}`}
              onClick={() => {
                if (!busy.search) {
                  openFileDialog();
                }
              }}
              onDrop={onDropFile}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (busy.search) {
                  return;
                }
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  openFileDialog();
                }
              }}
            >
              <p className="dropzone-title">Carica immagine pianta</p>
              <p className="dropzone-subtitle">
                {file ? `Selezionata: ${file.name}` : "Trascina qui una foto oppure clicca per scegliere"}
              </p>
            </div>

            <button type="submit" disabled={busy.search}>
              {busy.search ? "Riconoscimento in corso..." : "Riconosci pianta"}
            </button>

            <button type="button" className="btn-secondary" onClick={openCameraDialog} disabled={busy.search}>
              Scatta foto
            </button>

            {busy.search && (
              <div className="upload-progress" role="status" aria-live="polite">
                <span>{searchSteps[searchStepIndex]}</span>
              </div>
            )}
          </form>
          {preview && (
            <div className={`preview-shell ${busy.search ? "scanning" : ""}`}>
              <img className="preview" src={preview} alt="Anteprima upload" />
              {busy.search && (
                <div className="scan-overlay" aria-hidden="true">
                  <span className="scan-line" />
                  <span className="scan-glow" />
                </div>
              )}
            </div>
          )}
        </section>
      ) : null}

      {activeView === "recognize" && !!searchResults.length && (
        <section className="panel">
          {!showSpeciesGrid ? (
            <button className="btn-secondary" onClick={() => setShowSpeciesGrid(true)}>
              ▾ Visualizza altre specie
            </button>
          ) : (
            <>
              <div className="species-header">
                <h2>Specie trovate</h2>
                <button className="btn-secondary btn-small" onClick={() => setShowSpeciesGrid(false)}>
                  ▴ Nascondi
                </button>
              </div>
              <div className="result-grid">
                {searchResults.map((item) => (
                  <button
                    key={item.species}
                    className={`result ${selectedSpecies === item.species ? "active" : ""}`}
                    onClick={() => selectSpecies(item.species)}
                  >
                    {!!speciesPreviews[item.species] && (
                      <img
                        className="result-preview"
                        src={toOptimizedImage(speciesPreviews[item.species], 420)}
                        alt={`Esempio ${item.species}`}
                        loading="lazy"
                        decoding="async"
                      />
                    )}
                    <strong>{item.species}</strong>
                    {!!speciesCommonNames[item.species] && (
                      <span className="result-common-name">({speciesCommonNames[item.species]})</span>
                    )}
                    {!!item.is_draft && (
                      <span className="badge-draft">🔨 scheda in costruzione</span>
                    )}
                    <div className="score-bar" aria-label={`Affinita ${(Number(item.displayScore ?? item.score ?? 0) * 100).toFixed(1)} percento`}>
                      <div className="score-fill" style={{ width: `${Math.max(0, Math.min(100, Number(item.displayScore ?? item.score ?? 0) * 100))}%` }} />
                    </div>
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="btn-secondary btn-more-species"
                onClick={loadMoreSpecies}
                disabled={busy.moreSpecies}
              >
                {busy.moreSpecies ? "Cerco..." : "+ Altre specie"}
              </button>
            </>
          )}
        </section>
      )}

      {activeView === "recognize" && busy.plant && <p className="status">Caricamento scheda pianta...</p>}

      {activeView === "recognize" && plantCard && (
        <section className="panel details">
          <div>
            <h2>{plantCard.title}</h2>
            {plantCard.common_name && <p>Nome comune: {plantCard.common_name}</p>}
            {!!isSelectedDraft && (
              <div className="banner-draft">
                🔨 <strong>Scheda in costruzione</strong> — questa specie è stata identificata
                dall'IA ma la sua scheda completa non è ancora disponibile.
              </div>
            )}
            {!!draftBuildMessage && <p className="status">{draftBuildMessage}</p>}

            {!!galleryImages.length && (
              <div className="gallery-wrap">
                <button
                  type="button"
                  className="gallery-nav"
                  onClick={prevImage}
                  aria-label="Foto precedente"
                >
                  &lt;
                </button>
                <div className="gallery-stage">
                  <img
                    className="gallery-photo"
                    src={activeImage}
                    alt={`${plantCard.title} foto ${imageIndex + 1}`}
                    decoding="async"
                    onError={nextImage}
                  />
                  {!!secondaryImage && (
                    <img
                      className="gallery-photo gallery-photo-secondary"
                      src={secondaryImage}
                      alt={`${plantCard.title} foto ${((imageIndex + 1) % galleryImages.length) + 1}`}
                      decoding="async"
                    />
                  )}
                  <p className="gallery-counter">
                    {imageIndex + 1}{secondaryImage ? `-${((imageIndex + 1) % galleryImages.length) + 1}` : ""} / {galleryImages.length}
                  </p>
                </div>
                <button
                  type="button"
                  className="gallery-nav"
                  onClick={nextImage}
                  aria-label="Foto successiva"
                >
                  &gt;
                </button>
              </div>
            )}

            {!!profileEntries.length && (
              <>
                <div className="profile-grid">
                  {profileEntries.map((entry) => (
                    <div
                      key={entry.key}
                      className={`profile-item ${expandedProfileKey === entry.key ? "expanded" : ""}`}
                      role="button"
                      tabIndex={0}
                      aria-expanded={expandedProfileKey === entry.key}
                      onClick={() =>
                        setExpandedProfileKey((prev) => (prev === entry.key ? "" : entry.key))
                      }
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setExpandedProfileKey((prev) => (prev === entry.key ? "" : entry.key));
                        }
                      }}
                    >
                      <span className="profile-icon" aria-hidden="true">{entry.icon}</span>
                      <strong className="profile-value">{String(entry.value)}</strong>
                      <div className="profile-tooltip">
                        <span className="profile-tooltip-label">{entry.label}</span>
                        {entry.desc && <p>{entry.desc}</p>}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>

          <h3>Descrizione</h3>
          <div className="summary markdown-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{plantCard.summary || ""}</ReactMarkdown>
          </div>

          {isLoggedIn && selectedSpecies && (
            <div className="my-plant-save">
              <h3>Salva tra le tue piante</h3>
              <form className="my-plant-form" onSubmit={handleSavePlant}>
                <input
                  type="text"
                  value={userPlantName}
                  onChange={(event) => setUserPlantName(event.target.value)}
                  placeholder="Nome dato dall'utente (es. Basilico balcone)"
                  maxLength={80}
                />
                <button type="submit" disabled={busy.savePlant}>
                  {busy.savePlant ? "Salvataggio..." : "Salva questa pianta"}
                </button>
              </form>
              {saveStatus && <p className="status">{saveStatus}</p>}
            </div>
          )}
        </section>
      )}

      {isLoggedIn && activeView === "my-plants" && (
        <section className="panel">
          <div className="species-header">
            <h2>Le tue piante</h2>
            <div className="my-plants-header-actions">
              {!!selectedMyPlant && isMyPlantsListCollapsed && (
                <button
                  type="button"
                  className="btn-secondary btn-small"
                  onClick={() => setIsMyPlantsListCollapsed(false)}
                >
                  Scegli un'altra pianta
                </button>
              )}
              <button
                type="button"
                className="btn-secondary btn-small"
                onClick={loadMyPlants}
                disabled={busy.myPlants}
              >
                {busy.myPlants ? "Aggiorno..." : "Aggiorna"}
              </button>
            </div>
          </div>

          {!myPlants.length && !busy.myPlants && (
            <p className="status">Non hai ancora salvato piante.</p>
          )}

          {!!myPlants.length && !isMyPlantsListCollapsed && (
            <div className="my-plants-list">
              {myPlants.map((item) => {
                const cardPhoto = Array.isArray(item.user_photos) && item.user_photos.length
                  ? item.user_photos[0]
                  : item.user_photo_url;
                const photoCount = Array.isArray(item.user_photos)
                  ? item.user_photos.length
                  : (item.user_photo_url ? 1 : 0);

                return (
                <article
                  key={item.id}
                  className={`my-plant-item ${selectedMyPlant?.id === item.id ? "active" : ""}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => openMyPlantDetails(item)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      openMyPlantDetails(item);
                    }
                  }}
                >
                  <div className="my-plant-item-head">
                    <strong>{item.user_given_name}</strong>
                    <button
                      type="button"
                      className="btn-secondary btn-small"
                      onClick={(event) => handleDeleteMyPlant(item, event)}
                      disabled={deletingPlantId === item.id}
                    >
                      {deletingPlantId === item.id ? "Elimino..." : "Elimina"}
                    </button>
                  </div>
                  {cardPhoto && (
                    <img
                      src={toAbsoluteImage(cardPhoto)}
                      alt={`Foto di ${item.user_given_name}`}
                      className="my-plant-item-photo"
                    />
                  )}
                  <p>Specie: {item.plant_name}</p>
                  <p>Inserita: {item.created_at}</p>
                  <button
                    type="button"
                    className="btn-secondary btn-small btn-upload-photo"
                    onClick={(event) => { event.stopPropagation(); openPlantPhotoDialog(item.id); }}
                    disabled={uploadingPhotoId === item.id}
                  >
                    {uploadingPhotoId === item.id ? "Carico..." : (photoCount > 0 ? `📷 Aggiungi altra foto (${photoCount})` : "📷 Aggiungi foto")}
                  </button>
                </article>
                );
              })}
            </div>
          )}
        </section>
      )}

      {isLoggedIn && activeView === "my-plants" && busy.myPlantDetail && (
        <p className="status">Caricamento scheda pianta salvata...</p>
      )}

      {isLoggedIn && activeView === "my-plants" && selectedMyPlant && (
        <section className="panel details">
          <div>
            <h2>{myPlantCard?.title || selectedMyPlant?.plant_name || "Scheda pianta"}</h2>
            {selectedMyPlant?.user_given_name && (
              <p>Il tuo nome: {selectedMyPlant.user_given_name}</p>
            )}
            <div className="my-plant-user-gallery">
              <div className="my-plant-user-gallery-head">
                <p className="my-plant-user-gallery-title">Le tue foto</p>
                <button
                  type="button"
                  className="btn-secondary btn-small"
                  onClick={() => selectedMyPlant?.id && openPlantPhotoDialog(selectedMyPlant.id)}
                  disabled={!selectedMyPlant?.id || uploadingPhotoId === selectedMyPlant?.id}
                >
                  {uploadingPhotoId === selectedMyPlant?.id ? "Carico..." : "Aggiungi foto"}
                </button>
              </div>

              {!!myPlantUserPhotos.length ? (
                <div className="my-plant-user-gallery-grid">
                  {myPlantUserPhotos.map((photoUrl, idx) => (
                    <img
                      key={`${photoUrl}-${idx}`}
                      src={photoUrl}
                      alt={`Foto ${idx + 1} di ${selectedMyPlant?.user_given_name || "questa pianta"}`}
                      className="my-plant-detail-photo"
                      loading="lazy"
                    />
                  ))}
                </div>
              ) : (
                <p className="status">Nessuna foto caricata per questa pianta.</p>
              )}
            </div>
            {myPlantCard?.common_name && <p>Nome comune: {myPlantCard.common_name}</p>}

            {!myPlantCard && (
              <p className="status">
                Scheda botanica non disponibile al momento. Le tue foto restano disponibili qui.
              </p>
            )}

            {!!myPlantProfileEntries.length && (
              <div className="profile-grid">
                {myPlantProfileEntries.map((entry) => (
                  <div key={entry.key} className="profile-item">
                    <span className="profile-icon" aria-hidden="true">{entry.icon}</span>
                    <strong className="profile-value">{String(entry.value)}</strong>
                  </div>
                ))}
              </div>
            )}
          </div>

          {!!myPlantCard?.summary && (
            <>
              <h3>Descrizione</h3>
              <div className="summary markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{myPlantCard.summary || ""}</ReactMarkdown>
              </div>
            </>
          )}

          <div className="watering-calendar">
            <h3>Calendario innaffiature</h3>
            {!wateringSchedule.length ? (
              <p className="status">
                Nessun calendario disponibile: controlla il campo annaffiatura_gg nella tabella plants.
              </p>
            ) : (
              <>
                <p className="watering-month-title">{wateringMonthCalendar?.monthLabel || "Mese corrente"}</p>
                {googleCalendarUrl && (
                  <a
                    className="btn-secondary watering-export"
                    href={googleCalendarUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Salva su Google Calendar
                  </a>
                )}
                <div className="watering-weekdays">
                  {wateringMonthCalendar?.weekdays.map((label) => (
                    <span key={label}>{label}</span>
                  ))}
                </div>
                <div className="watering-cells">
                  {wateringMonthCalendar?.cells.map((cell) => (
                    <div
                      key={cell.key}
                      className={[
                        "watering-cell",
                        cell.day === null ? "is-empty" : "",
                        cell.isToday ? "is-today" : "",
                        cell.isHighlighted ? "is-highlighted" : ""
                      ].join(" ").trim()}
                    >
                      {cell.day ?? ""}
                    </div>
                  ))}
                </div>
                {wateringMonthCalendar && wateringMonthCalendar.highlightedCount === 0 && (
                  <p className="status">Nessuna innaffiatura prevista in questo mese.</p>
                )}

                <div className="watering-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setIsEditingFirstWaterDate((prev) => !prev)}
                  >
                    Cambia data prima innaffiatura
                  </button>

                  {isEditingFirstWaterDate && (
                    <div className="watering-date-editor">
                      <input
                        type="date"
                        value={firstWaterDateInput}
                        onChange={(event) => setFirstWaterDateInput(event.target.value)}
                      />
                      <button
                        type="button"
                        onClick={applyFirstWateringDateChange}
                        disabled={!firstWaterDateInput || busy.updateFirstWaterDate}
                      >
                        {busy.updateFirstWaterDate ? "Salvo..." : "Applica data"}
                      </button>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </section>
      )}

      {!isLoggedIn && activeView === "register" && (
        <section className="panel guest-register-panel">
          <h2>Registrati o accedi con Google</h2>
          <p className="status guest-register-text">
            Accedi con Google per salvare le tue piante e chiedere consigli personalizzati sulla loro cura.
          </p>
          {googleClientIdConfigured ? (
            <div className="auth-login">
              <GoogleLogin
                onSuccess={handleGoogleSuccess}
                onError={() => setError("Login Google annullato o non riuscito.")}
                shape="pill"
                text="signin_with"
              />
            </div>
          ) : (
            <p className="auth-warning">Configura GOOGLE_CLIENT_ID per abilitare la registrazione Google.</p>
          )}
        </section>
      )}

      {isLoggedIn && activeView === "my-plants" && selectedMyPlant?.plant_name && (
        <section className="panel">
          <h2>
            Domanda sulla cura
            {activeChatPlantName ? `: ${activeChatPlantName}` : ""}
          </h2>
          <form onSubmit={handleQuestion} className="chat-form">
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={4}
              placeholder="Esempio: devo rinvasarla ora o aspettare?"
            />
            <button type="submit" disabled={!canAsk || busy.chat || !isLoggedIn}>
              {busy.chat ? "Sto preparando la risposta..." : "Chiedi consigli"}
            </button>
          </form>
          {chatAnswer && (
            <article className="answer">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{chatAnswer}</ReactMarkdown>
            </article>
          )}
        </section>
      )}

      {isLoggedIn && isAdmin && activeView === "admin" && (
        <section className="panel">
          <div className="species-header">
            <h2>AD Console</h2>
            <button
              type="button"
              className="btn-secondary btn-small"
              onClick={() => loadAdminConsole(adminChartDays)}
              disabled={busy.adminConsole}
            >
              {busy.adminConsole ? "Aggiorno..." : "Aggiorna"}
            </button>
          </div>

          <p className="status">Admin: {auth?.user?.email || ""}</p>

          <div className="admin-stats-grid">
            <div className="admin-stat-card">
              <strong>{adminConsole?.stats?.registered_users_total ?? 0}</strong>
              <span>Utenti registrati</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.stats?.saved_plants_total ?? 0}</strong>
              <span>Piante salvate</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.stats?.external_user_images_total ?? 0}</strong>
              <span>Immagini utente su store esterno</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.recognition?.total ?? 0}</strong>
              <span>Riconoscimenti totali</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.recognition?.guest_total ?? 0}</strong>
              <span>Riconoscimenti guest</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.recognition?.openai_total ?? 0}</strong>
              <span>Con supporto OpenAI</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.recognition?.with_image_total ?? 0}</strong>
              <span>Con URL immagine salvata</span>
            </div>
            <div className="admin-stat-card">
              <strong>{formatDurationMs(adminConsole?.recognition?.avg_recognition_ms)}</strong>
              <span>Tempo medio riconoscimento</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.inventory?.catalog?.species_db_total ?? 0}</strong>
              <span>Specie totali su DB</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.inventory?.catalog?.species_rag_total ?? 0}</strong>
              <span>Specie totali su RAG</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.inventory?.faiss?.plantclef?.species_total ?? 0}</strong>
              <span>PlantCLEF specie indicizzate</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.inventory?.faiss?.plantclef?.images_total ?? 0}</strong>
              <span>PlantCLEF immagini indicizzate</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.inventory?.faiss?.leafsnap?.species_total ?? 0}</strong>
              <span>LeafSnap specie indicizzate</span>
            </div>
            <div className="admin-stat-card">
              <strong>{adminConsole?.inventory?.faiss?.leafsnap?.images_total ?? 0}</strong>
              <span>LeafSnap immagini indicizzate</span>
            </div>
          </div>

          <div className="admin-filter-row" role="group" aria-label="Filtro giorni grafici">
            <span className="admin-filter-label">Grafici:</span>
            {[7, 30, 90].map((days) => (
              <button
                key={days}
                type="button"
                className={`btn-secondary btn-small admin-filter-btn ${adminChartDays === days ? "is-active" : ""}`}
                onClick={() => applyAdminChartDays(days)}
                disabled={busy.adminConsole}
              >
                {days} giorni
              </button>
            ))}
          </div>

          {!!(adminConsole?.charts?.top_species || []).length && (
            <div className="admin-chart-block">
              <h3>Specie piu riconosciute (ultimi {adminConsole?.recognition?.chart_days || adminChartDays} giorni)</h3>
              <div className="admin-bar-list">
                {(adminConsole?.charts?.top_species || []).map((row) => {
                  const maxVal = Math.max(...(adminConsole?.charts?.top_species || []).map((r) => Number(r.count || 0)), 1);
                  const width = Math.max(6, Math.round((Number(row.count || 0) / maxVal) * 100));
                  return (
                    <div key={row.species} className="admin-bar-row">
                      <span className="admin-bar-label">{row.species}</span>
                      <div className="admin-bar-track">
                        <span className="admin-bar-fill" style={{ width: `${width}%` }} />
                      </div>
                      <strong className="admin-bar-value">{row.count}</strong>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {!!(adminConsole?.charts?.daily_series || []).length && (
            <div className="admin-chart-block">
              <h3>Trend giornaliero (ultimi {adminConsole?.recognition?.chart_days || adminChartDays} giorni)</h3>
              <div className="admin-columns-chart" role="img" aria-label="Riconoscimenti giornalieri">
                {(adminConsole?.charts?.daily_series || []).map((row) => {
                  const all = adminConsole?.charts?.daily_series || [];
                  const maxVal = Math.max(...all.map((r) => Number(r.total || 0)), 1);
                  const total = Number(row.total || 0);
                  const openai = Number(row.openai || 0);
                  const heightPct = Math.max(4, Math.round((total / maxVal) * 100));
                  const dayLabel = String(row.day || "").slice(5);

                  return (
                    <div key={row.day} className="admin-column-item" title={`${row.day} - Tot: ${total}, OpenAI: ${openai}`}>
                      <div className="admin-column-track">
                        <span className="admin-column-fill" style={{ height: `${heightPct}%` }} />
                      </div>
                      <strong className="admin-column-value">{total}</strong>
                      <span className="admin-column-label">{dayLabel}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {busy.adminConsole && <p className="status">Caricamento dati admin...</p>}

          {!busy.adminConsole && !(adminConsole?.users || []).length && (
            <p className="status">Nessun utente registrato disponibile.</p>
          )}

          {!busy.adminConsole && !!(adminConsole?.users || []).length && (
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Email</th>
                    <th>Data registrazione</th>
                  </tr>
                </thead>
                <tbody>
                  {(adminConsole?.users || []).map((item) => (
                    <tr key={`${item.email}-${item.registered_at}`}>
                      <td>{item.email}</td>
                      <td>{item.registered_at_display || item.registered_at || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {!isLoggedIn && activeView !== "register" && (
        <section className="panel guest-cta-panel">
          <p className="status guest-cta-text">
            Per salvare le tue piante o chiedere riguardo la loro cura{" "}
            <button type="button" className="guest-cta-link hero-highlight" onClick={openRegisterPage}>
              REGISTRATI QUI
            </button>
          </p>
        </section>
      )}

      {error && <p className="error">{error}</p>}
    </main>
  );
}
