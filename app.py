import os
from datetime import date, datetime
import sqlite3

from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector
from mysql.connector import Error

app = Flask(__name__)
app.secret_key = "super-secret-key-change-later"  # for flash messages

# Use SQLite locally when LOCAL_DEV=1
USE_SQLITE = os.getenv("LOCAL_DEV") == "1"


def get_db_connection():
    """Return a DB connection: SQLite for local dev, MySQL/MariaDB for AWS."""
    if USE_SQLITE:
        conn = sqlite3.connect("fitness_local.db")
        conn.row_factory = sqlite3.Row  # dict-style access
        return conn

    config = {
        "host": os.getenv("DB_HOST"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "database": os.getenv("DB_NAME"),
    }
    missing = [k for k, v in config.items() if not v]
    if missing:
        raise RuntimeError(
            f"Missing DB config values: {', '.join(missing)}. "
            "Check environment variables DB_HOST, DB_USER, DB_PASSWORD, DB_NAME."
        )
    return mysql.connector.connect(**config)


def init_db():
    """Create tables if they don't exist."""
    conn = get_db_connection()
    cur = conn.cursor()

    if USE_SQLITE:
        # SQLite-compatible schema
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fitness_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_date TEXT NOT NULL,
                weight REAL,
                calories INTEGER,
                steps INTEGER
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL
            );
            """
        )
    else:
        # MySQL/MariaDB schema for RDS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fitness_entries (
                id INT AUTO_INCREMENT PRIMARY KEY,
                entry_date DATE NOT NULL,
                weight DECIMAL(5,2),
                calories INT,
                steps INT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) UNIQUE NOT NULL,
                value VARCHAR(255) NOT NULL
            );
            """
        )

    conn.commit()
    cur.close()
    conn.close()


@app.route("/", methods=["GET"])
def index():
    """Main page: show forms, table and chart."""
    try:
        conn = get_db_connection()
        if USE_SQLITE:
            cur = conn.cursor()
        else:
            cur = conn.cursor(dictionary=True)

        # Get calorie goal
        cur.execute("SELECT value FROM settings WHERE name = 'calorie_goal'")
        row = cur.fetchone()

        # row is sqlite3.Row or dict (MySQL cursor dict=True)
        calorie_goal = int(row["value"]) if row else None

        # Get last 30 entries (most recent first)
        cur.execute(
            """
            SELECT entry_date, weight, calories, steps
            FROM fitness_entries
            ORDER BY entry_date DESC, id DESC
            LIMIT 30;
            """
        )
        entries = cur.fetchall()

        # For SQLite, convert rows to dicts so templates behave consistently
        if USE_SQLITE:
            entries = [dict(e) for e in entries]

        cur.close()
        conn.close()
    except Error as e:
        return f"Database error: {e}", 500
    except Exception as e:
        return f"App error: {e}", 500

    # ---------------------------
    # Add per-entry status logic
    # ---------------------------
    # status = Over Goal / Within Goal (or N/A if no goal or calories)
    if calorie_goal is not None:
        for e in entries:
            calories = e.get("calories")
            if calories is None:
                e["status"] = "N/A"
            elif calories > calorie_goal:
                e["status"] = "Over Goal"
            else:
                e["status"] = "Within Goal"
    else:
        for e in entries:
            e["status"] = "N/A"

    # Prepare flags and chart data
    over_goal_flags = {}
    chart_labels = []
    chart_weights = []
    chart_calories = []
    chart_steps = []

    # Reverse entries for chart (oldest first)
    for e in reversed(entries):
        d = e["entry_date"]
        if isinstance(d, (datetime, date)):
            label = d.strftime("%Y-%m-%d")
        else:
            label = str(d)

        chart_labels.append(label)
        chart_weights.append(float(e["weight"]) if e["weight"] is not None else None)
        chart_calories.append(e["calories"] or 0)
        chart_steps.append(e["steps"] or 0)

    if calorie_goal is not None:
        for e in entries:
            d = e["entry_date"]
            key = d.strftime("%Y-%m-%d") if isinstance(d, (datetime, date)) else str(d)
            is_over = e["calories"] is not None and e["calories"] > calorie_goal
            over_goal_flags[key] = over_goal_flags.get(key, False) or is_over

    return render_template(
        "index.html",
        entries=entries,
        calorie_goal=calorie_goal,
        over_goal_flags=over_goal_flags,
        chart_labels=chart_labels,
        chart_weights=chart_weights,
        chart_calories=chart_calories,
        chart_steps=chart_steps,
    )


@app.route("/add", methods=["POST"])
def add_entry():
    """Add daily fitness entry."""
    try:
        entry_date = request.form.get("date")
        weight = request.form.get("weight") or None
        calories = request.form.get("calories") or None
        steps = request.form.get("steps") or None

        if not entry_date:
            flash("Date is required.", "error")
            return redirect(url_for("index"))

        conn = get_db_connection()
        cur = conn.cursor()

        if USE_SQLITE:
            cur.execute(
                """
                INSERT INTO fitness_entries (entry_date, weight, calories, steps)
                VALUES (?, ?, ?, ?)
                """,
                (entry_date, weight, calories, steps),
            )
        else:
            cur.execute(
                """
                INSERT INTO fitness_entries (entry_date, weight, calories, steps)
                VALUES (%s, %s, %s, %s)
                """,
                (entry_date, weight, calories, steps),
            )

        conn.commit()
        cur.close()
        conn.close()
        flash("Entry added.", "success")
    except Exception as e:
        flash(f"Error adding entry: {e}", "error")

    return redirect(url_for("index"))


@app.route("/set_goal", methods=["POST"])
def set_goal():
    """Set or update the daily calorie goal."""
    try:
        goal = request.form.get("calorie_goal")
        if not goal:
            flash("Calorie goal cannot be empty.", "error")
            return redirect(url_for("index"))

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("DELETE FROM settings WHERE name = 'calorie_goal'")

        if USE_SQLITE:
            cur.execute(
                "INSERT INTO settings (name, value) VALUES (?, ?)",
                ("calorie_goal", goal),
            )
        else:
            cur.execute(
                "INSERT INTO settings (name, value) VALUES (%s, %s)",
                ("calorie_goal", goal),
            )

        conn.commit()
        cur.close()
        conn.close()
        flash("Calorie goal updated.", "success")
    except Exception as e:
        flash(f"Error updating goal: {e}", "error")

    return redirect(url_for("index"))


@app.route("/health")
def health():
    """Simple health check for debugging."""
    try:
        conn = get_db_connection()
        conn.close()
        return "OK", 200
    except Exception:
        return "DB error", 500


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)