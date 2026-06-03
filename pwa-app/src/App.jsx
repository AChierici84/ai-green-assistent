import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation, Trans } from "react-i18next";
import { GoogleLogin } from "@react-oauth/google";
import RecognitionFlowSection from "./components/RecognitionFlowSection";
import MyPlantsSection from "./components/MyPlantsSection";
import AdminConsoleSection from "./components/AdminConsoleSection";
import useRecognitionFlow from "./hooks/useRecognitionFlow";
import useMyPlantsFlow from "./hooks/useMyPlantsFlow";
import useAdminConsoleFlow from "./hooks/useAdminConsoleFlow";
import {
  setAuthToken,
  setUnauthorizedHandler,
  toAbsoluteImage,
  toOptimizedImage,
  verifyGoogleToken,
} from "./api";

const AUTH_STORAGE_KEY = "clorofilla-auth";

const PROFILE_KEYS = [
  "annaffiatura_gg",
  "annaffiatura_time",
  "luce",
  "temperatura",
  "umidita",
  "altezza_media",
  "pulizia",
  "terriccio",
  "concimazione",
  "prevenzione"
];

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

function requestGoogleDriveAccessToken(idToken, interactive = true) {
  const payload = parseJwtPayload(idToken || "");
  const clientId = String(payload?.aud || "").trim();

  if (!clientId || !window.google?.accounts?.oauth2) {
    return Promise.resolve("");
  }

  return new Promise((resolve) => {
    let done = false;
    const finish = (tokenValue) => {
      if (done) {
        return;
      }
      done = true;
      resolve(String(tokenValue || ""));
    };

    const timeoutId = window.setTimeout(() => finish(""), 10000);

    function requestToken(promptValue) {
      const tokenClient = window.google.accounts.oauth2.initTokenClient({
        client_id: clientId,
        scope: "https://www.googleapis.com/auth/drive.file",
        callback: (response) => {
          const accessToken = String(response?.access_token || "").trim();
          if (accessToken || !interactive || promptValue === "consent") {
            window.clearTimeout(timeoutId);
            finish(accessToken);
            return;
          }

          requestToken("consent");
        },
        error_callback: () => {
          if (interactive && promptValue !== "consent") {
            requestToken("consent");
            return;
          }
          window.clearTimeout(timeoutId);
          finish("");
        }
      });

      tokenClient.requestAccessToken({ prompt: promptValue });
    }

    try {
      requestToken("");
    } catch {
      window.clearTimeout(timeoutId);
      finish("");
    }
  });
}

export default function App({ googleClientIdConfigured = false }) {
  const { t, i18n: i18nInstance } = useTranslation();
  const getBackendLang = () => (i18nInstance.language === "en" ? "en" : "it");

  const [auth, setAuth] = useState(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [isAuthMenuOpen, setIsAuthMenuOpen] = useState(false);
  const [activeView, setActiveView] = useState("recognize");
  const [error, setError] = useState("");

  const [busy, setBusy] = useState({
    search: false,
    plant: false,
    chat: false,
    savePlant: false,
    myPlants: false,
    myPlantDetail: false,
    adminConsole: false,
    adminSpeciesBuild: false,
    updateFirstWaterDate: false,
    moreSpecies: false,
  });

  const authMenuRef = useRef(null);
  const refreshPromiseRef = useRef(null);
  const loadMyPlantsRef = useRef(async () => {});
  const clearChatRef = useRef(() => {});
  const setSelectedSpeciesRef = useRef(() => {});

  const isLoggedIn = Boolean(auth?.idToken);
  const isAdmin = Boolean(auth?.user?.is_admin);

  const recognitionFlow = useRecognitionFlow({
    t,
    busy,
    getBackendLang,
    setBusy,
    setError,
    isLoggedIn,
    authIdToken: auth?.idToken || "",
    requestGoogleDriveAccessToken,
    loadMyPlants: () => loadMyPlantsRef.current(),
    clearChatState: () => clearChatRef.current(),
  });

  const myPlantsFlow = useMyPlantsFlow({
    t,
    i18nLanguage: i18nInstance.language,
    getBackendLang,
    isLoggedIn,
    activeView,
    setError,
    setBusy,
    authIdToken: auth?.idToken || "",
    requestGoogleDriveAccessToken,
    setSelectedSpecies: (speciesName) => setSelectedSpeciesRef.current(speciesName),
  });

  const adminFlow = useAdminConsoleFlow({
    isLoggedIn,
    isAdmin,
    setBusy,
    setError,
  });

  loadMyPlantsRef.current = myPlantsFlow.loadMyPlants;
  clearChatRef.current = myPlantsFlow.clearChatState;
  setSelectedSpeciesRef.current = recognitionFlow.setSelectedSpecies;

  const profileEntries = useMemo(() => {
    if (!recognitionFlow.plantProfile) {
      return [];
    }

    const formatProfileValue = (key, value) => {
      if (key === "annaffiatura_gg") {
        const numeric = Number(value);
        if (!Number.isNaN(numeric)) {
          return `${numeric} ${t(numeric === 1 ? "day" : "days_plural")}`;
        }
      }
      return value;
    };

    return PROFILE_KEYS
      .map((key) => ({
        key,
        label: t(`profile_label_${key}`),
        icon: PROFILE_ICONS[key] || "\u{2139}\u{FE0F}",
        desc: t(`profile_desc_${key}`),
        value: formatProfileValue(key, recognitionFlow.plantProfile[key])
      }))
      .filter((entry) => entry.value !== null && entry.value !== "");
  }, [recognitionFlow.plantProfile, t]);

  const myPlantProfileEntries = useMemo(() => {
    if (!myPlantsFlow.myPlantProfile) {
      return [];
    }

    const formatProfileValue = (key, value) => {
      if (key === "annaffiatura_gg") {
        const numeric = Number(value);
        if (!Number.isNaN(numeric)) {
          return `${numeric} ${t(numeric === 1 ? "day" : "days_plural")}`;
        }
      }
      return value;
    };

    return PROFILE_KEYS
      .map((key) => ({
        key,
        label: t(`profile_label_${key}`),
        icon: PROFILE_ICONS[key] || "\u{2139}\u{FE0F}",
        desc: t(`profile_desc_${key}`),
        value: formatProfileValue(key, myPlantsFlow.myPlantProfile[key])
      }))
      .filter((entry) => entry.value !== null && entry.value !== "");
  }, [myPlantsFlow.myPlantProfile, t]);

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
      adminFlow.resetAdminState();
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
    recognitionFlow.resetRecognitionState();
    myPlantsFlow.resetMyPlantsState();
    adminFlow.resetAdminState();
    setActiveView("recognize");
    setError("");
  }

  function openAdminConsole() {
    setIsAuthMenuOpen(false);
    setActiveView("admin");
    adminFlow.loadAdminConsole();
  }

  function openRegisterPage() {
    setActiveView("register");
    setError("");
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

  useEffect(() => {
    if (!isLoggedIn) {
      myPlantsFlow.resetMyPlantsState();
      adminFlow.resetAdminState();
      return;
    }
    myPlantsFlow.loadMyPlants();
  }, [isLoggedIn]);

  useEffect(() => {
    const now = new Date();
    myPlantsFlow.setCalendarMonth(now.getMonth());
    myPlantsFlow.setCalendarYear(now.getFullYear());
  }, [myPlantsFlow.selectedMyPlant, myPlantsFlow.wateringSchedule.length]);

  useEffect(() => {
    if (activeView === "admin" && (!isLoggedIn || !isAdmin)) {
      setActiveView("recognize");
    }
  }, [activeView, isAdmin, isLoggedIn]);

  return (
    <main className="page">
      <input
        ref={myPlantsFlow.plantPhotoInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: "none" }}
        onChange={myPlantsFlow.handlePlantPhotoFileChange}
      />
      <section className="hero">
        <div className="hero-inner">
          <div className="hero-topbar">
            <div className="hero-brand-block">
              <div className="hero-brand">
                <img src="/icons/icon-512.svg" alt={t("logoAlt")} className="hero-logo" />
                <p className="tag"><Trans i18nKey="welcome" components={{ highlight: <span className="hero-highlight" /> }} /></p>
              </div>
              <h1><Trans i18nKey="description" components={{ highlight: <span className="hero-highlight" />, br: <br /> }} /></h1>
            </div>

            <div className="auth-box">
              <div className="lang-switcher">
                <button
                  type="button"
                  className={`lang-btn ${i18nInstance.language === "it" ? "active" : ""}`}
                  onClick={() => i18nInstance.changeLanguage("it")}
                  aria-label="Italiano"
                >
                  IT
                </button>
                <button
                  type="button"
                  className={`lang-btn ${i18nInstance.language === "en" ? "active" : ""}`}
                  onClick={() => i18nInstance.changeLanguage("en")}
                  aria-label="English"
                >
                  EN
                </button>
              </div>
              {!googleClientIdConfigured && (
                <p className="auth-warning">
                  {t("configureGoogleLogin")}
                </p>
              )}

              {googleClientIdConfigured && !isLoggedIn && (
                <div className="auth-login">
                  <GoogleLogin
                    onSuccess={handleGoogleSuccess}
                    onError={() => setError(t("loginCancelledOrFailed"))}
                    shape="pill"
                    text="signin_with"
                  />
                </div>
              )}

              {isLoggedIn && (
                <div className="auth-user">
                  <div className="auth-user-panel" ref={authMenuRef}>
                    <div className="auth-user-header">
                      <strong>{auth?.user?.name || t("googleuser")}</strong>
                      <button
                        type="button"
                        className="auth-menu-toggle"
                        aria-label={t("openusermenu")}
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
                            {t("console")}
                          </button>
                        )}
                        <button type="button" className="auth-user-menu-item" onClick={handleLogout}>
                          {t("logout")}
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {authBusy && <p className="status">{t("googleverification")}</p>}
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
          {t("recognizeNewPlant")}
        </button>
        {isLoggedIn && (
          <button
            type="button"
            className={`menu-btn ${activeView === "my-plants" ? "active" : ""}`}
            onClick={() => setActiveView("my-plants")}
          >
            {t("myPlants")}
          </button>
        )}
        {!isLoggedIn && (
          <button
            type="button"
            className={`menu-btn ${activeView === "register" ? "active" : ""}`}
            onClick={openRegisterPage}
          >
            {t("register")}
          </button>
        )}
      </section>

      <RecognitionFlowSection
        t={t}
        activeView={activeView}
        file={recognitionFlow.file}
        busy={busy}
        handleSearch={recognitionFlow.handleSearch}
        fileInputRef={recognitionFlow.fileInputRef}
        cameraInputRef={recognitionFlow.cameraInputRef}
        onFileChange={recognitionFlow.onFileChange}
        isDragActive={recognitionFlow.isDragActive}
        openFileDialog={recognitionFlow.openFileDialog}
        onDropFile={recognitionFlow.onDropFile}
        onDragOver={recognitionFlow.onDragOver}
        onDragLeave={recognitionFlow.onDragLeave}
        openCameraDialog={recognitionFlow.openCameraDialog}
        searchSteps={recognitionFlow.searchSteps}
        searchStepIndex={recognitionFlow.searchStepIndex}
        preview={recognitionFlow.preview}
        searchResults={recognitionFlow.searchResults}
        showSpeciesGrid={recognitionFlow.showSpeciesGrid}
        setShowSpeciesGrid={recognitionFlow.setShowSpeciesGrid}
        selectedSpecies={recognitionFlow.selectedSpecies}
        selectSpecies={recognitionFlow.selectSpecies}
        speciesPreviews={recognitionFlow.speciesPreviews}
        speciesCommonNames={recognitionFlow.speciesCommonNames}
        toOptimizedImage={toOptimizedImage}
        loadMoreSpecies={recognitionFlow.loadMoreSpecies}
        plantCard={recognitionFlow.plantCard}
        isSelectedDraft={recognitionFlow.isSelectedDraft}
        draftBuildMessage={recognitionFlow.draftBuildMessage}
        galleryImages={recognitionFlow.galleryImages}
        imageIndex={recognitionFlow.imageIndex}
        activeImage={recognitionFlow.activeImage}
        secondaryImage={recognitionFlow.secondaryImage}
        prevImage={recognitionFlow.prevImage}
        nextImage={recognitionFlow.nextImage}
        profileEntries={profileEntries}
        expandedProfileKey={recognitionFlow.expandedProfileKey}
        setExpandedProfileKey={recognitionFlow.setExpandedProfileKey}
        isLoggedIn={isLoggedIn}
        handleSavePlant={recognitionFlow.handleSavePlant}
        userPlantName={recognitionFlow.userPlantName}
        setUserPlantName={recognitionFlow.setUserPlantName}
        saveStatus={recognitionFlow.saveStatus}
      />

      <MyPlantsSection
        t={t}
        isLoggedIn={isLoggedIn}
        activeView={activeView}
        selectedMyPlant={myPlantsFlow.selectedMyPlant}
        isMyPlantsListCollapsed={myPlantsFlow.isMyPlantsListCollapsed}
        setIsMyPlantsListCollapsed={myPlantsFlow.setIsMyPlantsListCollapsed}
        loadMyPlants={myPlantsFlow.loadMyPlants}
        busy={busy}
        myPlants={myPlantsFlow.myPlants}
        openMyPlantDetails={myPlantsFlow.openMyPlantDetails}
        deletingPlantId={myPlantsFlow.deletingPlantId}
        handleDeleteMyPlant={myPlantsFlow.handleDeleteMyPlant}
        openPlantPhotoDialog={myPlantsFlow.openPlantPhotoDialog}
        uploadingPhotoId={myPlantsFlow.uploadingPhotoId}
        toAbsoluteImage={toAbsoluteImage}
        myPlantCard={myPlantsFlow.myPlantCard}
        myPlantUserPhotos={myPlantsFlow.myPlantUserPhotos}
        myPlantProfileEntries={myPlantProfileEntries}
        wateringSchedule={myPlantsFlow.wateringSchedule}
        wateringMonthCalendar={myPlantsFlow.wateringMonthCalendar}
        setCalendarMonth={myPlantsFlow.setCalendarMonth}
        setCalendarYear={myPlantsFlow.setCalendarYear}
        googleCalendarUrl={myPlantsFlow.googleCalendarUrl}
        isEditingFirstWaterDate={myPlantsFlow.isEditingFirstWaterDate}
        setIsEditingFirstWaterDate={myPlantsFlow.setIsEditingFirstWaterDate}
        firstWaterDateInput={myPlantsFlow.firstWaterDateInput}
        setFirstWaterDateInput={myPlantsFlow.setFirstWaterDateInput}
        applyFirstWateringDateChange={myPlantsFlow.applyFirstWateringDateChange}
        activeChatPlantName={myPlantsFlow.activeChatPlantName}
        handleQuestion={myPlantsFlow.handleQuestion}
        question={myPlantsFlow.question}
        setQuestion={myPlantsFlow.setQuestion}
        canAsk={myPlantsFlow.canAsk}
        chatAnswer={myPlantsFlow.chatAnswer}
      />

      {!isLoggedIn && activeView === "register" && (
        <section className="panel guest-register-panel">
          <h2>{t("registerOrLoginWithGoogle")}</h2>
          <p className="status guest-register-text">
            {t("loginWithGoogleToSavePlants")}
          </p>
          {googleClientIdConfigured ? (
            <div className="auth-login">
              <GoogleLogin
                onSuccess={handleGoogleSuccess}
                onError={() => setError(t("loginCancelledOrFailed"))}
                shape="pill"
                text="signin_with"
              />
            </div>
          ) : (
            <p className="auth-warning">{t("configureGoogleRegister")}</p>
          )}
        </section>
      )}

      <AdminConsoleSection
        t={t}
        isLoggedIn={isLoggedIn}
        isAdmin={isAdmin}
        activeView={activeView}
        auth={auth}
        busy={busy}
        adminChartDays={adminFlow.adminChartDays}
        loadAdminConsole={adminFlow.loadAdminConsole}
        adminSpeciesName={adminFlow.adminSpeciesName}
        setAdminSpeciesName={adminFlow.setAdminSpeciesName}
        handleAdminSpeciesBuild={adminFlow.handleAdminSpeciesBuild}
        adminSpeciesBuildStatus={adminFlow.adminSpeciesBuildStatus}
        adminHealth={adminFlow.adminHealth}
        adminHealthError={adminFlow.adminHealthError}
        adminConsole={adminFlow.adminConsole}
        formatDurationMs={formatDurationMs}
        applyAdminChartDays={adminFlow.applyAdminChartDays}
      />

      {!isLoggedIn && activeView !== "register" && (
        <section className="panel guest-cta-panel">
          <p className="status guest-cta-text">
            {t("registrationLabelInfo")}{" "}
            <button type="button" className="guest-cta-link hero-highlight" onClick={openRegisterPage}>
              {t("registerHere")}
            </button>
          </p>
        </section>
      )}

      {error && <p className="error">{error}</p>}
    </main>
  );
}
