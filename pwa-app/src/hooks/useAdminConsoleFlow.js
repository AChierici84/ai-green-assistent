import { useState } from "react";
import {
  getAdminConsoleData,
  getApiHealth,
  triggerAdminSpeciesBuild,
} from "../api";

export default function useAdminConsoleFlow({ isLoggedIn, isAdmin, setBusy, setError }) {
  const [adminConsole, setAdminConsole] = useState(null);
  const [adminHealth, setAdminHealth] = useState(null);
  const [adminHealthError, setAdminHealthError] = useState("");
  const [adminChartDays, setAdminChartDays] = useState(30);
  const [adminSpeciesName, setAdminSpeciesName] = useState("");
  const [adminSpeciesBuildStatus, setAdminSpeciesBuildStatus] = useState("");

  function resetAdminState() {
    setAdminConsole(null);
    setAdminHealth(null);
    setAdminHealthError("");
    setAdminSpeciesBuildStatus("");
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
    setAdminHealthError("");
    setBusy((prev) => ({ ...prev, adminConsole: true }));
    try {
      const [consoleResult, healthResult] = await Promise.allSettled([
        getAdminConsoleData(500, chartDays),
        getApiHealth(),
      ]);

      let consoleErrorMessage = "";

      if (consoleResult.status === "fulfilled") {
        setAdminConsole(consoleResult.value || null);
      } else {
        setAdminConsole(null);
        consoleErrorMessage = consoleResult.reason?.message || "Errore caricamento AD Console.";
      }

      if (healthResult.status === "fulfilled") {
        setAdminHealth(healthResult.value || null);
      } else {
        setAdminHealth(null);
        const message = healthResult.reason?.message || "Stato /health non disponibile.";
        setAdminHealthError(message);
      }

      if (consoleErrorMessage) {
        setError(consoleErrorMessage);
      }
    } catch (err) {
      setError(err.message || "Errore caricamento AD Console.");
    } finally {
      setBusy((prev) => ({ ...prev, adminConsole: false }));
    }
  }

  function applyAdminChartDays(nextDays) {
    const safeDays = [7, 30, 90].includes(Number(nextDays)) ? Number(nextDays) : 30;
    setAdminChartDays(safeDays);
    loadAdminConsole(safeDays);
  }

  async function handleAdminSpeciesBuild(forceRebuild) {
    const speciesName = String(adminSpeciesName || "").trim();
    if (!speciesName) {
      setError("Inserisci il nome specie da buildare.");
      return;
    }

    setError("");
    setAdminSpeciesBuildStatus("");
    setBusy((prev) => ({ ...prev, adminSpeciesBuild: true }));

    try {
      const data = await triggerAdminSpeciesBuild(speciesName, forceRebuild);
      const status = data?.status?.status || "queued";
      const action = forceRebuild ? "Rebuild" : "Build";
      setAdminSpeciesBuildStatus(`${action} avviata per ${speciesName}. Stato: ${status}.`);
    } catch (err) {
      setError(err.message || "Errore durante avvio build specie.");
    } finally {
      setBusy((prev) => ({ ...prev, adminSpeciesBuild: false }));
    }
  }

  return {
    adminConsole,
    adminHealth,
    adminHealthError,
    adminChartDays,
    adminSpeciesName,
    adminSpeciesBuildStatus,
    setAdminSpeciesName,
    loadAdminConsole,
    applyAdminChartDays,
    handleAdminSpeciesBuild,
    resetAdminState,
  };
}
