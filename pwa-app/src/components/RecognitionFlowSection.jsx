import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function RecognitionFlowSection({
  t,
  activeView,
  file,
  busy,
  handleSearch,
  fileInputRef,
  cameraInputRef,
  onFileChange,
  isDragActive,
  openFileDialog,
  onDropFile,
  onDragOver,
  onDragLeave,
  openCameraDialog,
  searchSteps,
  searchStepIndex,
  preview,
  searchResults,
  showSpeciesGrid,
  setShowSpeciesGrid,
  selectedSpecies,
  selectSpecies,
  speciesPreviews,
  speciesCommonNames,
  toOptimizedImage,
  loadMoreSpecies,
  plantCard,
  isSelectedDraft,
  draftBuildMessage,
  galleryImages,
  imageIndex,
  activeImage,
  secondaryImage,
  prevImage,
  nextImage,
  profileEntries,
  expandedProfileKey,
  setExpandedProfileKey,
  isLoggedIn,
  handleSavePlant,
  userPlantName,
  setUserPlantName,
  saveStatus,
}) {
  if (activeView !== "recognize") {
    return null;
  }

  return (
    <>
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
            <p className="dropzone-title">{t("uploadPlantImage")}</p>
            <p className="dropzone-subtitle">
              {file ? `${t("selectedFile")}: ${file.name}` : t("dropzoneSubtitle")}
            </p>
          </div>

          <button type="button" className="btn-secondary" onClick={openCameraDialog} disabled={busy.search}>
            {t("takePhoto")}
          </button>

          <button type="submit" disabled={busy.search}>
            {busy.search ? t("recognitionInProgress") : t("recognizePlant")}
          </button>

          {busy.search && (
            <div className="upload-progress" role="status" aria-live="polite">
              <span>{searchSteps[searchStepIndex]}</span>
            </div>
          )}
        </form>
        {preview && (
          <div className={`preview-shell ${busy.search ? "scanning" : ""}`}>
            <img className="preview" src={preview} alt={t("previewAlt")} />
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
              ▾ {t("showFoundSpecies")}
            </button>
          ) : (
            <>
              <div className="species-header">
                <h2>{t("foundSpecies")}</h2>
                <button className="btn-secondary btn-small" onClick={() => setShowSpeciesGrid(false)}>
                  ▴ {t("hideFoundSpecies")}
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
                        alt={`${t("exampleAlt")} ${item.species}`}
                        loading="lazy"
                        decoding="async"
                      />
                    )}
                    <strong>{item.species}</strong>
                    {!!speciesCommonNames[item.species] && (
                      <span className="result-common-name">({speciesCommonNames[item.species]})</span>
                    )}
                    {!!item.is_draft && (
                      <span className="badge-draft">🔨 {t("draftCard")}</span>
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
                {busy.moreSpecies ? t("searching") : `+ ${t("moreSpecies")}`}
              </button>
            </>
          )}
        </section>
      )}

      {busy.plant && <p className="status">{t("loadingPlantCard")}</p>}

      {!busy.plant && plantCard && (
        <section className="panel details">
          <div>
            <h2>{plantCard.title}</h2>
            {plantCard.common_name && <p>{t("commonNameLabel")}: {plantCard.common_name}</p>}
            {!!isSelectedDraft && (
              <div className="banner-draft">
                🔨 <strong>{t("draftBannerTitle")}</strong> - {t("draftCardDescription")}
              </div>
            )}
            {!!draftBuildMessage && <p className="status">{draftBuildMessage}</p>}

            {!!galleryImages.length && (
              <div className="gallery-wrap">
                <button
                  type="button"
                  className="gallery-nav"
                  onClick={prevImage}
                  aria-label={t("previousPhoto")}
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
                  aria-label={t("nextPhoto")}
                >
                  &gt;
                </button>
              </div>
            )}

            {!!profileEntries.length && (
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
            )}
          </div>

          <h3>{t("descriptionl")}</h3>
          <div className="summary markdown-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{plantCard.summary || ""}</ReactMarkdown>
          </div>

          {isLoggedIn && selectedSpecies && (
            <div className="my-plant-save">
              <h3>{t("saveOnYourPlants")}</h3>
              <form className="my-plant-form" onSubmit={handleSavePlant}>
                <input
                  type="text"
                  value={userPlantName}
                  onChange={(event) => setUserPlantName(event.target.value)}
                  placeholder={t("enterPlantName")}
                  maxLength={80}
                />
                <button type="submit" disabled={busy.savePlant}>
                  {busy.savePlant ? t("saving") : t("savePlant")}
                </button>
              </form>
              {saveStatus && <p className="status">{saveStatus}</p>}
            </div>
          )}
        </section>
      )}
    </>
  );
}
