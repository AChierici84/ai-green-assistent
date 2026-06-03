import { useEffect, useMemo, useRef, useState } from "react";
import {
  getPlantCard,
  getPlantProfile,
  getSpeciesBuildStatus,
  getSpeciesCommonNames,
  getSpeciesPreviews,
  logRecognition,
  saveMyPlant,
  searchPlantImage,
  toOptimizedImage,
  uploadMyPlantPhoto,
} from "../api";

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
    const displayScore = maxScore > 0 ? Math.max(0, Math.min(1, score / maxScore)) : 0;

    return {
      ...item,
      displayScore,
    };
  });
}

export default function useRecognitionFlow({
  t,
  busy,
  getBackendLang,
  setBusy,
  setError,
  isLoggedIn,
  authIdToken,
  requestGoogleDriveAccessToken,
  loadMyPlants,
  clearChatState,
}) {
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

  const [userPlantName, setUserPlantName] = useState("");
  const [saveStatus, setSaveStatus] = useState("");
  const [searchStepIndex, setSearchStepIndex] = useState(0);
  const searchSteps = [t("searchStep1"), t("searchStep2"), t("searchStep3")];

  const [lastSearchUsedOpenAI, setLastSearchUsedOpenAI] = useState(false);
  const [lastSearchDurationMs, setLastSearchDurationMs] = useState(null);
  const lastSearchDurationRef = useRef(null);

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
            getPlantCard(speciesName, { refreshCache: true, lang: getBackendLang() }),
            getPlantProfile(speciesName, { lang: getBackendLang() }).catch(() => null)
          ]);

          const stillDraft = card?.source === "db_draft" || profile?.indexed === false || !profile;
          if (stillDraft && attempts < maxAttempts) {
            setDraftBuildMessage("Build completata, sincronizzo la scheda finale...");
            return;
          }

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
    clearChatState();
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

  async function selectSpecies(speciesName) {
    stopDraftPolling();
    setSelectedSpecies(speciesName);
    setShowSpeciesGrid(false);
    setPlantCard(null);
    setPlantProfile(null);
    setDraftBuildMessage("");
    clearChatState();
    setUserPlantName("");
    setSaveStatus("");
    setImageIndex(0);
    setError("");
    setBusy((prev) => ({ ...prev, plant: true }));

    try {
      const [card, profile] = await Promise.all([
        getPlantCard(speciesName, { lang: getBackendLang() }),
        getPlantProfile(speciesName, { lang: getBackendLang() }).catch(() => null)
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
    clearChatState();
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
    if (!searchFile) return;
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
          const driveAccessToken = await requestGoogleDriveAccessToken(authIdToken || "", true);
          const uploadData = await uploadMyPlantPhoto(saved.id, file, driveAccessToken);
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

  function resetRecognitionState() {
    setFile(null);
    setPreview("");
    setSearchResults([]);
    setSearchFile(null);
    setSpeciesPreviews({});
    setSpeciesCommonNames({});
    setSelectedSpecies("");
    setShowSpeciesGrid(true);
    setExpandedProfileKey("");
    setPlantCard(null);
    setPlantProfile(null);
    setDraftBuildMessage("");
    setUserPlantName("");
    setSaveStatus("");
    setImageIndex(0);
  }

  return {
    file,
    preview,
    isDragActive,
    fileInputRef,
    cameraInputRef,
    searchResults,
    speciesPreviews,
    speciesCommonNames,
    selectedSpecies,
    showSpeciesGrid,
    expandedProfileKey,
    plantCard,
    plantProfile,
    draftBuildMessage,
    userPlantName,
    saveStatus,
    imageIndex,
    searchSteps,
    searchStepIndex,
    galleryImages,
    activeImage,
    secondaryImage,
    isSelectedDraft: plantCard?.source === "db_draft" || plantProfile?.indexed === false,
    setShowSpeciesGrid,
    setExpandedProfileKey,
    setUserPlantName,
    setSelectedSpecies,
    onFileChange,
    onDropFile,
    onDragOver,
    onDragLeave,
    openFileDialog,
    openCameraDialog,
    handleSearch,
    loadMoreSpecies,
    selectSpecies,
    handleSavePlant,
    prevImage,
    nextImage,
    resetRecognitionState,
  };
}
