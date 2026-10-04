import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "asteroids.db")


def init_db(db_path=DB_PATH):
    """Ensure database tables exist using schema.sql."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "sql", "schema.sql")
    if not os.path.exists(schema_path):
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with sqlite3.connect(db_path) as conn:
        if os.path.exists(schema_path):
            with open(schema_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
        else:
            conn.executescript(
                """
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
                """
            )


def load_data(asteroid_data, db_path=DB_PATH):
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        for row in asteroid_data:
            cursor.execute(
                """
                INSERT OR IGNORE INTO asteroids (
                    asteroid_id,
                    name,
                    hazardous
                )
                VALUES (?, ?, ?)
                """,
                (
                    row["id"],
                    row["name"],
                    row["hazardous"]
                )
            )

            cursor.execute(
                """
                INSERT INTO close_approaches (
                    asteroid_id,
                    approach_date,
                    miss_distance_km
                )
                SELECT ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM close_approaches
                    WHERE asteroid_id = ?
                      AND approach_date = ?
                )
                """,
                (
                    row["id"],
                    row["closest_approach_date"],
                    row["miss_distance_km"],
                    row["id"],
                    row["closest_approach_date"]
                )
            )

        conn.commit()