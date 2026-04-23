import streamlit as st
from flask import Flask, request
import sqlite3
from threading import Thread
import pandas as pd
import time
import os

# Make sure a folder exists for the uploaded images
if not os.path.exists("logos"):
    os.makedirs("logos")

DB_NAME = "kuhs.db"

# --- PART 1: RELATIONAL DATABASE ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    # Table for Stores
    conn.execute('''CREATE TABLE IF NOT EXISTS stores 
                    (id INTEGER PRIMARY KEY, name TEXT, location TEXT, logo TEXT)''')
    # Table for Scans (linked to stores)
    conn.execute('''CREATE TABLE IF NOT EXISTS scans 
                    (id INTEGER PRIMARY KEY, store_id INTEGER, barcode TEXT, time DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    # Table to track the "Active Simulation"
    conn.execute('''CREATE TABLE IF NOT EXISTS active_sim (store_id INTEGER)''')
    conn.commit()
    conn.close()

# 🔥 FORCE DATABASE CREATION IMMEDIATELY 🔥
init_db()

# --- PART 2: FLASK BACKEND ---
app = Flask(__name__)

@app.route('/scan', methods=['POST'])
def receive_scan():
    data = request.json
    barcode = data.get('content') if data else None
    
    if not barcode:
        return {"status": "fail"}, 400

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    # Check if a simulation is running
    cur.execute("SELECT store_id FROM active_sim LIMIT 1")
    active = cur.fetchone()
    
    if active:
        # Save barcode and link it to the active store's ID
        cur.execute('INSERT INTO scans (store_id, barcode) VALUES (?, ?)', (active[0], barcode))
        conn.commit()
        conn.close()
        return {"status": "success"}, 200
    else:
        conn.close()
        print("Scanned, but no simulation is running!")
        return {"status": "rejected", "message": "Simulation OFF"}, 403

def run_flask():
    # We removed init_db() from here because it already ran at the top
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if "flask_started" not in st.session_state:
    thread = Thread(target=run_flask, daemon=True)
    thread.start()
    st.session_state.flask_started = True

# --- PART 3: STREAMLIT ADMIN DASHBOARD ---
st.set_page_config(page_title="KUHS Admin", layout="wide")
st.title("🏥 KUHS Central Admin Panel")
# --- PART 3: STREAMLIT ADMIN DASHBOARD ---
st.set_page_config(page_title="KUHS Admin", layout="wide")
st.title("🏥 KUHS Central Admin Panel")

# Navigation
menu = st.sidebar.radio("Navigation", ["Simulate Action", "Store History"])

def get_active_store():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT stores.name FROM active_sim JOIN stores ON active_sim.store_id = stores.id LIMIT 1")
    res = cur.fetchone()
    conn.close()
    return res[0] if res else None

# --- PAGE 1: SIMULATE ACTION ---
if menu == "Simulate Action":
    st.header("➕ Add Record & Start Scanning")
    
    active_store = get_active_store()
    if active_store:
        st.success(f"🟢 SIMULATION RUNNING FOR: {active_store}")
        st.info("Scanner is active. Any scans from your phone are going to this store.")
        
        # LIVE REAL-TIME TABLE
        st.subheader("Live Scans")
        conn = sqlite3.connect(DB_NAME)
        live_scans = pd.read_sql_query("SELECT barcode, time FROM scans WHERE store_id = (SELECT store_id FROM active_sim LIMIT 1) ORDER BY time DESC", conn)
        conn.close()
        
        if not live_scans.empty:
            st.dataframe(live_scans, use_container_width=True, hide_index=True)
        else:
            st.write("Waiting for phone scans...")

        if st.button("🛑 Stop Simulation", type="primary"):
            conn = sqlite3.connect(DB_NAME)
            conn.execute("DELETE FROM active_sim")
            conn.commit()
            conn.close()
            st.rerun()
            
        # ONLY RERUN WHEN SIMULATION IS ACTIVE
        time.sleep(2)
        st.rerun()
        
    else:
        st.warning("🔴 No simulation running. Phone scans will be rejected.")
        
        with st.form("add_store_form"):
            name = st.text_input("Store Name")
            location = st.text_input("Store Location")
            logo = st.file_uploader("Upload Store Logo", type=["jpg", "png", "jpeg"])
            submit = st.form_submit_button("🚀 Simulate Action")
            
            if submit and name and location and logo:
                img_path = os.path.join("logos", logo.name)
                with open(img_path, "wb") as f:
                    f.write(logo.getbuffer())
                
                conn = sqlite3.connect(DB_NAME)
                cur = conn.cursor()
                cur.execute("INSERT INTO stores (name, location, logo) VALUES (?, ?, ?)", (name, location, img_path))
                store_id = cur.lastrowid
                cur.execute("DELETE FROM active_sim")
                cur.execute("INSERT INTO active_sim (store_id) VALUES (?)", (store_id,))
                conn.commit()
                conn.close()
                st.rerun()

# --- PAGE 2: HISTORY ---
elif menu == "Store History":
    st.header("📂 Registered Stores")
    
    conn = sqlite3.connect(DB_NAME)
    stores = pd.read_sql_query("SELECT * FROM stores", conn)
    
    if stores.empty:
        st.write("No stores added yet.")
    else:
        cols = st.columns(3)
        for index, row in stores.iterrows():
            col = cols[index % 3]
            with col:
                st.image(row["logo"], width=150)
                st.subheader(row["name"])
                st.write(f"📍 {row['location']}")
                
                if st.button(f"View Scans for {row['name']}", key=row["id"]):
                    st.session_state.view_store = row["id"]
                    st.session_state.view_store_name = row["name"]
        
        st.markdown("---")
        
        if "view_store" in st.session_state:
            st.subheader(f"📦 Inventory for {st.session_state.view_store_name}")
            df_scans = pd.read_sql_query(f"SELECT barcode, time FROM scans WHERE store_id = {st.session_state.view_store} ORDER BY time DESC", conn)
            
            if df_scans.empty:
                st.write("No items scanned for this store yet.")
            else:
                st.dataframe(df_scans, use_container_width=True, hide_index=True)

    conn.close()

