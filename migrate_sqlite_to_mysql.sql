-- =============================================================
-- Migrazione Clorofilla: SQLite → MySQL
-- Script SQL per creare schema e strutture
-- =============================================================

-- Catalogo piante (da plants.db)
CREATE TABLE IF NOT EXISTS plants (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    species_name VARCHAR(255) NOT NULL UNIQUE,
    indexed TINYINT(1) NOT NULL DEFAULT 0,
    image_paths LONGTEXT,
    annaffiatura_gg INT,
    annaffiatura_time VARCHAR(20),
    luce VARCHAR(255),
    temperatura VARCHAR(255),
    umidita VARCHAR(255),
    altezza_media VARCHAR(255),
    pulizia VARCHAR(255),
    terriccio VARCHAR(255),
    concimazione VARCHAR(255),
    prevenzione VARCHAR(255),
    updated_at VARCHAR(40) NOT NULL,
    KEY idx_species_name (species_name),
    KEY idx_indexed (indexed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Alias LeafSnap → specie del DB
CREATE TABLE IF NOT EXISTS leafsnap_aliases (
    leafsnap_label VARCHAR(255) PRIMARY KEY,
    db_species_name VARCHAR(255) NOT NULL,
    FOREIGN KEY (db_species_name) REFERENCES plants(species_name) ON DELETE CASCADE,
    KEY idx_species (db_species_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- Dati utenti (da user_plants.db)
-- =============================================================

-- Piante salvate dagli utenti
CREATE TABLE IF NOT EXISTS user_plants (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    plant_name VARCHAR(255) NOT NULL,
    user_given_name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    user_email VARCHAR(255),
    user_photo_url TEXT,
    created_at VARCHAR(40) NOT NULL,
    KEY idx_user_id (user_id),
    KEY idx_user_email (user_email),
    KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Foto associate alle piante utenti
CREATE TABLE IF NOT EXISTS user_plant_photos (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    plant_id BIGINT NOT NULL,
    photo_url TEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    FOREIGN KEY (plant_id) REFERENCES user_plants(id) ON DELETE CASCADE,
    KEY idx_plant_id (plant_id),
    KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Utenti registrati con Google OAuth
CREATE TABLE IF NOT EXISTS registered_users (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    google_sub VARCHAR(255) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL,
    registered_at VARCHAR(40) NOT NULL,
    KEY idx_email (email),
    KEY idx_registered_at (registered_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Log di tutti i riconoscimenti (ospite e autenticati)
CREATE TABLE IF NOT EXISTS recognition_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id VARCHAR(255) NOT NULL,
    user_email VARCHAR(255),
    user_type VARCHAR(16) NOT NULL,
    chosen_species VARCHAR(255) NOT NULL,
    image_url TEXT,
    used_openai TINYINT(1) NOT NULL DEFAULT 0,
    recognition_ms INT,
    created_at VARCHAR(40) NOT NULL,
    KEY idx_user_id (user_id),
    KEY idx_created_at (created_at),
    KEY idx_user_type (user_type),
    KEY idx_species (chosen_species)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================
-- Verifiche post-migrazione (eseguire dopo import dati)
-- =============================================================

-- SELECT COUNT(*) AS total_plants FROM plants;
-- SELECT COUNT(*) AS total_aliases FROM leafsnap_aliases;
-- SELECT COUNT(*) AS total_user_plants FROM user_plants;
-- SELECT COUNT(*) AS total_photos FROM user_plant_photos;
-- SELECT COUNT(*) AS total_users FROM registered_users;
-- SELECT COUNT(*) AS total_logs FROM recognition_logs;
