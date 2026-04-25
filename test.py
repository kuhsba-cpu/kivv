import sqlite3
import streamlit as st
from datetime import datetime
from flask import Flask, request
from threading import Thread
import time
from io import BytesIO
import barcode
from barcode.writer import ImageWriter

DB_FILE = "aa.db"

st.set_page_config(page_title="Store Manager", layout="wide")
st.title("Store, Rooms, Places + Barcode Simulation")


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
            time TEXT NOT NULL,
            FOREIGN KEY(store_id) REFERENCES stores(id) ON DELETE CASCADE,
            FOREIGN KEY(place_id) REFERENCES places(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS active_sim (
            store_id INTEGER,
            active_place_id INTEGER
        )
        """
    )
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


def insert_scan(store_id, place_id, barcode):
    conn = get_conn()
    conn.execute(
        "INSERT INTO scans (store_id, place_id, barcode, time) VALUES (?, ?, ?, ?)",
        (store_id, place_id, barcode.strip(), datetime.now().isoformat()),
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
        return {"status": "fail"}, 400

    barcode = barcode.strip()
    conn = get_conn()
    cur = conn.cursor()
    
    active = cur.execute("SELECT store_id, active_place_id FROM active_sim LIMIT 1").fetchone()
    
    if not active:
        conn.close()
        return {"status": "rejected", "message": "Simulation OFF"}, 403
        
    store_id = active["store_id"]
    active_place_id = active["active_place_id"]

    # 1. Check if the scanned barcode belongs to a place in this store
    place_check = cur.execute(
        """
        SELECT p.id 
        FROM places p 
        JOIN rooms r ON p.room_id = r.id 
        WHERE p.unique_code = ? AND r.store_id = ?
        """, 
        (barcode, store_id)
    ).fetchone()

    if place_check:
        place_id = place_check["id"]
        # If it's already the active place, close it
        if active_place_id == place_id:
            cur.execute("UPDATE active_sim SET active_place_id = NULL")
        else:
            # Open the new place
            cur.execute("UPDATE active_sim SET active_place_id = ?", (place_id,))
        
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Place toggled"}, 200

    # 2. It's a regular item scan. Check if a place is open.
    if active_place_id:
        insert_scan(store_id, active_place_id, barcode)
        conn.close()
        return {"status": "success"}, 200
    else:
        conn.close()
        return {"status": "rejected", "message": "Scan a place barcode first"}, 403


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


# --- UI ---
menu = st.sidebar.radio("Navigation", ["Manage stores", "Barcode simulation"])

stores = get_all_stores()
store_options = {0: "New store"}
for store in stores:
    store_options[store["id"]] = f"{store['id']} - {store['name']} ({store['location']})"

if menu == "Manage stores":
    st.header("Store manager")
    st.write("Only create or remove stores here. Configure rooms and places in the Barcode simulation tab.")

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

elif menu == "Barcode simulation":
    st.header("Barcode simulation")

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
            index=list(structure_options.keys()).index(st.session_state.structure_store_id)
            if st.session_state.structure_store_id in structure_options
            else 0,
            key="structure_store_id",
        )

        if selected_store_id != st.session_state.get("structure_store_id", 0):
            if selected_store_id == 0:
                clear_structure_state()
            else:
                load_structure_form(selected_store_id)

        if selected_store_id == 0:
            st.warning("Select a store first to manage rooms, places, and simulation.")
        else:
            store = get_store(selected_store_id)
            st.subheader(f"Store: {store['name']} — {store['location']}")

            with st.expander("Manage rooms and places", expanded=True):
                rooms_count = st.number_input(
                    "Number of rooms",
                    min_value=1,
                    value=st.session_state.get("structure_rooms_count", 1),
                    step=1,
                    key="structure_rooms_count",
                )

                control_cols = st.columns(2)
                if control_cols[0].button("Add room"):
                    st.session_state.structure_rooms_count = rooms_count + 1
                    st.rerun()
                if rooms_count > 1 and control_cols[1].button("Remove last room"):
                    st.session_state.structure_rooms_count = rooms_count - 1
                    st.rerun()

                for room_index in range(rooms_count):
                    with st.expander(f"Room {room_index + 1}", expanded=True):
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

                        place_controls = st.columns(2)
                        if place_controls[0].button("Add place", key=f"add_place_{room_index}"):
                            st.session_state[f"place_count_{room_index}"] = place_count + 1
                            st.rerun()
                        if place_count > 1 and place_controls[1].button("Remove last place", key=f"remove_place_{room_index}"):
                            st.session_state[f"place_count_{room_index}"] = place_count - 1
                            st.rerun()

                        for place_index in range(place_count):
                            st.markdown(
                                f"**Place {place_index + 1} in {st.session_state.get(f'room_name_{room_index}', f'Room {room_index + 1}')}'**"
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
                        except sqlite3.IntegrityError as exc:
                            st.error("A place code must be unique. Please adjust the duplicate code and save again.")

            st.markdown("---")
            st.subheader("Saved structure")
            saved_rooms = get_store_structure(selected_store_id)
            if not saved_rooms:
                st.info("No rooms and places saved yet. Add them above and save the structure.")
            else:
                for room_index, room in enumerate(saved_rooms, start=1):
                    st.write(f"**Room {room_index}: {room['name']}**")
                    for place_index, place in enumerate(room["places"], start=1):
                        cols = st.columns([6, 2])
                        cols[0].write(f"Place {place_index}: {place['name']} — expected {place['item_count']}")
                        cols[0].write(f"Code: {place['unique_code']}")
                        cols[1].image(get_barcode_image(place["unique_code"]), width=220)
                    st.markdown("---")

            conn = get_conn()
            active_sim = conn.execute("SELECT store_id, active_place_id FROM active_sim LIMIT 1").fetchone()
            conn.close()

            if active_sim:
                active_store_id = active_sim["store_id"]
                active_place_id = active_sim["active_place_id"]
                active_store = get_store(active_store_id)
                open_place = get_place_by_id(active_place_id)

                if active_store_id != selected_store_id:
                    st.warning(f"Simulation is currently active for {active_store['name']}. Stop it before starting a new one.")

                st.success(f"🟢 SIMULATION RUNNING FOR: {active_store['name']}")
                if open_place:
                    st.info(f"📂 PLACE OPEN: **{open_place['name']}** (Code: {open_place['unique_code']}). Scan items to save them here. Scan place code again to close.")
                else:
                    st.warning("⏳ WAITING: Scan a place's unique barcode to open it.")

                st.subheader("Live Scans")
                scans = get_scans(active_store_id)
                if not scans:
                    st.write("No items scanned yet.")
                else:
                    for scan in scans:
                        cols = st.columns([6, 3, 2])
                        cols[0].write(f"**{scan['barcode']}**")
                        cols[1].write(f"📍 {scan['place_name']}")
                        if cols[2].button("Delete", key=f"delete_scan_{scan['id']}"):
                            delete_scan(scan['id'])
                            st.rerun()

                st.markdown("---")
                if st.button("🛑 Stop Simulation", type="primary"):
                    summary = compare_place_counts(active_store_id)
                    conn = get_conn()
                    conn.execute("DELETE FROM active_sim")
                    conn.commit()
                    conn.close()
                    st.session_state.simulation_stop_summary = summary
                    st.session_state.simulation_stop_store_id = active_store_id
                    st.rerun()

                time.sleep(2)
                st.rerun()
            else:
                st.warning("🔴 No simulation running. Start one below to receive scans.")
                if st.button("🚀 Start Simulation", type="primary"):
                    conn = get_conn()
                    conn.execute("DELETE FROM active_sim")
                    conn.execute("INSERT INTO active_sim (store_id, active_place_id) VALUES (?, NULL)", (selected_store_id,))
                    conn.commit()
                    conn.close()
                    st.rerun()

                if "simulation_stop_summary" in st.session_state:
                    st.markdown("---")
                    st.subheader("Simulation result check")
                    store = get_store(st.session_state.simulation_stop_store_id)
                    if store:
                        st.write(f"Store: **{store['name']}** ({store['location']})")
                    summary = st.session_state.simulation_stop_summary
                    for item in summary:
                        if item["difference"] == 0:
                            st.success(
                                f"{item['room_name']} / {item['place_name']}: expected {item['expected']}, scanned {item['scanned']} — OK"
                            )
                        else:
                            st.error(
                                f"{item['room_name']} / {item['place_name']}: expected {item['expected']}, scanned {item['scanned']} — difference {item['difference']}"
                            )
                    del st.session_state.simulation_stop_summary
                    del st.session_state.simulation_stop_store_id

                st.markdown("---")
                st.subheader("Place scan difference")
                place_differences = compare_place_counts(selected_store_id)
                if not place_differences:
                    st.info("No configured places to compare yet.")
                else:
                    for item in place_differences:
                        if item["difference"] == 0:
                            st.success(
                                f"{item['room_name']} / {item['place_name']}: expected {item['expected']}, scanned {item['scanned']} — OK"
                            )
                        else:
                            status = "over" if item["difference"] > 0 else "under"
                            st.error(
                                f"{item['room_name']} / {item['place_name']}: expected {item['expected']}, scanned {item['scanned']} — {abs(item['difference'])} {status}"
                            )

                st.markdown("---")
                st.subheader("Past Scans")
                scans = get_scans(selected_store_id)
                if not scans:
                    st.info("No barcodes scanned for this store yet.")
                else:
                    for scan in scans:
                        st.write(f"**{scan['barcode']}** in 📍 {scan['place_name']} — {scan['time']}")

                st.markdown("---")
                st.write("Database file:")
                st.code(DB_FILE)
