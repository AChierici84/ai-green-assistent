import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  askPlantCare,
  getPlantCard,
  getPlantProfile,
  searchPlantImage,
  toAbsoluteImage
} from "./api";

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

export default function App() {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [imageIndex, setImageIndex] = useState(0);

  const [searchResults, setSearchResults] = useState([]);
  const [selectedSpecies, setSelectedSpecies] = useState("");

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

  const canAsk = selectedSpecies && question.trim().length > 2;
  const galleryImages = useMemo(() => {
    if (!plantCard?.images?.length) {
      return [];
    }
    return plantCard.images.map((imgUrl) => toAbsoluteImage(imgUrl));
  }, [plantCard]);

  const activeImage = galleryImages.length ? galleryImages[imageIndex % galleryImages.length] : "";

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
        value: formatProfileValue(key, plantProfile[key])
      }))
      .filter((entry) => entry.value !== null && entry.value !== "");
  }, [plantProfile]);

  function onFileChange(event) {
    const nextFile = event.target.files?.[0] || null;
    setFile(nextFile);
    setSelectedSpecies("");
    setSearchResults([]);
    setPlantCard(null);
    setPlantProfile(null);
    setChatAnswer("");
    setImageIndex(0);
    setError("");

    if (nextFile) {
      setPreview(URL.createObjectURL(nextFile));
    } else {
      setPreview("");
    }
  }

  async function handleSearch(event) {
    event.preventDefault();
    if (!file) {
      setError("Carica prima un'immagine della pianta.");
      return;
    }

    setError("");
    setBusy((prev) => ({ ...prev, search: true }));

    try {
      const data = await searchPlantImage(file, 5);
      setSearchResults(data.results || []);
      if (!data.results?.length) {
        setError("Nessun risultato trovato. Prova con una foto piu nitida.");
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, search: false }));
    }
  }

  async function selectSpecies(speciesName) {
    setSelectedSpecies(speciesName);
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

  return (
    <main className="page">
      <section className="hero">
        <p className="tag">Green Assistent</p>
        <h1>Riconosci una pianta e chiedi come curarla</h1>
        <p>
          Carica una foto, scegli la specie suggerita, apri la scheda descrittiva e fai domande
          operative sulla cura quotidiana.
        </p>
      </section>

      <section className="panel">
        <form onSubmit={handleSearch} className="upload-form">
          <label htmlFor="imageInput">Immagine pianta</label>
          <input id="imageInput" type="file" accept="image/*" onChange={onFileChange} />
          <button type="submit" disabled={busy.search}>
            {busy.search ? "Riconoscimento in corso..." : "Riconosci pianta"}
          </button>
        </form>
        {preview && <img className="preview" src={preview} alt="Anteprima upload" />}
      </section>

      {!!searchResults.length && (
        <section className="panel">
          <h2>Specie trovate</h2>
          <div className="result-grid">
            {searchResults.map((item) => (
              <button
                key={item.species}
                className={`result ${selectedSpecies === item.species ? "active" : ""}`}
                onClick={() => selectSpecies(item.species)}
              >
                <strong>{item.species}</strong>
                <div className="score-bar" aria-label={`Affinita ${(item.score * 100).toFixed(1)} percento`}>
                  <div className="score-fill" style={{ width: `${Math.max(0, Math.min(100, item.score * 100))}%` }} />
                </div>
              </button>
            ))}
          </div>
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
                    <div key={entry.key} className="profile-item">
                      <div className="profile-item-head">
                        <span className="profile-icon" aria-hidden="true">
                          {entry.icon}
                        </span>
                        <span>{entry.label}</span>
                      </div>
                      <strong>{String(entry.value)}</strong>
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
            <button type="submit" disabled={!canAsk || busy.chat}>
              {busy.chat ? "Sto preparando la risposta..." : "Chiedi consigli"}
            </button>
          </form>
          {chatAnswer && <article className="answer">{chatAnswer}</article>}
        </section>
      )}

      {error && <p className="error">{error}</p>}
    </main>
  );
}
