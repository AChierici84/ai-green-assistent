export default function AdminConsoleSection({
  t,
  isLoggedIn,
  isAdmin,
  activeView,
  auth,
  busy,
  adminChartDays,
  loadAdminConsole,
  adminSpeciesName,
  setAdminSpeciesName,
  handleAdminSpeciesBuild,
  adminSpeciesBuildStatus,
  adminHealth,
  adminHealthError,
  adminConsole,
  formatDurationMs,
  applyAdminChartDays,
}) {
  if (!isLoggedIn || !isAdmin || activeView !== "admin") {
    return null;
  }

  return (
    <section className="panel">
      <div className="species-header">
        <h2>{t("adminConsole")}</h2>
        <button
          type="button"
          className="btn-secondary btn-small"
          onClick={() => loadAdminConsole(adminChartDays)}
          disabled={busy.adminConsole}
        >
          {busy.adminConsole ? t("updating") : t("update")}
        </button>
      </div>

      <p className="status">Admin: {auth?.user?.email || ""}</p>

      <div className="admin-chart-block">
        <h3>{t("buildSpecies")}</h3>
        <p className="admin-section-note">{t("buildSpeciesNote")}</p>
        <div className="admin-build-form">
          <input
            type="text"
            value={adminSpeciesName}
            onChange={(event) => setAdminSpeciesName(event.target.value)}
            placeholder="Es. Plumbago auriculata"
            maxLength={160}
          />
          <button
            type="button"
            className="btn-secondary btn-small"
            onClick={() => handleAdminSpeciesBuild(false)}
            disabled={busy.adminSpeciesBuild}
          >
            {busy.adminSpeciesBuild ? t("buildingBtn") : t("buildBtn")}
          </button>
          <button
            type="button"
            className="btn-secondary btn-small"
            onClick={() => handleAdminSpeciesBuild(true)}
            disabled={busy.adminSpeciesBuild}
          >
            {busy.adminSpeciesBuild ? t("buildingBtn") : t("rebuildBtn")}
          </button>
        </div>
        {adminSpeciesBuildStatus && <p className="status">{adminSpeciesBuildStatus}</p>}
      </div>

      <div className="admin-chart-block">
        <h3>{t("ApiStatus")} (/health)</h3>
        <div className="admin-stats-grid">
          <div className="admin-stat-card">
            <strong>{adminHealth?.status || "-"}</strong>
            <span>{t("ApiStatus")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminHealth?.model || "-"}</strong>
            <span>{t("searchModel")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>
              {typeof adminHealth?.search_backend_ready === "boolean"
                ? (adminHealth.search_backend_ready ? t("yes") : t("no"))
                : "-"}
            </strong>
            <span>{t("searchBackendReady")}</span>
          </div>
        </div>
        {adminHealthError && <p className="status">{adminHealthError}</p>}
      </div>

      <div className="admin-chart-block">
        <h3>{t("usersAndCollection")}</h3>
        <p className="admin-section-note">{t("usersAndCollectionNote")}</p>
        <div className="admin-stats-grid">
          <div className="admin-stat-card">
            <strong>{adminConsole?.stats?.registered_users_total ?? 0}</strong>
            <span>{t("registeredUsers")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.stats?.saved_plants_total ?? 0}</strong>
            <span>{t("savedPlants")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.stats?.external_user_images_total ?? 0}</strong>
            <span>{t("externalUserImages")}</span>
          </div>
        </div>
      </div>

      <div className="admin-chart-block">
        <h3>{t("recognitions")}</h3>
        <p className="admin-section-note">{t("recognitionsNote")}</p>
        <div className="admin-stats-grid">
          <div className="admin-stat-card">
            <strong>{adminConsole?.recognition?.total ?? 0}</strong>
            <span>{t("totalRecognitions")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.recognition?.guest_total ?? 0}</strong>
            <span>{t("guestRecognitions")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.recognition?.openai_total ?? 0}</strong>
            <span>{t("openaiSupport")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.recognition?.with_image_total ?? 0}</strong>
            <span>{t("withImageUrl")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{formatDurationMs(adminConsole?.recognition?.avg_recognition_ms)}</strong>
            <span>{t("avgRecognitionTime")}</span>
          </div>
        </div>
      </div>

      <div className="admin-chart-block">
        <h3>{t("inventoryAndIndices")}</h3>
        <p className="admin-section-note">{t("inventoryAndIndicesNote")}</p>
        <div className="admin-stats-grid">
          <div className="admin-stat-card">
            <strong>{adminConsole?.inventory?.catalog?.species_db_total ?? 0}</strong>
            <span>{t("speciesDbTotal")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.inventory?.catalog?.species_rag_total ?? 0}</strong>
            <span>{t("speciesRagTotal")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.inventory?.faiss?.plantclef?.species_total ?? 0}</strong>
            <span>{t("plantclefSpeciesIndexed")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.inventory?.faiss?.plantclef?.images_total ?? 0}</strong>
            <span>{t("plantclefImagesIndexed")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.inventory?.faiss?.leafsnap?.species_total ?? 0}</strong>
            <span>{t("leafsnapSpeciesIndexed")}</span>
          </div>
          <div className="admin-stat-card">
            <strong>{adminConsole?.inventory?.faiss?.leafsnap?.images_total ?? 0}</strong>
            <span>{t("leafsnapImagesIndexed")}</span>
          </div>
        </div>
      </div>

      <div className="admin-filter-row" role="group" aria-label={t("charts")}>
        <span className="admin-filter-label">{t("charts")}:</span>
        {[7, 30, 90].map((days) => (
          <button
            key={days}
            type="button"
            className={`btn-secondary btn-small admin-filter-btn ${adminChartDays === days ? "is-active" : ""}`}
            onClick={() => applyAdminChartDays(days)}
            disabled={busy.adminConsole}
          >
            {days} {t("days")}
          </button>
        ))}
      </div>

      {!!(adminConsole?.charts?.top_species || []).length && (
        <div className="admin-chart-block">
          <h3>{t("topRecognizedSpecies", { days: adminConsole?.recognition?.chart_days || adminChartDays })}</h3>
          <div className="admin-bar-list">
            {(adminConsole?.charts?.top_species || []).map((row) => {
              const maxVal = Math.max(...(adminConsole?.charts?.top_species || []).map((r) => Number(r.count || 0)), 1);
              const width = Math.max(6, Math.round((Number(row.count || 0) / maxVal) * 100));
              return (
                <div key={row.species} className="admin-bar-row">
                  <span className="admin-bar-label">{row.species}</span>
                  <div className="admin-bar-track">
                    <span className="admin-bar-fill" style={{ width: `${width}%` }} />
                  </div>
                  <strong className="admin-bar-value">{row.count}</strong>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {!!(adminConsole?.charts?.daily_series || []).length && (
        <div className="admin-chart-block">
          <h3>{t("dailyTrend", { days: adminConsole?.recognition?.chart_days || adminChartDays })}</h3>
          <div className="admin-columns-chart" role="img" aria-label={t("dailyTrendlabel")}>
            {(adminConsole?.charts?.daily_series || []).map((row) => {
              const all = adminConsole?.charts?.daily_series || [];
              const maxVal = Math.max(...all.map((r) => Number(r.total || 0)), 1);
              const total = Number(row.total || 0);
              const openai = Number(row.openai || 0);
              const heightPct = Math.max(4, Math.round((total / maxVal) * 100));
              const dayLabel = String(row.day || "").slice(5);

              return (
                <div key={row.day} className="admin-column-item" title={`${row.day} - Tot: ${total}, OpenAI: ${openai}`}>
                  <div className="admin-column-track">
                    <span className="admin-column-fill" style={{ height: `${heightPct}%` }} />
                  </div>
                  <strong className="admin-column-value">{total}</strong>
                  <span className="admin-column-label">{dayLabel}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {busy.adminConsole && <p className="status">{t("loadingAdminData")}</p>}

      {!busy.adminConsole && !(adminConsole?.users || []).length && (
        <p className="status">{t("noRegisteredUsers")}</p>
      )}

      {!busy.adminConsole && !!(adminConsole?.users || []).length && (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>{t("email")}</th>
                <th>{t("registrationDate")}</th>
              </tr>
            </thead>
            <tbody>
              {(adminConsole?.users || []).map((item) => (
                <tr key={`${item.email}-${item.registered_at}`}>
                  <td>{item.email}</td>
                  <td>{item.registered_at_display || item.registered_at || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
