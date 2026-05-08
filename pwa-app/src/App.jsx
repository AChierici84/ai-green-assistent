import { useEffect, useMemo, useRef, useState } from "react";
import { GoogleLogin } from "@react-oauth/google";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  askPlantCare,
  getPlantCard,
  getPlantProfile,
  setAuthToken,
  getSpeciesCommonNames,
  getSpeciesPreviews,
  searchPlantImage,
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

export default function App() {
  const [auth, setAuth] = useState(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [isDragActive, setIsDragActive] = useState(false);
  const [imageIndex, setImageIndex] = useState(0);
  const fileInputRef = useRef(null);

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

  const [busy, setBusy] = useState({
    search: false,
    plant: false,
    chat: false
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
    setError("");
  }

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

          <button type="submit" disabled={busy.search || !isLoggedIn}>
            {busy.search ? "Riconoscimento in corso..." : "Riconosci pianta"}
          </button>

          {!isLoggedIn && <p className="status">Accedi con Google per avviare il riconoscimento.</p>}

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

      {!!searchResults.length && (
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

      {busy.plant && <p className="status">Caricamento scheda pianta...</p>}

      {plantCard && (
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
        </section>
      )}

      {selectedSpecies && (
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
