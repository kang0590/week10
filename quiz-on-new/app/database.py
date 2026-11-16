import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / 'quiz.db'


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 10000')
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            option1 TEXT NOT NULL,
            option2 TEXT NOT NULL,
            option3 TEXT NOT NULL,
            option4 TEXT NOT NULL,
            answer INTEGER NOT NULL CHECK(answer BETWEEN 1 AND 4),
            FOREIGN KEY(quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS rooms (
            code TEXT PRIMARY KEY,
            quiz_id INTEGER NOT NULL,
            host_token TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'waiting',
            question_index INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(quiz_id) REFERENCES quizzes(id)
        );
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT NOT NULL,
            nickname TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            UNIQUE(room_code, nickname),
            FOREIGN KEY(room_code) REFERENCES rooms(code) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            participant_id INTEGER NOT NULL,
            selected_answer INTEGER NOT NULL,
            is_correct INTEGER NOT NULL,
            UNIQUE(room_code, question_id, participant_id),
            FOREIGN KEY(room_code) REFERENCES rooms(code) ON DELETE CASCADE,
            FOREIGN KEY(question_id) REFERENCES questions(id),
            FOREIGN KEY(participant_id) REFERENCES participants(id)
        );
        ''')
        # Older versions of QUIZ ON may already have rooms without question_index.
        cols = {row['name'] for row in conn.execute('PRAGMA table_info(rooms)')}
        if 'question_index' not in cols:
            conn.execute('ALTER TABLE rooms ADD COLUMN question_index INTEGER NOT NULL DEFAULT 0')
