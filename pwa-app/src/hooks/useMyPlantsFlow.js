import { useMemo, useRef, useState } from "react";
import {
  askPlantCare,
  deleteMyPlant,
  getMyPlants,
  getPlantCard,
  getPlantProfile,
  toAbsoluteImage,
  updateMyPlantFirstWaterDate,
  uploadMyPlantPhoto,
} from "../api";

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

  const roundedDays = Math.round(days);
  if (roundedDays <= 0) {
    return null;
  }

  return roundedDays;
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

function buildWateringSchedule(startIso, intervalDays) {
  const start = new Date(startIso || "");
  const days = parseWateringIntervalDays(intervalDays);

  if (Number.isNaN(start.getTime()) || days === null || days <= 0) {
    return [];
  }

  const schedule = [];
  const end = new Date(start.getFullYear() + 1, 11, 31);
  let i = 0;
  while (true) {
    const date = new Date(start);
    date.setDate(date.getDate() + (i * days));
    if (date > end) break;
    schedule.push(date);
    i += 1;
  }
  return schedule;
}

export default function useMyPlantsFlow({
  t,
  i18nLanguage,
  getBackendLang,
  isLoggedIn,
  activeView,
  setError,
  setBusy,
  authIdToken,
  requestGoogleDriveAccessToken,
  setSelectedSpecies,
}) {
  const [question, setQuestion] = useState("");
  const [chatAnswer, setChatAnswer] = useState("");
  const [myPlants, setMyPlants] = useState([]);
  const [isMyPlantsListCollapsed, setIsMyPlantsListCollapsed] = useState(false);
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

  const [calendarMonth, setCalendarMonth] = useState(() => {
    const now = new Date();
    return now.getMonth();
  });
  const [calendarYear, setCalendarYear] = useState(() => {
    const now = new Date();
    return now.getFullYear();
  });

  const activeChatPlantName = activeView === "my-plants"
    ? (selectedMyPlant?.plant_name || "")
    : "";

  const canAsk = Boolean(activeChatPlantName) && question.trim().length > 2;

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
    const year = calendarYear;
    const month = calendarMonth;
    const now = new Date();
    const firstDay = new Date(year, month, 1);
    const daysInMonth = new Date(year, month + 1, 0).getDate();

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
      const isToday = day === now.getDate() && month === now.getMonth() && year === now.getFullYear();
      cells.push({
        key: `day-${day}`,
        day,
        isToday,
        isHighlighted: highlightedDays.has(day)
      });
    }

    const localeCode = i18nLanguage === "en" ? "en-US" : "it-IT";
    return {
      monthLabel: firstDay.toLocaleDateString(localeCode, { month: "long", year: "numeric" }),
      weekdays: [
        t("weekdayShortMon"), t("weekdayShortTue"), t("weekdayShortWed"),
        t("weekdayShortThu"), t("weekdayShortFri"), t("weekdayShortSat"), t("weekdayShortSun")
      ],
      cells,
      highlightedCount: highlightedDays.size
    };
  }, [wateringSchedule, calendarMonth, calendarYear, t, i18nLanguage]);

  function clearChatState() {
    setQuestion("");
    setChatAnswer("");
  }

  function resetMyPlantsState() {
    setMyPlants([]);
    setIsMyPlantsListCollapsed(false);
    setSelectedMyPlant(null);
    setMyPlantCard(null);
    setMyPlantProfile(null);
    setWateringSchedule([]);
    setWateringPlan(null);
    setIsEditingFirstWaterDate(false);
    setFirstWaterDateInput("");
    clearChatState();
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
      const data = await askPlantCare(activeChatPlantName, question.trim(), { lang: getBackendLang() });
      setChatAnswer(data.answer || "Nessuna risposta disponibile.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((prev) => ({ ...prev, chat: false }));
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

  async function openMyPlantDetails(item) {
    if (!item?.plant_name) {
      return;
    }

    setError("");
    clearChatState();
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
        getPlantCard(item.plant_name, { lang: getBackendLang() }),
        getPlantProfile(item.plant_name, { lang: getBackendLang() }).catch(() => null)
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
    const recur = `RRULE:FREQ=DAILY;INTERVAL=${wateringPlan.intervalDays}`;

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
      const driveAccessToken = await requestGoogleDriveAccessToken(authIdToken || "", true);
      const data = await uploadMyPlantPhoto(targetId, photoFile, driveAccessToken);
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

  return {
    question,
    chatAnswer,
    myPlants,
    isMyPlantsListCollapsed,
    selectedMyPlant,
    myPlantCard,
    myPlantProfile,
    wateringSchedule,
    wateringPlan,
    isEditingFirstWaterDate,
    firstWaterDateInput,
    deletingPlantId,
    uploadingPhotoId,
    plantPhotoInputRef,
    calendarMonth,
    calendarYear,
    activeChatPlantName,
    canAsk,
    myPlantUserPhotos,
    wateringMonthCalendar,
    googleCalendarUrl,
    setQuestion,
    setChatAnswer,
    setIsMyPlantsListCollapsed,
    setIsEditingFirstWaterDate,
    setFirstWaterDateInput,
    setCalendarMonth,
    setCalendarYear,
    loadMyPlants,
    handleQuestion,
    handleDeleteMyPlant,
    openMyPlantDetails,
    applyFirstWateringDateChange,
    openPlantPhotoDialog,
    handlePlantPhotoFileChange,
    clearChatState,
    resetMyPlantsState,
  };
}
