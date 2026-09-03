
import json
import os
from datetime import datetime

from env_loader import load_env_file

load_env_file()

MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "rural_healthcare")

# Resolved relative to THIS file's directory, not the process's current
# working directory -- a bare "ca.pem" only worked when the app happened
# to be launched from inside backend/. Render's Start Command working
# directory isn't guaranteed to match that, and when the CA file can't be
# found at all (it was previously never even committed -- see .gitignore
# history) mysql.connector's SSL handshake stalls rather than failing
# fast, which was long enough to blow past Render's port-scan timeout
# before the app ever got to bind a port.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MYSQL_SSL_CA = os.environ.get("MYSQL_SSL_CA", os.path.join(_BASE_DIR, "ca.pem"))

_pool = None
_pool_init_failed = False


def _get_pool():
    """Lazily create the connection pool. Returns None (never raises) when
    MySQL is unreachable/misconfigured -- callers treat that as "the store
    is unavailable right now" the same way _connect_mongo() callers do."""
    global _pool, _pool_init_failed
    if _pool is not None:
        return _pool
    if _pool_init_failed:
        return None
    try:
        from mysql.connector import pooling

      
        ssl_kwargs = {}
        if MYSQL_SSL_CA and os.path.isfile(MYSQL_SSL_CA):
            ssl_kwargs = {"ssl_ca": MYSQL_SSL_CA, "ssl_verify_cert": True}
        elif MYSQL_SSL_CA:
            print(f"[mysql_store] WARNING: MYSQL_SSL_CA={MYSQL_SSL_CA!r} not found -- "
                  f"connecting without CA verification (Aiven still requires TLS; "
                  f"this only skips verifying its identity)")

        _pool = pooling.MySQLConnectionPool(
            pool_name="rural_healthcare_pool",
            pool_size=5,
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            connection_timeout=5,
            **ssl_kwargs,
        )
        return _pool
    except Exception as exc:
        print(f"[mysql_store] connection pool init failed: {exc!r}")
        _pool_init_failed = True
        return None


_SCHEMA_STATEMENTS = [
 
    """
    CREATE TABLE IF NOT EXISTS legacy_users (
        email VARCHAR(255) PRIMARY KEY,
        full_name VARCHAR(255),
        password VARCHAR(255) NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # Admin-dashboard assessment records written by /predict. `id` mirrors
    # the sequential id the old Mongo store assigned by hand -- AUTO_INCREMENT
    # gives the same guarantee for free.
    """
    CREATE TABLE IF NOT EXISTS patients (
        id INT AUTO_INCREMENT PRIMARY KEY,
        created_at DATETIME,
        patient_name VARCHAR(255),
        age VARCHAR(20),
        bp_systolic VARCHAR(20),
        bp_diastolic VARCHAR(20),
        heart_rate VARCHAR(20),
        temperature VARCHAR(20),
        sugar_level VARCHAR(20),
        lab_test_result VARCHAR(255),
        symptoms TEXT,
        medical_report_name VARCHAR(255),
        predicted_disease VARCHAR(255),
        confidence DOUBLE,
        risk_category VARCHAR(50),
        risk_level VARCHAR(50),
        risk_score DOUBLE,
        recommendation TEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # session_id doubles as the conversation id everywhere in app.py.
    """
    CREATE TABLE IF NOT EXISTS chat_conversations (
        session_id VARCHAR(100) PRIMARY KEY,
        user_id VARCHAR(100),
        user_email VARCHAR(255),
        title VARCHAR(200),
        custom_title TINYINT(1) NOT NULL DEFAULT 0,
        created_at VARCHAR(40),
        updated_at VARCHAR(40),
        message_count INT NOT NULL DEFAULT 0,
        INDEX idx_chat_conversations_user_id (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id VARCHAR(64) PRIMARY KEY,
        conversation_id VARCHAR(100) NOT NULL,
        sender VARCHAR(20),
        message_text LONGTEXT,
        timestamp VARCHAR(40),
        kind VARCHAR(20),
        file_name VARCHAR(255),
        file_content LONGTEXT,
        INDEX idx_chat_messages_conversation_id (conversation_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_usage (
        user_email VARCHAR(255) PRIMARY KEY,
        count INT NOT NULL DEFAULT 0,
        window_start VARCHAR(40),
        updated_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
  
    """
    CREATE TABLE IF NOT EXISTS rag_chat_log (
        id INT AUTO_INCREMENT PRIMARY KEY,
        session_id VARCHAR(100),
        timestamp DATETIME,
        question TEXT,
        detected_language VARCHAR(50),
        retrieved_chunks TEXT,
        retrieved_sources TEXT,
        retrieval_time_ms DOUBLE,
        llm_response LONGTEXT,
        llm_time_ms DOUBLE,
        total_time_ms DOUBLE,
        model_used VARCHAR(100),
        INDEX idx_rag_chat_log_session_id (session_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # Self-registered doctor accounts (/doctor-register in app.py). Replaces
    # doctor_accounts.json as the source of truth -- Render's web-service
    # filesystem is ephemeral (wiped on every deploy/restart unless a paid
    # persistent Disk is attached), so a JSON-only store silently lost every
    # doctor who signed up between deploys. The JSON file is kept as a
    # best-effort local backup only; see _load_doctor_accounts() in app.py.
    """
    CREATE TABLE IF NOT EXISTS doctor_accounts (
        email VARCHAR(255) PRIMARY KEY,
        password_hash VARCHAR(255) NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id INT AUTO_INCREMENT PRIMARY KEY,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        name VARCHAR(255),
        email VARCHAR(255),
        subject VARCHAR(255),
        message TEXT,
        rating INT,
        source VARCHAR(50),
        delivery VARCHAR(50),
        delivery_status VARCHAR(50)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

_COLUMN_MIGRATIONS = [
    ("patients", "email", "VARCHAR(255)"),
    ("feedback", "rating", "INT"),
]


def init_schema() -> None:
    """Create every table this app needs if it doesn't exist yet, and add
    any columns introduced after a table already existed. Safe to call
    every startup (idempotent)."""
    pool = _get_pool()
    if pool is None:
        print("[mysql_store] MySQL unavailable -- database features disabled.")
        return
    conn = None
    try:
        conn = pool.get_connection()
        cur = conn.cursor()
        for statement in _SCHEMA_STATEMENTS:
            cur.execute(statement)

        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = %s",
            (MYSQL_DATABASE,),
        )
        existing = {(t, c) for (t, c) in cur.fetchall()}
        for table, column, definition in _COLUMN_MIGRATIONS:
            if (table, column) in existing:
                continue
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            print(f"[mysql_store] added column {table}.{column}")

        conn.commit()
        cur.close()
        print("[mysql_store] schema ready (legacy_users, patients, "
              "chat_conversations, chat_messages, chat_usage, rag_chat_log, "
              "doctor_accounts, feedback).")
    except Exception as exc:
        print(f"[mysql_store] init_schema failed: {exc!r}")
    finally:
        if conn is not None:
            conn.close()


def _get_conn():
    """Borrow a pooled connection, or None when MySQL is unavailable."""
    pool = _get_pool()
    if pool is None:
        return None
    try:
        return pool.get_connection()
    except Exception as exc:
        print(f"[mysql_store] get_connection failed: {exc!r}")
        return None


def is_available() -> bool:
    """Cheap reachability check -- True once the connection pool exists."""
    return _get_pool() is not None



def legacy_user_get(email: str) -> dict | None:
    """One legacy login record ({fullName, email, password}), or None."""
    email = (email or "").strip().lower()
    if not email:
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT email, full_name, password FROM legacy_users WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return {"email": row["email"], "fullName": row["full_name"] or "", "password": row["password"]}
    except Exception as exc:
        print(f"[mysql_store] legacy_user_get failed: {exc!r}")
        return None
    finally:
        conn.close()


def legacy_user_upsert(email: str, full_name: str, password: str) -> bool:
    """Insert a new legacy login record, or update full_name/password on an
    existing one (keyed by email)."""
    email = (email or "").strip().lower()
    if not email:
        return False
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO legacy_users (email, full_name, password)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE full_name = VALUES(full_name), password = VALUES(password)
            """,
            (email, full_name or "", password),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        print(f"[mysql_store] legacy_user_upsert failed: {exc!r}")
        return False
    finally:
        conn.close()


def legacy_users_get_all() -> dict:
    """All legacy login records as {email: {fullName, email, password}} --
    the exact shape the old in-memory `users` dict (loaded from users.json)
    used, so callers don't need to change their access pattern."""
    conn = _get_conn()
    if conn is None:
        return {}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT email, full_name, password FROM legacy_users")
        rows = cur.fetchall()
        cur.close()
        return {
            row["email"]: {"fullName": row["full_name"] or "", "email": row["email"], "password": row["password"]}
            for row in rows
        }
    except Exception as exc:
        print(f"[mysql_store] legacy_users_get_all failed: {exc!r}")
        return {}
    finally:
        conn.close()


# ===== PATIENTS (admin-dashboard assessment records) =====
# Replaces the old MongoDB `patients` collection +   patients.json fallback.

def _patient_row_to_record(row: dict) -> dict:
    """DB row (snake_case) -> the camelCase dict shape the frontend
    (AdminDashboard.tsx) has always received from Mongo/patients.json."""
    symptoms = row.get("symptoms")
    try:
        symptoms = json.loads(symptoms) if symptoms else []
    except Exception:
        symptoms = []
    created_at = row.get("created_at")
    return {
        "id": row.get("id"),
        "createdAt": created_at.isoformat(timespec="seconds") if hasattr(created_at, "isoformat") else (created_at or ""),
        "email": row.get("email") or "",
        "patientName": row.get("patient_name") or "",
        "age": row.get("age") or "",
        "bloodPressureSystolic": row.get("bp_systolic") or "",
        "bloodPressureDiastolic": row.get("bp_diastolic") or "",
        "heartRate": row.get("heart_rate") or "",
        "temperature": row.get("temperature") or "",
        "sugarLevel": row.get("sugar_level") or "",
        "labTestResult": row.get("lab_test_result") or "",
        "symptoms": symptoms,
        "medicalReportName": row.get("medical_report_name") or "",
        "predictedDisease": row.get("predicted_disease") or "N/A",
        "confidence": row.get("confidence") if row.get("confidence") is not None else 0,
        "riskCategory": row.get("risk_category") or "Unknown",
        "riskLevel": row.get("risk_level") or "Unknown",
        "riskScore": row.get("risk_score") if row.get("risk_score") is not None else 0,
        "recommendation": row.get("recommendation") or "",
    }


def insert_patient(record: dict) -> dict | None:
    """Insert one assessment record (camelCase dict, same shape
    store_patient_record() in app.py used to hand to Mongo). Returns the
    inserted record (with its new `id` + `createdAt`), or None on failure."""
    conn = _get_conn()
    if conn is None:
        return None
    try:
        cur = conn.cursor()
        created_at = record.get("createdAt") or datetime.now().isoformat(timespec="seconds")
        cur.execute(
            """
            INSERT INTO patients (
                created_at, email, patient_name, age, bp_systolic, bp_diastolic, heart_rate,
                temperature, sugar_level, lab_test_result, symptoms, medical_report_name,
                predicted_disease, confidence, risk_category, risk_level, risk_score, recommendation
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                created_at,
                record.get("email", ""),
                record.get("patientName", ""),
                record.get("age", ""),
                record.get("bloodPressureSystolic", ""),
                record.get("bloodPressureDiastolic", ""),
                record.get("heartRate", ""),
                record.get("temperature", ""),
                record.get("sugarLevel", ""),
                record.get("labTestResult", ""),
                json.dumps(record.get("symptoms") or []),
                record.get("medicalReportName", ""),
                record.get("predictedDisease", "N/A"),
                float(record.get("confidence") or 0),
                record.get("riskCategory", "Unknown"),
                record.get("riskLevel", "Unknown"),
                float(record.get("riskScore") or 0),
                record.get("recommendation", ""),
            ),
        )
        new_id = cur.lastrowid
        conn.commit()
        cur.close()
        out = dict(record)
        out["id"] = new_id
        out["createdAt"] = created_at
        return out
    except Exception as exc:
        print(f"[mysql_store] insert_patient failed: {exc!r}")
        return None
    finally:
        conn.close()


def list_patients() -> list:
    """All patient records, id ascending (matches the old Mongo
    .sort([("id", 1)]) order). Empty list when MySQL is unavailable."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM patients ORDER BY id ASC")
        rows = cur.fetchall()
        cur.close()
        return [_patient_row_to_record(row) for row in rows]
    except Exception as exc:
        print(f"[mysql_store] list_patients failed: {exc!r}")
        return []
    finally:
        conn.close()


def update_patient_risk_level(patient_id: int, risk_level: str) -> bool:
    """Overwrite one patient row's risk_level in place (used by
    migrate_risk_levels.py after a classification-rule change)."""
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute("UPDATE patients SET risk_level = %s WHERE id = %s", (risk_level, patient_id))
        changed = cur.rowcount > 0
        conn.commit()
        cur.close()
        return changed
    except Exception as exc:
        print(f"[mysql_store] update_patient_risk_level failed: {exc!r}")
        return False
    finally:
        conn.close()


def delete_patient(patient_id: int) -> bool:
    """True when a row was actually deleted."""
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM patients WHERE id = %s", (patient_id,))
        deleted = cur.rowcount > 0
        conn.commit()
        cur.close()
        return deleted
    except Exception as exc:
        print(f"[mysql_store] delete_patient failed: {exc!r}")
        return False
    finally:
        conn.close()


def delete_all_patients() -> int:
    """Number of rows deleted (0 when unavailable or already empty)."""
    conn = _get_conn()
    if conn is None:
        return 0
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM patients")
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        return deleted
    except Exception as exc:
        print(f"[mysql_store] delete_all_patients failed: {exc!r}")
        return 0
    finally:
        conn.close()


# ===== CHAT CONVERSATIONS / MESSAGES / USAGE =====
# chat_store.json remains the fast primary read/write path (see app.py);
# these tables are kept in sync the same way they were kept in sync with
# MongoDB before -- best-effort mirror on every write, read-through first
# on the list/get endpoints so data survives a chat_store.json loss/reset.

def chat_store_is_empty() -> bool:
    """True when chat_conversations has zero rows (or MySQL is unavailable
    -- callers treat that as 'nothing to skip migrating' the same way)."""
    conn = _get_conn()
    if conn is None:
        return True
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM chat_conversations LIMIT 1")
        return cur.fetchone() is None
    except Exception as exc:
        print(f"[mysql_store] chat_store_is_empty failed: {exc!r}")
        return True
    finally:
        conn.close()


def chat_conversation_upsert(
    session_id: str, user_id: str, user_email: str, title: str,
    custom_title: bool, created_at: str, updated_at: str, message_count: int,
) -> bool:
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chat_conversations
                (session_id, user_id, user_email, title, custom_title, created_at, updated_at, message_count)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                user_id = VALUES(user_id), user_email = VALUES(user_email), title = VALUES(title),
                updated_at = VALUES(updated_at), message_count = VALUES(message_count)
            """,
            (session_id, user_id, user_email or "", title, 1 if custom_title else 0, created_at, updated_at, message_count),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        print(f"[mysql_store] chat_conversation_upsert failed: {exc!r}")
        return False
    finally:
        conn.close()


def chat_conversations_list(user_id: str) -> list:
    """A user's conversations, newest first. Empty list when unavailable
    (caller falls back to the JSON store, matching the old Mongo-mirror
    read-through behavior)."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT session_id, user_id, user_email, title, custom_title, created_at, updated_at, message_count "
            "FROM chat_conversations WHERE user_id = %s ORDER BY updated_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "id": row["session_id"],
                "session_id": row["session_id"],
                "user_id": row["user_id"] or "",
                "user_email": row["user_email"] or "",
                "title": row["title"] or "",
                "custom_title": bool(row["custom_title"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": row["message_count"],
            }
            for row in rows
        ]
    except Exception as exc:
        print(f"[mysql_store] chat_conversations_list failed: {exc!r}")
        return []
    finally:
        conn.close()


def chat_message_insert(msg: dict) -> bool:
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chat_messages (id, conversation_id, sender, message_text, timestamp, kind, file_name, file_content)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                msg.get("id"), msg.get("conversation_id"), msg.get("sender"),
                msg.get("message_text"), msg.get("timestamp"), msg.get("kind"),
                msg.get("file_name"), msg.get("file_content"),
            ),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        print(f"[mysql_store] chat_message_insert failed: {exc!r}")
        return False
    finally:
        conn.close()


def chat_messages_list(conversation_id: str) -> list:
    """A conversation's messages, oldest first. Empty list when unavailable."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, conversation_id, sender, message_text, timestamp, kind, file_name, file_content "
            "FROM chat_messages WHERE conversation_id = %s ORDER BY timestamp ASC",
            (conversation_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [{k: v for k, v in row.items() if v is not None} for row in rows]
    except Exception as exc:
        print(f"[mysql_store] chat_messages_list failed: {exc!r}")
        return []
    finally:
        conn.close()


def chat_conversation_delete(conversation_id: str) -> None:
    """Delete a conversation + its messages. Never raises."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_conversations WHERE session_id = %s", (conversation_id,))
        cur.execute("DELETE FROM chat_messages WHERE conversation_id = %s", (conversation_id,))
        conn.commit()
        cur.close()
    except Exception as exc:
        print(f"[mysql_store] chat_conversation_delete failed: {exc!r}")
    finally:
        conn.close()


def chat_conversation_rename(conversation_id: str, title: str) -> bool:
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE chat_conversations SET title = %s, custom_title = 1 WHERE session_id = %s",
            (title, conversation_id),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        print(f"[mysql_store] chat_conversation_rename failed: {exc!r}")
        return False
    finally:
        conn.close()


def chat_usage_get(user_email: str) -> dict | None:
    """{count, window_start} for one user, or None (unavailable/no record --
    caller falls back to the JSON store either way)."""
    conn = _get_conn()
    if conn is None:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT count, window_start FROM chat_usage WHERE user_email = %s", (user_email,))
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return {"count": row["count"], "window_start": row["window_start"]}
    except Exception as exc:
        print(f"[mysql_store] chat_usage_get failed: {exc!r}")
        return None
    finally:
        conn.close()


def chat_usage_set(user_email: str, count: int, window_start, updated_at: str) -> bool:
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chat_usage (user_email, count, window_start, updated_at)
            VALUES (%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE count = VALUES(count), window_start = VALUES(window_start), updated_at = VALUES(updated_at)
            """,
            (user_email, count, window_start, updated_at),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        print(f"[mysql_store] chat_usage_set failed: {exc!r}")
        return False
    finally:
        conn.close()


# ===== RAG CHAT LOG (diagnostics) =====
# Replaces the old MongoDB `rag_chat_history` collection. Insert-only from
# the app's perspective; /chat-history and /rag-stats read it back.

def rag_log_insert(entry: dict) -> bool:
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        ts = entry.get("timestamp")
        ts_sql = ts if hasattr(ts, "isoformat") else datetime.now()
        cur.execute(
            """
            INSERT INTO rag_chat_log (
                session_id, timestamp, question, detected_language, retrieved_chunks,
                retrieved_sources, retrieval_time_ms, llm_response, llm_time_ms, total_time_ms, model_used
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                entry.get("session_id", ""),
                ts_sql,
                entry.get("question", ""),
                entry.get("detected_language", ""),
                json.dumps(entry.get("retrieved_chunks") or []),
                json.dumps(entry.get("retrieved_sources") or []),
                float(entry.get("retrieval_time_ms") or 0),
                entry.get("llm_response", ""),
                float(entry.get("llm_time_ms") or 0),
                float(entry.get("total_time_ms") or 0),
                entry.get("model_used", ""),
            ),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        print(f"[mysql_store] rag_log_insert failed: {exc!r}")
        return False
    finally:
        conn.close()


def rag_log_recent(session_id: str = "", limit: int = 20) -> list | None:
    """Recent log rows, newest first, or None when MySQL is unavailable
    (caller returns 503, same as when Mongo was down)."""
    conn = _get_conn()
    if conn is None:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        base = (
            "SELECT session_id, timestamp, question, detected_language, retrieved_chunks, "
            "retrieved_sources, retrieval_time_ms, llm_response, llm_time_ms, total_time_ms, model_used "
            "FROM rag_chat_log"
        )
        if session_id:
            cur.execute(base + " WHERE session_id = %s ORDER BY timestamp DESC LIMIT %s", (session_id, limit))
        else:
            cur.execute(base + " ORDER BY timestamp DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
        cur.close()
        for row in rows:
            for key in ("retrieved_chunks", "retrieved_sources"):
                try:
                    row[key] = json.loads(row[key]) if row[key] else []
                except Exception:
                    row[key] = []
            ts = row.get("timestamp")
            row["timestamp"] = ts.isoformat() if hasattr(ts, "isoformat") else ts
        return rows
    except Exception as exc:
        print(f"[mysql_store] rag_log_recent failed: {exc!r}")
        return None
    finally:
        conn.close()


def rag_log_stats() -> dict | None:
    """Aggregate stats across all RAG log rows, or None when MySQL is
    unavailable (caller returns 503, same as when Mongo was down)."""
    conn = _get_conn()
    if conn is None:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS total FROM rag_chat_log")
        total = cur.fetchone()["total"]
        if total == 0:
            cur.close()
            return {
                "total_conversations": 0,
                "avg_retrieval_ms": 0.0,
                "avg_llm_ms": 0.0,
                "avg_total_ms": 0.0,
                "top_sources": [],
            }
        cur.execute(
            "SELECT AVG(retrieval_time_ms) AS a, AVG(llm_time_ms) AS b, AVG(total_time_ms) AS c FROM rag_chat_log"
        )
        avgs = cur.fetchone()
        cur.execute("SELECT retrieved_sources FROM rag_chat_log")
        source_counts: dict = {}
        for row in cur.fetchall():
            try:
                sources = json.loads(row["retrieved_sources"]) if row["retrieved_sources"] else []
            except Exception:
                sources = []
            for s in sources:
                source_counts[s] = source_counts.get(s, 0) + 1
        cur.close()
        top_sources = sorted(
            ({"source": k, "count": v} for k, v in source_counts.items()),
            key=lambda x: x["count"], reverse=True,
        )[:5]
        return {
            "total_conversations": total,
            "avg_retrieval_ms": round(float(avgs["a"] or 0), 2),
            "avg_llm_ms": round(float(avgs["b"] or 0), 2),
            "avg_total_ms": round(float(avgs["c"] or 0), 2),
            "top_sources": top_sources,
        }
    except Exception as exc:
        print(f"[mysql_store] rag_log_stats failed: {exc!r}")
        return None
    finally:
        conn.close()


# ===== FEEDBACK (website contact form) =====
# Replaces the old MongoDB `feedback` collection (Mongo-only, no fallback
# file -- same here).

def feedback_insert(
    name: str, email: str, subject: str, message: str,
    delivery: str = "unknown", delivery_status: str = "pending",
    rating: int | None = None,
) -> bool:
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO feedback (name, email, subject, message, rating, source, delivery, delivery_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (name, email, subject, message, rating, "website_form", delivery, delivery_status),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        print(f"[mysql_store] feedback_insert failed: {exc!r}")
        return False
    finally:
        conn.close()


def feedback_get_all() -> list[dict]:
    """All website feedback submissions (newest first), for the admin
    dashboard's Feedback tab."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, created_at, name, email, subject, message, rating,
                   delivery, delivery_status
            FROM feedback
            ORDER BY created_at DESC
            """
        )
        rows = cur.fetchall()
        cur.close()
        result = []
        for row in rows:
            created_at = row.get("created_at")
            result.append({
                "id": row.get("id"),
                "createdAt": created_at.isoformat(timespec="seconds") if hasattr(created_at, "isoformat") else (created_at or ""),
                "name": row.get("name") or "",
                "email": row.get("email") or "",
                "subject": row.get("subject") or "",
                "message": row.get("message") or "",
                "rating": row.get("rating"),
                "delivery": row.get("delivery") or "",
                "deliveryStatus": row.get("delivery_status") or "",
            })
        return result
    except Exception as exc:
        print(f"[mysql_store] feedback_get_all failed: {exc!r}")
        return []
    finally:
        conn.close()


# ===== DOCTOR ACCOUNTS (self-registered via /doctor-register) =====
# Source of truth for doctor_login()/doctor_register() in app.py --
# doctor_accounts.json remains only as a best-effort local backup, since a
# Render web service's disk doesn't survive deploys/restarts without a paid
# persistent Disk attached.

def doctor_account_create(email: str, password_hash: str) -> bool:
    """Insert one self-registered doctor account. False on failure,
    including a duplicate email (app.py already checks for that before
    calling this -- the UNIQUE primary key is just the race-safe backstop)."""
    email = (email or "").strip().lower()
    if not email:
        return False
    conn = _get_conn()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO doctor_accounts (email, password_hash) VALUES (%s, %s)",
            (email, password_hash),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        print(f"[mysql_store] doctor_account_create failed: {exc!r}")
        return False
    finally:
        conn.close()


def doctor_accounts_get_all() -> list[dict]:
    """Every self-registered doctor account, as the {"email",
    "password_hash", "created_at"} shape app.py's doctor_accounts.json
    always used, so callers don't need to change their access pattern.
    Empty list when MySQL is unavailable or no accounts exist yet."""
    conn = _get_conn()
    if conn is None:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT email, password_hash, created_at FROM doctor_accounts")
        rows = cur.fetchall()
        cur.close()
        result = []
        for row in rows:
            created_at = row.get("created_at")
            result.append({
                "email": row["email"],
                "password_hash": row["password_hash"],
                "created_at": created_at.isoformat(timespec="seconds") if hasattr(created_at, "isoformat") else (created_at or ""),
            })
        return result
    except Exception as exc:
        print(f"[mysql_store] doctor_accounts_get_all failed: {exc!r}")
        return []
    finally:
        conn.close()
