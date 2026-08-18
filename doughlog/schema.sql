CREATE TABLE IF NOT EXISTS recipe_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    formula_json TEXT NOT NULL,
    is_archived INTEGER NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flour_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    protein_pct REAL NOT NULL,
    ash_pct REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dough_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER,
    title TEXT NOT NULL,
    service_date TEXT NOT NULL,
    mix_datetime TEXT NOT NULL,
    formula_json TEXT NOT NULL,
    calculated_json TEXT NOT NULL,
    room_temp_f REAL,
    humidity_pct REAL,
    flour_temp_f REAL,
    water_temp_f REAL,
    desired_final_dough_temp_f REAL,
    final_dough_temp_f REAL,
    mix_stages_json TEXT NOT NULL DEFAULT '[]',
    mix_notes TEXT NOT NULL DEFAULT '',
    service_notes TEXT NOT NULL DEFAULT '',
    overall_rating INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (template_id) REFERENCES recipe_templates(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS dough_logs_service_date_idx ON dough_logs(service_date DESC);
CREATE INDEX IF NOT EXISTS dough_logs_rating_idx ON dough_logs(overall_rating);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dough_log_id INTEGER NOT NULL,
    filename TEXT NOT NULL UNIQUE,
    original_name TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (dough_log_id) REFERENCES dough_logs(id) ON DELETE CASCADE
);
