import { useEffect, useMemo, useRef, useState } from "react";
import { GoogleLogin } from "@react-oauth/google";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  askPlantCare,
  deleteMyPlant,
  getMyPlants,
  getPlantCard,
  getPlantProfile,
  saveMyPlant,
  setAuthToken,
  getSpeciesCommonNames,
  getSpeciesPreviews,
  searchPlantImage,
  updateMyPlantFirstWaterDate,
  verifyGoogleToken,
  toAbsoluteImage
} from "./api";

const AUTH_STORAGE_KEY = "green-assistant-auth";

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
  const topScore = Number(top?.score ?? 0);
  const secondScore = Number(second?.score ?? 0);

  return topScore - secondScore >= 0.08;
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

export default function App() {
  const [auth, setAuth] = useState(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [isDragActive, setIsDragActive] = useState(false);
  const [imageIndex, setImageIndex] = useState(0);
  const fileInputRef = useRef(null);
  const cameraInputRef = useRef(null);

  const [searchResults, setSearchResults] = useState([]);
  const [speciesPreviews, setSpeciesPreviews] = useState({});
  const [speciesCommonNames, setSpeciesCommonNames] = useState({});
  const [selectedSpecies, setSelectedSpecies] = useState("");
  const [showSpeciesGrid, setShowSpeciesGrid] = useState(true);
  const [expandedProfileKey, setExpandedProfileKey] = useState("");

  const [plantCard, setPlantCard] = useState(null);
  const [plantProfile, setPlantProfile] = useState(null);

  const [question, setQuestion] = useState("");
  const [chatAnswer, setChatAnswer] = useState("");
  const [userPlantName, setUserPlantName] = useState("");
  const [myPlants, setMyPlants] = useState([]);
  const [saveStatus, setSaveStatus] = useState("");
  const [activeView, setActiveView] = useState("recognize");
  const [selectedMyPlant, setSelectedMyPlant] = useState(null);
  const [myPlantCard, setMyPlantCard] = useState(null);
  const [myPlantProfile, setMyPlantProfile] = useState(null);
  const [wateringSchedule, setWateringSchedule] = useState([]);
  const [wateringPlan, setWateringPlan] = useState(null);
  const [isEditingFirstWaterDate, setIsEditingFirstWaterDate] = useState(false);
  const [firstWaterDateInput, setFirstWaterDateInput] = useState("");
  const [deletingPlantId, setDeletingPlantId] = useState(null);

  const [busy, setBusy] = useState({
    search: false,
    plant: false,
    chat: false,
    savePlant: false,
    myPlants: false,
    myPlantDetail: false,
    updateFirstWaterDate: false
  });
  const [error, setError] = useState("");
  const [searchStepIndex, setSearchStepIndex] = useState(0);

  const searchSteps = ["Leggo i dettagli della foglia", "Confronto con le specie note", "Preparo i risultati migliori"];

  const canAsk = selectedSpecies && question.trim().length > 2;
  const isLoggedIn = Boolean(auth?.idToken);
  const googleClientIdConfigured = Boolean(import.meta.env.VITE_GOOGLE_CLIENT_ID);
  const galleryImages = useMemo(() => {
    if (!plantCard?.images?.length) {
      return [];
    }
    return plantCard.images.map((imgUrl) => toAbsoluteImage(imgUrl));
  }, [plantCard]);

  const activeImage = galleryImages.length ? galleryImages[imageIndex % galleryImages.length] : "";

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
    setExpandedProfileKey("");
  }, [plantProfile]);

  useEffect(() => {
    if (!isLoggedIn) {
      setMyPlants([]);
      return;
    }

    loadMyPlants();
  }, [isLoggedIn]);

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
    setSpeciesPreviews({});
    setSpeciesCommonNames({});
    setShowSpeciesGrid(true);
    setPlantCard(null);
    setPlantProfile(null);
    setQuestion("");
    setChatAnswer("");
    setUserPlantName("");
    setSaveStatus("");
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
    if (!isLoggedIn) {
      setError("Accedi con Google per usare la ricerca piante.");
      return;
    }
    if (!file) {
      setError("Carica prima un'immagine della pianta.");
      return;
    }

    setError("");
    setSearchResults([]);
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
      const results = [...(data.results || [])].sort((a, b) => b.score - a.score);
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

  async function selectSpecies(speciesName) {
    setSelectedSpecies(speciesName);
    setShowSpeciesGrid(false);
    setPlantCard(null);
    setPlantProfile(null);
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
    if (!canAsk) {
      return;
    }

    setError("");
    setBusy((prev) => ({ ...prev, chat: true }));

    try {
      const data = await askPlantCare(selectedSpecies, question.trim());
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
      setMyPlants(data.items || []);
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
      setSaveStatus(saved ? `Salvata: ${saved.user_given_name}` : "Pianta salvata.");
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
    setSelectedMyPlant(item);
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
          title: item.user_given_name || item.plant_name || "Innaffiatura"
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

    const endDate = new Date(startDate);
    endDate.setDate(endDate.getDate() + 1);

    const startToken = formatDateYYYYMMDD(startDate);
    const endToken = formatDateYYYYMMDD(endDate);
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
        user: data.user || null,
        expiresAt: data.expires_at || null
      };
      setAuth(nextAuth);
      setAuthToken(idToken);
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(nextAuth));
    } catch (err) {
      setAuth(null);
      setAuthToken("");
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      setError(err.message || "Login Google non riuscito.");
    } finally {
      setAuthBusy(false);
    }
  }

  function handleLogout() {
    setAuth(null);
    setAuthToken("");
    window.localStorage.removeItem(AUTH_STORAGE_KEY);
    setMyPlants([]);
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

  const googleCalendarUrl = buildGoogleCalendarRecurringUrl();

  return (
    <main className="page">
      <section className="hero">
        <div className="hero-inner">
          <div className="hero-brand">
            <img src="/icons/icon-512.svg" alt="Icona Green Assistent" className="hero-logo" />
            <p className="tag">Green Assistent</p>
          </div>

          <h1>Ti aiuta a <span className="hero-highlight">riconoscere</span> e <span className="hero-highlight">curare</span> le tue piante.</h1>

          <div className="auth-box">
            {!googleClientIdConfigured && (
              <p className="auth-warning">
                Imposta VITE_GOOGLE_CLIENT_ID per abilitare il login con Google.
              </p>
            )}

            {googleClientIdConfigured && !isLoggedIn && (
              <div className="auth-login">
                <GoogleLogin
                  onSuccess={handleGoogleSuccess}
                  onError={() => setError("Login Google annullato o non riuscito.")}
                  useOneTap
                  shape="pill"
                  text="signin_with"
                />
              </div>
            )}

            {isLoggedIn && (
              <div className="auth-user">
                <div>
                  <strong>{auth?.user?.name || "Utente Google"}</strong>
                  <p>{auth?.user?.email || ""}</p>
                </div>
                <button type="button" className="btn-secondary" onClick={handleLogout}>
                  Esci
                </button>
              </div>
            )}

            {authBusy && <p className="status">Verifica accesso Google...</p>}
          </div>
        </div>
      </section>

      {isLoggedIn && (
        <section className="panel view-menu">
          <button
            type="button"
            className={`menu-btn ${activeView === "recognize" ? "active" : ""}`}
            onClick={() => setActiveView("recognize")}
          >
            Riconosci nuova pianta
          </button>
          <button
            type="button"
            className={`menu-btn ${activeView === "my-plants" ? "active" : ""}`}
            onClick={() => setActiveView("my-plants")}
          >
            Le tue piante
          </button>
        </section>
      )}

      {activeView === "recognize" ? (
        isLoggedIn ? (
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
        ) : (
        <section className="panel">
          <p className="status">Accedi con Google per caricare la foto della pianta.</p>
        </section>
        )
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
                        src={toAbsoluteImage(speciesPreviews[item.species])}
                        alt={`Esempio ${item.species}`}
                      />
                    )}
                    <strong>{item.species}</strong>
                    {!!speciesCommonNames[item.species] && (
                      <span className="result-common-name">({speciesCommonNames[item.species]})</span>
                    )}
                    <div className="score-bar" aria-label={`Affinita ${(item.score * 100).toFixed(1)} percento`}>
                      <div className="score-fill" style={{ width: `${Math.max(0, Math.min(100, item.score * 100))}%` }} />
                    </div>
                  </button>
                ))}
              </div>
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
                  <img src={activeImage} alt={`${plantCard.title} foto ${imageIndex + 1}`} />
                  <p className="gallery-counter">
                    {imageIndex + 1} / {galleryImages.length}
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
            <button
              type="button"
              className="btn-secondary btn-small"
              onClick={loadMyPlants}
              disabled={busy.myPlants}
            >
              {busy.myPlants ? "Aggiorno..." : "Aggiorna"}
            </button>
          </div>

          {!myPlants.length && !busy.myPlants && (
            <p className="status">Non hai ancora salvato piante.</p>
          )}

          {!!myPlants.length && (
            <div className="my-plants-list">
              {myPlants.map((item) => (
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
                  <p>Specie: {item.plant_name}</p>
                  <p>Inserita: {item.created_at}</p>
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      {isLoggedIn && activeView === "my-plants" && busy.myPlantDetail && (
        <p className="status">Caricamento scheda pianta salvata...</p>
      )}

      {isLoggedIn && activeView === "my-plants" && myPlantCard && (
        <section className="panel details">
          <div>
            <h2>{myPlantCard.title}</h2>
            {selectedMyPlant?.user_given_name && (
              <p>Il tuo nome: {selectedMyPlant.user_given_name}</p>
            )}
            {myPlantCard.common_name && <p>Nome comune: {myPlantCard.common_name}</p>}

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

          <h3>Descrizione</h3>
          <div className="summary markdown-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{myPlantCard.summary || ""}</ReactMarkdown>
          </div>

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

      {activeView === "recognize" && selectedSpecies && (
        <section className="panel">
          <h2>Domanda sulla cura</h2>
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

      {error && <p className="error">{error}</p>}
    </main>
  );
}
