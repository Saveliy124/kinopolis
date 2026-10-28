PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    duration_min INTEGER NOT NULL CHECK (duration_min BETWEEN 1 AND 600),
    age_rating TEXT NOT NULL DEFAULT '0+',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS halls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    rows_count INTEGER NOT NULL CHECK (rows_count BETWEEN 1 AND 30),
    seats_per_row INTEGER NOT NULL CHECK (seats_per_row BETWEEN 1 AND 50),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS seats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hall_id INTEGER NOT NULL REFERENCES halls(id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL CHECK (row_number > 0),
    seat_number INTEGER NOT NULL CHECK (seat_number > 0),
    UNIQUE (hall_id, row_number, seat_number)
);

CREATE TABLE IF NOT EXISTS screenings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE RESTRICT,
    hall_id INTEGER NOT NULL REFERENCES halls(id) ON DELETE RESTRICT,
    starts_at TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    base_price_cents INTEGER NOT NULL CHECK (base_price_cents BETWEEN 0 AND 10000000),
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (hall_id, starts_at)
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    screening_id INTEGER NOT NULL REFERENCES screenings(id) ON DELETE RESTRICT,
    code TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK (mode IN ('reserve', 'purchase')),
    status TEXT NOT NULL CHECK (status IN ('reserved', 'paid', 'cancelled', 'expired')),
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    screening_id INTEGER NOT NULL REFERENCES screenings(id) ON DELETE RESTRICT,
    seat_id INTEGER NOT NULL REFERENCES seats(id) ON DELETE RESTRICT,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    status TEXT NOT NULL CHECK (status IN ('reserved', 'paid', 'cancelled', 'expired')),
    UNIQUE (booking_id, seat_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_movies_active ON movies(active, title);
CREATE INDEX IF NOT EXISTS idx_screenings_movie_start ON screenings(movie_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_screenings_hall_start ON screenings(hall_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_bookings_user_created ON bookings(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bookings_expiry ON bookings(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_tickets_screening_status ON tickets(screening_id, status);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_ticket
    ON tickets(screening_id, seat_id)
    WHERE status IN ('reserved', 'paid');
