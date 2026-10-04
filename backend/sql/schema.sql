CREATE TABLE IF NOT EXISTS asteroids (
    asteroid_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hazardous BOOLEAN NOT NULL
);
CREATE TABLE IF NOT EXISTS close_approaches (
    approach_id INTEGER PRIMARY KEY,
    asteroid_id TEXT NOT NULL,
    approach_date TEXT NOT NULL,
    miss_distance_km REAL NOT NULL,
    FOREIGN KEY (asteroid_id) REFERENCES asteroids(asteroid_id)
);