import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function MyPlantsSection({
  t,
  isLoggedIn,
  activeView,
  selectedMyPlant,
  isMyPlantsListCollapsed,
  setIsMyPlantsListCollapsed,
  loadMyPlants,
  busy,
  myPlants,
  openMyPlantDetails,
  deletingPlantId,
  handleDeleteMyPlant,
  openPlantPhotoDialog,
  uploadingPhotoId,
  toAbsoluteImage,
  myPlantCard,
  myPlantUserPhotos,
  myPlantProfileEntries,
  wateringSchedule,
  wateringMonthCalendar,
  setCalendarMonth,
  setCalendarYear,
  googleCalendarUrl,
  isEditingFirstWaterDate,
  setIsEditingFirstWaterDate,
  firstWaterDateInput,
  setFirstWaterDateInput,
  applyFirstWateringDateChange,
  activeChatPlantName,
  handleQuestion,
  question,
  setQuestion,
  canAsk,
  chatAnswer,
}) {
  if (!isLoggedIn || activeView !== "my-plants") {
    return null;
  }

  return (
    <>
      <section className="panel">
        <div className="species-header">
          <h2>{t("myPlants")}</h2>
          <div className="my-plants-header-actions">
            {!!selectedMyPlant && isMyPlantsListCollapsed && (
              <button
                type="button"
                className="btn-secondary btn-small"
                onClick={() => setIsMyPlantsListCollapsed(false)}
              >
                {t("chooseAnotherPlant")}
              </button>
            )}
            <button
              type="button"
              className="btn-secondary btn-small"
              onClick={loadMyPlants}
              disabled={busy.myPlants}
            >
              {busy.myPlants ? t("updating") : t("update")}
            </button>
          </div>
        </div>

        {!myPlants.length && !busy.myPlants && (
          <p className="status">{t("noPlantsSaved")}</p>
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
                      {deletingPlantId === item.id ? t("deleting") : t("delete")}
                    </button>
                  </div>
                  {cardPhoto && (
                    <img
                      src={toAbsoluteImage(cardPhoto)}
                      alt={`Foto di ${item.user_given_name}`}
                      className="my-plant-item-photo"
                    />
                  )}
                  <p>{t("speciesLabel")}: {item.plant_name}</p>
                  <p>{t("addedAtLabel")}: {item.created_at}</p>
                  <button
                    type="button"
                    className="btn-secondary btn-small btn-upload-photo"
                    onClick={(event) => { event.stopPropagation(); openPlantPhotoDialog(item.id); }}
                    disabled={uploadingPhotoId === item.id}
                  >
                    {uploadingPhotoId === item.id ? t("uploading") : (photoCount > 0 ? t("addAnotherPhoto", { count: photoCount }) : t("addPhoto"))}
                  </button>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {busy.myPlantDetail && (
        <p className="status">{t("loadingSavedPlantCard")}</p>
      )}

      {selectedMyPlant && (
        <section className="panel details">
          <div>
            <h2>{myPlantCard?.title || selectedMyPlant?.plant_name || t("plantCard")}</h2>
            {selectedMyPlant?.user_given_name && (
              <p>{t("yourNameLabel")}: {selectedMyPlant.user_given_name}</p>
            )}
            <div className="my-plant-user-gallery">
              <div className="my-plant-user-gallery-head">
                <p className="my-plant-user-gallery-title">{t("yourPhotos")}</p>
                <button
                  type="button"
                  className="btn-secondary btn-small"
                  onClick={() => selectedMyPlant?.id && openPlantPhotoDialog(selectedMyPlant.id)}
                  disabled={!selectedMyPlant?.id || uploadingPhotoId === selectedMyPlant?.id}
                >
                  {uploadingPhotoId === selectedMyPlant?.id ? t("uploading") : t("addPhoto")}
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
                <p className="status">{t("noPhotos")}</p>
              )}
            </div>
            {myPlantCard?.common_name && <p>{t("commonNameLabel")}: {myPlantCard.common_name}</p>}

            {!busy.myPlantDetail && !myPlantCard && (
              <p className="status">
                {t("plantCardNotAvailable")}
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
              <h3>{t("descriptionl")}</h3>
              <div className="summary markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{myPlantCard.summary || ""}</ReactMarkdown>
              </div>
            </>
          )}

          <div className="watering-calendar">
            <h3>{t("wateringCalendar")}</h3>
            {busy.myPlantDetail ? (
              <p className="status">{t("loadingSavedPlantCard")}</p>
            ) : (
              <>
                {!wateringSchedule.length && (
                  <p className="status">
                    {t("noWateringSchedule")}
                  </p>
                )}
                <div className="watering-month-nav">
                  <button
                    type="button"
                    className="btn-secondary btn-small"
                    aria-label="Mese precedente"
                    onClick={() => {
                      setCalendarMonth((prev) => {
                        if (prev === 0) {
                          setCalendarYear((y) => y - 1);
                          return 11;
                        }
                        return prev - 1;
                      });
                    }}
                  >
                    ◀
                  </button>
                  <span className="watering-month-title">{wateringMonthCalendar?.monthLabel || t("currentMonth")}</span>
                  <button
                    type="button"
                    className="btn-secondary btn-small"
                    aria-label="Mese successivo"
                    onClick={() => {
                      setCalendarMonth((prev) => {
                        if (prev === 11) {
                          setCalendarYear((y) => y + 1);
                          return 0;
                        }
                        return prev + 1;
                      });
                    }}
                  >
                    ▶
                  </button>
                </div>
                {googleCalendarUrl && (
                  <a
                    className="btn-secondary watering-export"
                    href={googleCalendarUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {t("exportGoogleCalendar")}
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
                {wateringSchedule.length > 0 && wateringMonthCalendar.highlightedCount === 0 && (
                  <p className="status">{t("noWateringSchedule2")}</p>
                )}

                <div className="watering-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setIsEditingFirstWaterDate((prev) => !prev)}
                  >
                    {t("changeFirstWaterDate")}
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
                        {busy.updateFirstWaterDate ? t("savingShort") : t("applyDate")}
                      </button>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </section>
      )}

      {selectedMyPlant?.plant_name && (
        <section className="panel">
          <h2>
            {t("askForCareAdvice")}
            {activeChatPlantName ? `: ${activeChatPlantName}` : ""}
          </h2>
          <form onSubmit={handleQuestion} className="chat-form">
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={4}
              placeholder={t("exampleQuestion")}
            />
            <button type="submit" disabled={!canAsk || busy.chat || !isLoggedIn}>
              {busy.chat ? t("preparingAnswer") : t("askCareBtn")}
            </button>
          </form>
          {chatAnswer && (
            <article className="answer">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{chatAnswer}</ReactMarkdown>
            </article>
          )}
        </section>
      )}
    </>
  );
}
