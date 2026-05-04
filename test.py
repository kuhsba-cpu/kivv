import sqlite3
import streamlit as st
from datetime import datetime
from flask import Flask, request
from threading import Thread
import time
from io import BytesIO
import pandas as pd
import barcode
from barcode.writer import ImageWriter

DB_FILE = "aa.db"

st.set_page_config(page_title="Store Manager", layout="wide")
st.markdown(
    """
    <style>
    .stApp { background: #050812; color: #e2e8f0; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    .stSidebar .sidebar-content { background: #0b1221; border-right: 1px solid rgba(148,163,184,.12); }
    .page-header { padding: 28px 32px; border-radius: 28px; background: linear-gradient(135deg, #111827 0%, #17233a 100%); box-shadow: 0 24px 80px rgba(15,23,42,.35); margin-bottom: 28px; }
    .page-header h1 { margin: 0; font-size: 48px; letter-spacing: -0.04em; color: #ffffff; }
    .page-header p { margin: 12px 0 0; color: #94a3b8; font-size: 17px; line-height: 1.6; max-width: 760px; }
    .card { background: #0f172a; border: 1px solid rgba(148,163,184,.12); border-radius: 24px; padding: 24px; box-shadow: 0 20px 60px rgba(15,23,42,.18); }
    .card-title { margin: 0; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.12em; font-size: 12px; }
    .card-value { margin: 10px 0 0; color: #ffffff; font-size: 34px; font-weight: 800; }
    .card-note { margin: 14px 0 0; color: #94a3b8; line-height: 1.65; font-size: 14px; }
    .button-block { padding: 20px 22px; border-radius: 20px; background: #111827; border: 1px solid rgba(148,163,184,.08); }
    .button-label { margin: 0 0 12px; font-size: 14px; color: #94a3b8; }
    .section-title { margin-bottom: 16px; color: #f8fafc; font-size: 18px; font-weight: 700; }
    .detail-box { background: #0b1221; border: 1px solid rgba(148,163,184,.08); border-radius: 20px; padding: 18px; }
    .stButton>button { background: #2563eb; color: #ffffff; border: none; border-radius: 12px; padding: 12px 18px; font-weight: 700; box-shadow: 0 12px 30px rgba(37,99,235,.2); }
    .stButton>button:hover { background: #1d4ed8; }
    .stTextInput>div>div>input, .stNumberInput>div>div>input, .stSelectbox>div>div>div { background: #0b1221; color: #e2e8f0; border: 1px solid #334155; }
    .stTextInput>div>div>input::placeholder, .stSelectbox>div>div>div::placeholder { color: #94a3b8; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown(
    """
    <div class="page-header">
      <h1>Store Manager Dashboard</h1>
      <p>Manage stores, scanning workflows, and inventory simulation from one refined control center.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# --- Database helpers ---
def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stores (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY,
            store_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY(store_id) REFERENCES stores(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS places (
            id INTEGER PRIMARY KEY,
            room_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            unique_code TEXT NOT NULL UNIQUE,
            FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY,
            store_id INTEGER NOT NULL,
            place_id INTEGER,
            barcode TEXT NOT NULL,
            device_id TEXT,
            time TEXT NOT NULL,
            FOREIGN KEY(store_id) REFERENCES stores(id) ON DELETE CASCADE,
            FOREIGN KEY(place_id) REFERENCES places(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS active_sim (
            device_id TEXT,
            store_id INTEGER,
            active_place_id INTEGER
        )
        """
    )
    conn.commit()
    conn.close()
    ensure_db_columns()


def ensure_db_columns():
    conn = get_conn()
    active_sim_columns = [row["name"] for row in conn.execute("PRAGMA table_info(active_sim)").fetchall()]
    if "device_id" not in active_sim_columns:
        conn.execute("ALTER TABLE active_sim ADD COLUMN device_id TEXT")
        conn.execute("UPDATE active_sim SET device_id = 'global' WHERE device_id IS NULL")

    scan_columns = [row["name"] for row in conn.execute("PRAGMA table_info(scans)").fetchall()]
    if "device_id" not in scan_columns:
        conn.execute("ALTER TABLE scans ADD COLUMN device_id TEXT")

    conn.commit()
    conn.close()


def get_all_stores():
    conn = get_conn()
    stores = conn.execute("SELECT * FROM stores ORDER BY id").fetchall()
    conn.close()
    return stores


def get_store(store_id):
    conn = get_conn()
    store = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
    conn.close()
    return store


def get_store_structure(store_id):
    conn = get_conn()
    rooms = []
    room_rows = conn.execute("SELECT * FROM rooms WHERE store_id = ? ORDER BY id", (store_id,)).fetchall()
    for room in room_rows:
        places = conn.execute(
            "SELECT * FROM places WHERE room_id = ? ORDER BY id",
            (room["id"],),
        ).fetchall()
        rooms.append(
            {
                "id": room["id"],
                "name": room["name"],
                "places": [
                    {
                        "id": place["id"],
                        "name": place["name"],
                        "item_count": place["item_count"],
                        "unique_code": place["unique_code"]
                    }
                    for place in places
                ],
            }
        )
    conn.close()
    return rooms


def insert_store(name, location):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO stores (name, location, created_at) VALUES (?, ?, ?)",
        (name, location, datetime.now().isoformat()),
    )
    store_id = cur.lastrowid
    conn.commit()
    conn.close()
    return store_id


def update_store(store_id, name, location):
    conn = get_conn()
    conn.execute(
        "UPDATE stores SET name = ?, location = ? WHERE id = ?",
        (name, location, store_id),
    )
    conn.commit()
    conn.close()


def delete_store(store_id):
    conn = get_conn()
    conn.execute("DELETE FROM active_sim WHERE store_id = ?", (store_id,))
    conn.execute("DELETE FROM scans WHERE store_id = ?", (store_id,))
    room_rows = conn.execute("SELECT id FROM rooms WHERE store_id = ?", (store_id,)).fetchall()
    for room in room_rows:
        conn.execute("DELETE FROM places WHERE room_id = ?", (room["id"],))
    conn.execute("DELETE FROM rooms WHERE store_id = ?", (store_id,))
    conn.execute("DELETE FROM stores WHERE id = ?", (store_id,))
    conn.commit()
    conn.close()


def clear_structure_state():
    keys = [
        key
        for key in st.session_state.keys()
        if key.startswith(
            (
                "structure_",
                "room_id_",
                "room_name_",
                "place_count_",
                "place_id_",
                "place_name_",
                "item_count_",
                "place_code_",
            )
        )
    ]
    for key in keys:
        if key == "structure_selected_store_id":
            continue
        del st.session_state[key]


def clear_store_form_state():
    st.session_state.store_form_store_id = 0
    for key in ["store_form_name", "store_form_location"]:
        if key in st.session_state:
            del st.session_state[key]


def load_store_form(store_id):
    store = get_store(store_id)
    if not store:
        clear_store_form_state()
        return

    clear_store_form_state()
    st.session_state.store_form_store_id = store_id
    st.session_state.store_form_name = store["name"]
    st.session_state.store_form_location = store["location"]


def load_structure_form(store_id):
    clear_structure_state()
    st.session_state.structure_store_id = store_id
    rooms = get_store_structure(store_id)
    st.session_state.structure_rooms_count = max(1, len(rooms))

    for room_index, room in enumerate(rooms):
        st.session_state[f"room_id_{room_index}"] = room["id"]
        st.session_state[f"room_name_{room_index}"] = room["name"]
        place_count = max(1, len(room["places"]))
        st.session_state[f"place_count_{room_index}"] = place_count
        for place_index, place in enumerate(room["places"]):
            st.session_state[f"place_id_{room_index}_{place_index}"] = place["id"]
            st.session_state[f"place_name_{room_index}_{place_index}"] = place["name"]
            st.session_state[f"item_count_{room_index}_{place_index}"] = place["item_count"]
            st.session_state[f"place_code_{room_index}_{place_index}"] = place["unique_code"]


def validate_structure_inputs(rooms_count):
    seen_codes = set()
    for room_index in range(rooms_count):
        room_name = st.session_state.get(f"room_name_{room_index}", "").strip()
        if not room_name:
            return f"Room {room_index + 1} needs a name."

        place_count = st.session_state.get(f"place_count_{room_index}", 1)
        for place_index in range(place_count):
            place_name = st.session_state.get(f"place_name_{room_index}_{place_index}", "").strip()
            place_code = st.session_state.get(f"place_code_{room_index}_{place_index}", "").strip()

            if not place_name:
                return f"Place {place_index + 1} in Room {room_index + 1} needs a name."
            if not place_code:
                return f"Place {place_index + 1} in Room {room_index + 1} needs a unique code."
            if place_code in seen_codes:
                return f"Duplicate place code '{place_code}' found. Each place must have a unique code."
            seen_codes.add(place_code)
    return None


def save_store_structure(store_id, rooms_count):
    conn = get_conn()
    cur = conn.cursor()

    active_room_ids = []
    active_place_ids = []

    for room_index in range(rooms_count):
        room_name = st.session_state.get(f"room_name_{room_index}", f"Room {room_index + 1}").strip() or f"Room {room_index + 1}"
        room_id = st.session_state.get(f"room_id_{room_index}")

        if room_id:
            cur.execute(
                "UPDATE rooms SET name = ? WHERE id = ? AND store_id = ?",
                (room_name, room_id, store_id),
            )
        else:
            cur.execute(
                "INSERT INTO rooms (store_id, name) VALUES (?, ?)",
                (store_id, room_name),
            )
            room_id = cur.lastrowid
            st.session_state[f"room_id_{room_index}"] = room_id

        active_room_ids.append(room_id)

        place_count = st.session_state.get(f"place_count_{room_index}", 1)
        for place_index in range(place_count):
            place_name = st.session_state.get(
                f"place_name_{room_index}_{place_index}", f"Place {place_index + 1}"
            ).strip() or f"Place {place_index + 1}"
            item_count = int(st.session_state.get(f"item_count_{room_index}_{place_index}", 0))
            place_code = st.session_state.get(f"place_code_{room_index}_{place_index}", "").strip()
            place_id = st.session_state.get(f"place_id_{room_index}_{place_index}")

            if place_id:
                cur.execute(
                    "UPDATE places SET name = ?, item_count = ?, unique_code = ? WHERE id = ? AND room_id = ?",
                    (place_name, item_count, place_code, place_id, room_id),
                )
            else:
                cur.execute(
                    "INSERT INTO places (room_id, name, item_count, unique_code) VALUES (?, ?, ?, ?)",
                    (room_id, place_name, item_count, place_code),
                )
                place_id = cur.lastrowid
                st.session_state[f"place_id_{room_index}_{place_index}"] = place_id

            active_place_ids.append(place_id)

    if active_place_ids:
        placeholders = ",".join("?" for _ in active_place_ids)
        cur.execute(
            f"DELETE FROM places WHERE room_id IN (SELECT id FROM rooms WHERE store_id = ?) AND id NOT IN ({placeholders})",
            (store_id, *active_place_ids),
        )
    else:
        cur.execute(
            "DELETE FROM places WHERE room_id IN (SELECT id FROM rooms WHERE store_id = ?)",
            (store_id,),
        )

    if active_room_ids:
        placeholders = ",".join("?" for _ in active_room_ids)
        cur.execute(
            f"DELETE FROM rooms WHERE store_id = ? AND id NOT IN ({placeholders})",
            (store_id, *active_room_ids),
        )
    else:
        cur.execute("DELETE FROM rooms WHERE store_id = ?", (store_id,))

    conn.commit()
    conn.close()


def insert_scan(store_id, place_id, barcode, device_id=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO scans (store_id, place_id, barcode, device_id, time) VALUES (?, ?, ?, ?, ?)",
        (store_id, place_id, barcode.strip(), device_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_scans(store_id):
    conn = get_conn()
    scans = conn.execute(
        """
        SELECT scans.*, places.name as place_name, places.unique_code 
        FROM scans 
        LEFT JOIN places ON scans.place_id = places.id 
        WHERE scans.store_id = ? 
        ORDER BY scans.id DESC
        """,
        (store_id,),
    ).fetchall()
    conn.close()
    return scans


def get_scan_counts_by_place(store_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT place_id, COUNT(*) AS scanned_count FROM scans WHERE store_id = ? GROUP BY place_id",
        (store_id,),
    ).fetchall()
    conn.close()
    return {row["place_id"]: row["scanned_count"] for row in rows}


def compare_place_counts(store_id):
    rooms = get_store_structure(store_id)
    scanned_counts = get_scan_counts_by_place(store_id)
    result = []
    for room in rooms:
        for place in room["places"]:
            scanned = scanned_counts.get(place["id"], 0)
            difference = scanned - place["item_count"]
            result.append(
                {
                    "room_name": room["name"],
                    "place_name": place["name"],
                    "expected": place["item_count"],
                    "scanned": scanned,
                    "difference": difference,
                }
            )
    return result


def delete_scan(scan_id):
    conn = get_conn()
    conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
    conn.commit()
    conn.close()


def get_place_by_id(place_id):
    if not place_id:
        return None
    conn = get_conn()
    place = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
    conn.close()
    return place


def get_active_sessions(store_id=None):
    conn = get_conn()
    query = "SELECT device_id, store_id, active_place_id FROM active_sim"
    params = ()
    if store_id is not None:
        query += " WHERE store_id = ? AND device_id != 'global'"
        params = (store_id,)
    sessions = conn.execute(query, params).fetchall()
    conn.close()
    return sessions


def stop_store_sessions(store_id):
    conn = get_conn()
    conn.execute("DELETE FROM active_sim WHERE store_id = ?", (store_id,))
    conn.commit()
    conn.close()


def start_store_simulation(store_id):
    conn = get_conn()
    cur = conn.cursor()
    existing = cur.execute(
        "SELECT device_id FROM active_sim WHERE device_id = 'global'",
    ).fetchone()
    if existing:
        cur.execute(
            "UPDATE active_sim SET store_id = ?, active_place_id = NULL WHERE device_id = 'global'",
            (store_id,),
        )
    else:
        cur.execute(
            "INSERT INTO active_sim (device_id, store_id, active_place_id) VALUES ('global', ?, NULL)",
            (store_id,),
        )
    conn.commit()
    conn.close()


def is_store_simulation_active(store_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM active_sim WHERE device_id = 'global' AND store_id = ?",
        (store_id,),
    ).fetchone()
    conn.close()
    return bool(row)


def get_recent_scans_for_store(store_id, limit=20):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT scans.barcode, scans.time, scans.device_id, places.name as place_name, rooms.name as room_name
        FROM scans
        LEFT JOIN places ON scans.place_id = places.id
        LEFT JOIN rooms ON places.room_id = rooms.id
        WHERE scans.store_id = ?
        ORDER BY scans.id DESC
        LIMIT ?
        """,
        (store_id, limit),
    ).fetchall()
    conn.close()
    return rows


def get_barcode_image(code):
    barcode_class = barcode.get_barcode_class('code128')
    bar = barcode_class(code, writer=ImageWriter())
    buffer = BytesIO()
    bar.write(buffer, options={
        'module_width': 0.3,
        'module_height': 30,
        'quiet_zone': 1.0,
        'font_size': 10,
        'text_distance': 1,
        'write_text': False,
    })
    buffer.seek(0)
    return buffer


# --- Flask Backend ---
app = Flask(__name__)

@app.route('/scan', methods=['POST'])
def receive_scan():
    data = request.json
    barcode = data.get('content') if data else None

    if not barcode:
        return {"status": "fail", "message": "Missing barcode."}, 400

    barcode = barcode.strip()
    device_id = None
    if data:
        device_id = data.get('device_id') or data.get('deviceName') or data.get('device_name')
    if not device_id:
        device_id = request.headers.get('X-Device-ID') or request.headers.get('Device-ID')

    if not device_id:
        addr = request.headers.get('X-Forwarded-For') or request.remote_addr or ''
        ua = request.headers.get('User-Agent') or ''
        device_id = f"{addr}|{ua[:80]}" if addr or ua else 'unknown-device'

    device_id = str(device_id).strip()
    if not device_id:
        device_id = 'unknown-device'

    conn = get_conn()
    cur = conn.cursor()

    place_check = cur.execute(
        """
        SELECT p.id, r.store_id
        FROM places p
        JOIN rooms r ON p.room_id = r.id
        WHERE p.unique_code = ?
        """,
        (barcode,),
    ).fetchone()

    if place_check:
        place_id = place_check["id"]
        store_id = place_check["store_id"]
        simulation_active = cur.execute(
            "SELECT 1 FROM active_sim WHERE device_id = 'global' AND store_id = ?",
            (store_id,),
        ).fetchone()
        if not simulation_active:
            conn.close()
            return {"status": "rejected", "message": "Simulation not started for this store."}, 403

        active = cur.execute(
            "SELECT store_id, active_place_id FROM active_sim WHERE device_id = ?",
            (device_id,),
        ).fetchone()

        if active:
            if active["store_id"] != store_id:
                cur.execute(
                    "UPDATE active_sim SET store_id = ?, active_place_id = ? WHERE device_id = ?",
                    (store_id, place_id, device_id),
                )
            elif active["active_place_id"] == place_id:
                cur.execute(
                    "UPDATE active_sim SET active_place_id = NULL WHERE device_id = ?",
                    (device_id,),
                )
            else:
                cur.execute(
                    "UPDATE active_sim SET active_place_id = ? WHERE device_id = ?",
                    (place_id, device_id),
                )
        else:
            cur.execute(
                "INSERT INTO active_sim (device_id, store_id, active_place_id) VALUES (?, ?, ?)",
                (device_id, store_id, place_id),
            )

        conn.commit()
        conn.close()
        return {"status": "success", "message": "Place toggled", "place_id": place_id}, 200

    active = cur.execute(
        "SELECT store_id, active_place_id FROM active_sim WHERE device_id = ?",
        (device_id,),
    ).fetchone()

    if active and active["active_place_id"]:
        insert_scan(active["store_id"], active["active_place_id"], barcode, device_id)
        conn.close()
        return {"status": "success"}, 200

    conn.close()
    return {"status": "rejected", "message": "Scan a place barcode first."}, 403


def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if "flask_started" not in st.session_state:
    thread = Thread(target=run_flask, daemon=True)
    thread.start()
    st.session_state.flask_started = True


# --- Initialization ---
init_db()

if "selected_store_id" not in st.session_state:
    st.session_state.selected_store_id = 0

if "store_form_store_id" not in st.session_state:
    clear_store_form_state()

if "structure_store_id" not in st.session_state:
    st.session_state.structure_store_id = 0
if "structure_selected_store_id" not in st.session_state:
    st.session_state.structure_selected_store_id = 0


def get_active_store_name():
    conn = get_conn()
    row = conn.execute("SELECT store_id FROM active_sim LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    store = get_store(row["store_id"])
    return store["name"] if store else None


def get_dashboard_summary():
    conn = get_conn()
    total_stores = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
    total_places = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    total_scans = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
    scan_by_store = pd.read_sql_query(
        "SELECT stores.name AS store_name, COUNT(scans.id) AS scan_count "
        "FROM stores LEFT JOIN scans ON stores.id = scans.store_id "
        "GROUP BY stores.id ORDER BY scan_count DESC",
        conn,
    )
    scan_over_time = pd.read_sql_query(
        "SELECT DATE(time) AS scan_date, COUNT(*) AS scan_count "
        "FROM scans GROUP BY DATE(time) ORDER BY scan_date",
        conn,
    )
    conn.close()
    return total_stores, total_places, total_scans, scan_by_store, scan_over_time


def render_home_page():
    total_stores, total_places, total_scans, scan_by_store, scan_over_time = get_dashboard_summary()
    active_store = get_active_store_name()

    with st.container():
        st.markdown('<p class="section-title">Overview</p>', unsafe_allow_html=True)
        cols = st.columns(3, gap="large")
        cols[0].markdown(
            f'<div class="card"><p class="card-title">Stores</p><p class="card-value">{total_stores}</p><p class="card-note">Total registered stores tracked in the system.</p></div>',
            unsafe_allow_html=True,
        )
        cols[1].markdown(
            f'<div class="card"><p class="card-title">Places</p><p class="card-value">{total_places}</p><p class="card-note">Configured rooms and places ready for barcode simulation.</p></div>',
            unsafe_allow_html=True,
        )
        cols[2].markdown(
            f'<div class="card"><p class="card-title">Scans</p><p class="card-value">{total_scans}</p><p class="card-note">Total barcode scans collected across all stores.</p></div>',
            unsafe_allow_html=True,
        )

    with st.container():
        st.markdown('<p class="section-title">Live analytics</p>', unsafe_allow_html=True)
        chart_col1, chart_col2 = st.columns(2, gap="large")
        with chart_col1:
            st.markdown('<div class="card"><p class="card-title">Scans by store</p>', unsafe_allow_html=True)
            if not scan_by_store.empty:
                chart_data = scan_by_store.rename(columns={"store_name": "Store", "scan_count": "Scans"}).set_index("Store")
                st.bar_chart(chart_data)
            else:
                st.info("No scan data available yet.")
            st.markdown('</div>', unsafe_allow_html=True)

        with chart_col2:
            st.markdown('<div class="card"><p class="card-title">Scan volume over time</p>', unsafe_allow_html=True)
            if not scan_over_time.empty:
                time_data = scan_over_time.rename(columns={"scan_date": "Date", "scan_count": "Scans"}).set_index("Date")
                st.line_chart(time_data)
            else:
                st.info("No historical scan data found.")
            st.markdown('</div>', unsafe_allow_html=True)

    with st.container():
        st.markdown('<p class="section-title">Quick actions</p>', unsafe_allow_html=True)
        action_col1, action_col2 = st.columns(2, gap="large")
        action_col1.markdown(
            '<div class="button-block"><p class="button-label">Create or update store records.</p></div>',
            unsafe_allow_html=True,
        )
        action_col2.markdown(
            '<div class="button-block"><p class="button-label">Prepare rooms, places and start scanning.</p></div>',
            unsafe_allow_html=True,
        )
        if action_col1.button("Manage stores"):
            st.session_state.current_page = "Manage stores"
            st.rerun()
        if action_col2.button("Barcode simulation"):
            st.session_state.current_page = "Barcode simulation"
            st.rerun()

    if active_store:
        st.markdown('<div class="card"><p class="card-title">Active simulation</p><p class="card-value">Running</p><p class="card-note">Current active store: ' + active_store + '</p></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="card"><p class="card-title">Active simulation</p><p class="card-value">Stopped</p><p class="card-note">No store is currently receiving scans.</p></div>', unsafe_allow_html=True)


# --- UI ---
if "current_page" not in st.session_state:
    st.session_state.current_page = "Home"

page = st.sidebar.radio("Navigation", ["Home", "Manage stores", "Barcode simulation"], index=["Home", "Manage stores", "Barcode simulation"].index(st.session_state.current_page))
st.session_state.current_page = page

stores = get_all_stores()
store_options = {0: "New store"}
for store in stores:
    store_options[store["id"]] = f"{store['id']} - {store['name']} ({store['location']})"

if page == "Home":
    render_home_page()

elif page == "Manage stores":
    st.markdown('<div class="card"><p class="card-title">Store manager</p><h2 style="margin:0;color:#ffffff;">Create and manage stores</h2><p class="card-note">Only create or remove stores here. Configure rooms and places in the Barcode simulation tab.</p></div>', unsafe_allow_html=True)

    selected_store_id = st.selectbox(
        "Select store to edit",
        options=list(store_options.keys()),
        format_func=lambda x: store_options[x],
        index=list(store_options.keys()).index(st.session_state.selected_store_id)
        if st.session_state.selected_store_id in store_options
        else 0,
        key="selected_store_id",
    )

    if selected_store_id != st.session_state.store_form_store_id:
        if selected_store_id == 0:
            clear_store_form_state()
        else:
            load_store_form(selected_store_id)

    st.subheader("Store details")
    store_name = st.text_input(
        "Store name",
        value=st.session_state.get("store_form_name", ""),
        key="store_form_name",
    )
    store_location = st.text_input(
        "Store location",
        value=st.session_state.get("store_form_location", ""),
        key="store_form_location",
    )

    submitted = st.button("Save store")

    if submitted:
        if not store_name.strip() or not store_location.strip():
            st.warning("Store name and location are required.")
        else:
            if st.session_state.store_form_store_id > 0:
                update_store(st.session_state.store_form_store_id, store_name.strip(), store_location.strip())
                st.success("Store updated successfully.")
            else:
                new_id = insert_store(store_name.strip(), store_location.strip())
                st.success("Store added successfully.")
            clear_store_form_state()
            st.rerun()

    if st.session_state.store_form_store_id > 0:
        if st.button("Delete this store", type="secondary"):
            delete_store(st.session_state.store_form_store_id)
            st.success("Store deleted.")
            clear_store_form_state()
            st.rerun()

    st.markdown("---")
    st.subheader("Saved stores")

    if not stores:
        st.info("No stores created yet.")
    else:
        for store in stores:
            st.write(f"**{store['id']} - {store['name']}**")
            st.write(f"Location: {store['location']}")
            st.write("Configure rooms and places in the Barcode simulation page.")
            st.markdown("---")

elif page == "Barcode simulation":
    st.markdown(
        '<div class="card"><p class="card-title">Barcode simulation</p><h2 style="margin:0;color:#ffffff;">Scan and verify inventory</h2><p class="card-note">Select a store, configure rooms and places, then scan from any mobile device. Each mobile session is tracked separately to avoid interference.</p></div>',
        unsafe_allow_html=True,
    )

    if not stores:
        st.info("Create a store first in the Manage stores tab.")
    else:
        structure_options = {0: "Select store to configure"}
        for store in stores:
            structure_options[store["id"]] = f"{store['id']} - {store['name']} ({store['location']})"

        selected_store_id = st.selectbox(
            "Select store to configure and scan",
            options=list(structure_options.keys()),
            format_func=lambda x: structure_options[x],
            index=list(structure_options.keys()).index(st.session_state.structure_selected_store_id)
            if st.session_state.structure_selected_store_id in structure_options
            else 0,
            key="structure_selected_store_id",
        )

        if selected_store_id != st.session_state.get("structure_store_id", 0):
            if selected_store_id == 0:
                clear_structure_state()
                st.session_state.structure_store_id = 0
            else:
                load_structure_form(selected_store_id)
                st.session_state.structure_store_id = selected_store_id

        elif selected_store_id != 0 and "structure_rooms_count" not in st.session_state:
            load_structure_form(selected_store_id)
            st.session_state.structure_store_id = selected_store_id

        if selected_store_id == 0:
            st.warning("Select a store first to manage rooms, places, and scan sessions.")
        else:
            store = get_store(selected_store_id)
            active_sessions = get_active_sessions(selected_store_id)

            top_left, top_right = st.columns([3, 1], gap="large")
            with top_left:
                st.subheader(f"Store: {store['name']} — {store['location']}")
                st.markdown(
                    '<div class="card"><p class="card-title">Multi-device scanning</p><p class="card-note">Each mobile keeps its own active place context. Scan a place barcode first on the phone, then scan item barcodes. Sessions do not interfere across devices.</p></div>',
                    unsafe_allow_html=True,
                )

            with top_right:
                st.markdown('<div class="card"><p class="card-title">Active scanners</p>', unsafe_allow_html=True)
                if active_sessions:
                    for session in active_sessions:
                        place = get_place_by_id(session["active_place_id"])
                        place_name = place["name"] if place else "Waiting for place barcode"
                        place_code = place["unique_code"] if place else ""
                        st.markdown(
                            f"<div style='padding:12px; border:1px solid #334155; border-radius:14px; margin-bottom:12px; background:#0f172a; color:#f8fafc;'>"
                            f"<strong>Device:</strong> {session['device_id']}<br/>"
                            f"<strong>Active place:</strong> {place_name}<br/>"
                            f"<strong>Code:</strong> <code style='background:#111827; color:#e2e8f0; padding:2px 4px; border-radius:4px;'>{place_code}</code>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                else:
                    st.info("No active scanner sessions for this store yet.")

                simulation_active = is_store_simulation_active(selected_store_id)
                if simulation_active:
                    st.success("Simulation is active for this store. Mobile devices can now scan place barcodes.")
                else:
                    st.warning("Simulation is not started. Use the button below to start scanning.")

                if st.button("Start stimulation for this store"):
                    start_store_simulation(selected_store_id)
                    st.success("Stimulation started for the selected store. Scan place barcodes from any device now.")
                    st.rerun()

                if st.button("Stop stimulation for this store", type="secondary"):
                    stop_store_sessions(selected_store_id)
                    st.success("All active scan sessions were stopped.")
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

            st.markdown("---")
            main_col, side_col = st.columns([2, 1], gap="large")

            with main_col:
                with st.expander("Configure rooms & places", expanded=True):
                    rooms_count = st.number_input(
                        "Number of rooms",
                        min_value=1,
                        value=st.session_state.get("structure_rooms_count", 1),
                        step=1,
                        key="structure_rooms_count",
                    )

                    st.markdown(
                        """
                        <div style='background:#111827; padding:14px; border-radius:16px; margin-bottom:18px; color:#e2e8f0;'>
                        Add rooms and place details here. Each place must have a unique barcode code for scanning.
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    for room_index in range(rooms_count):
                        st.markdown(
                            f"<div style='padding:14px; border:1px solid #334155; border-radius:16px; margin-bottom:18px; background:#0f172a; color:#f8fafc;'>"
                            f"<h4 style='margin:0 0 8px 0; color:#f8fafc;'>Room {room_index + 1}</h4>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        st.text_input(
                            "Room name",
                            value=st.session_state.get(f"room_name_{room_index}", f"Room {room_index + 1}"),
                            key=f"room_name_{room_index}",
                        )

                        place_count = st.number_input(
                            "Number of places",
                            min_value=1,
                            value=st.session_state.get(f"place_count_{room_index}", 1),
                            key=f"place_count_{room_index}",
                            step=1,
                        )

                        for place_index in range(place_count):
                            st.markdown(
                                f"<div style='padding:12px; border:1px solid #334155; border-radius:14px; margin-bottom:14px; background:#0f172a; color:#f8fafc;'>"
                                f"<strong>Place {place_index + 1}</strong>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                            st.text_input(
                                "Place name",
                                value=st.session_state.get(f"place_name_{room_index}_{place_index}", f"Place {place_index + 1}"),
                                key=f"place_name_{room_index}_{place_index}",
                            )
                            st.text_input(
                                "Place barcode code",
                                value=st.session_state.get(f"place_code_{room_index}_{place_index}", ""),
                                key=f"place_code_{room_index}_{place_index}",
                            )
                            st.number_input(
                                "Expected item count",
                                min_value=0,
                                value=st.session_state.get(f"item_count_{room_index}_{place_index}", 0),
                                key=f"item_count_{room_index}_{place_index}",
                                step=1,
                            )

                    if st.button("Save structure", type="primary"):
                        error = validate_structure_inputs(rooms_count)
                        if error:
                            st.error(error)
                        else:
                            try:
                                save_store_structure(selected_store_id, rooms_count)
                                st.success("Rooms and places saved successfully.")
                                load_structure_form(selected_store_id)
                            except sqlite3.IntegrityError:
                                st.error("A place code must be unique. Please adjust the duplicate code and save again.")

                st.markdown("---")
                st.subheader("Place scan difference")
                place_differences = compare_place_counts(selected_store_id)
                if not place_differences:
                    st.info("No configured places to compare yet.")
                else:
                    for item in place_differences:
                        if item['difference'] == 0:
                            st.success(f"{item['room_name']} / {item['place_name']}: {item['scanned']} scanned — OK")
                        else:
                            status = "over" if item['difference'] > 0 else "under"
                            st.error(f"{item['room_name']} / {item['place_name']}: {item['scanned']} scanned — {abs(item['difference'])} {status}")

            with side_col:
                st.subheader("Saved structure")
                saved_rooms = get_store_structure(selected_store_id)
                if not saved_rooms:
                    st.info("No rooms and places saved yet. Add them on the left and save.")
                else:
                    for room_index, room in enumerate(saved_rooms, start=1):
                        st.markdown(
                            f"<div style='padding:16px; border:1px solid #334155; border-radius:16px; background:#0f172a; margin-bottom:16px; color:#f8fafc;'>"
                            f"<h3 style='margin:0 0 10px 0; color:#f8fafc;'>Room {room_index}: {room['name']}</h3>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        for place_index, place in enumerate(room['places'], start=1):
                            place_cols = st.columns([3, 1], gap="small")
                            with place_cols[0]:
                                st.markdown(
                                    f"<div style='padding:14px; border:1px solid #334155; border-radius:14px; background:#0f172a; color:#f8fafc;'>"
                                    f"<strong>Place {place_index}: {place['name']}</strong><br/>"
                                    f"Expected: <strong>{place['item_count']}</strong><br/>"
                                    f"Code: <code style='background:#111827; color:#e2e8f0; padding:2px 4px; border-radius:4px;'>{place['unique_code']}</code>"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )
                            with place_cols[1]:
                                st.image(get_barcode_image(place['unique_code']), width=160)

                st.markdown("---")
                st.subheader("Recent scans")
                recent_scans = get_recent_scans_for_store(selected_store_id)
                if not recent_scans:
                    st.info("No scans recorded yet.")
                else:
                    scan_rows = [
                        {
                            "Time": row["time"],
                            "Device": row["device_id"] or "unknown",
                            "Barcode": row["barcode"],
                            "Place": row["place_name"] or "Unknown",
                            "Room": row["room_name"] or "Unknown",
                        }
                        for row in recent_scans
                    ]
                    st.dataframe(pd.DataFrame(scan_rows), use_container_width=True)

                st.markdown("---")
                st.subheader("Device sessions")
                if active_sessions:
                    session_rows = []
                    for session in active_sessions:
                        place = get_place_by_id(session["active_place_id"])
                        session_rows.append(
                            {
                                "Device": session["device_id"],
                                "Place": place["name"] if place else "Waiting",
                                "Code": place["unique_code"] if place else "",
                                "Status": "Open" if place else "Waiting",
                            }
                        )
                    st.table(pd.DataFrame(session_rows))
                else:
                    st.info("No active device sessions for this store.")

                st.markdown("---")
                st.write("Database file:")
                st.code(DB_FILE)
