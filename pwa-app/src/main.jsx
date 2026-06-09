import React from "react";
import ReactDOM from "react-dom/client";
import * as Sentry from "@sentry/react";
import { GoogleOAuthProvider } from "@react-oauth/google";
import { registerSW } from "virtual:pwa-register";
import App from "./App";
import "./i18n";
import "./styles.css";

const sentryDsn = String(import.meta.env.VITE_SENTRY_DSN || "").trim();
const sentryEnabled = Boolean(sentryDsn);

if (sentryEnabled) {
  const tracesSampleRate = Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE || 0.05);
  Sentry.init({
    dsn: sentryDsn,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || import.meta.env.MODE || "production",
    release: import.meta.env.VITE_SENTRY_RELEASE || undefined,
    tracesSampleRate: Number.isFinite(tracesSampleRate) ? tracesSampleRate : 0.05,
    sendDefaultPii: false,
  });
}

function getApiBase() {
  if (import.meta.env.VITE_API_BASE) {
    return import.meta.env.VITE_API_BASE;
  }
  if (window.location.port === "5173") {
    return "http://localhost:8000";
  }
  return "";
}

async function loadAppConfig() {
  const buildTimeGoogleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";
  if (buildTimeGoogleClientId) {
    return { googleClientId: buildTimeGoogleClientId };
  }

  try {
    const apiBase = getApiBase();
    const response = await fetch(`${apiBase}/app-config`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    return { googleClientId: data.google_client_id || "" };
  } catch (error) {
    console.warn("Impossibile caricare la configurazione runtime dell'app", error);
    return { googleClientId: "" };
  }
}

registerSW({
  immediate: true,
  onRegistered(r) {
    if (r) {
      console.log("Service worker registrato");
    }
  }
});

const root = ReactDOM.createRoot(document.getElementById("root"));

loadAppConfig().then(({ googleClientId }) => {
  const appNode = googleClientId ? (
    <GoogleOAuthProvider clientId={googleClientId}>
      <App googleClientIdConfigured googleClientId={googleClientId} />
    </GoogleOAuthProvider>
  ) : (
    <App googleClientIdConfigured={false} googleClientId="" />
  );

  root.render(
    <React.StrictMode>
      {sentryEnabled ? (
        <Sentry.ErrorBoundary fallback={<p>Si e verificato un errore inatteso.</p>}>
          {appNode}
        </Sentry.ErrorBoundary>
      ) : (
        appNode
      )}
    </React.StrictMode>
  );
});
