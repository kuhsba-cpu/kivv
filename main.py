import streamlit as st
from flask import Flask, request
import sqlite3
from threading import Thread
import pandas as pd
import time
import os
from io import BytesIO

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
st.markdown(
    """
    <style>
    .metric-card { background: #0f172a; border: 1px solid #334155; border-radius: 20px; padding: 24px; }
    .stButton>button { background: #2563eb; color: white; border: none; border-radius: 12px; padding: 12px 18px; font-weight: 700; box-shadow: 0 10px 30px rgba(37,99,235,.18); }
    .stButton>button:hover { background: #1d4ed8; }
    .section-note { color: #94a3b8; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("KUHS Admin Dashboard")

if "current_page" not in st.session_state:
    st.session_state.current_page = "Home"

pages = ["Home", "Simulate Action", "Store History"]
page = st.sidebar.radio("Navigation", pages, index=pages.index(st.session_state.current_page))
st.session_state.current_page = page

def get_active_store():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT stores.name FROM active_sim JOIN stores ON active_sim.store_id = stores.id LIMIT 1")
    res = cur.fetchone()
    conn.close()
    return res[0] if res else None

def delete_store(store_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    # Get logo path before deleting
    cur.execute("SELECT logo FROM stores WHERE id = ?", (store_id,))
    logo_path = cur.fetchone()
    
    # Delete scans
    cur.execute("DELETE FROM scans WHERE store_id = ?", (store_id,))
    
    # Delete from active_sim if this store is active
    cur.execute("DELETE FROM active_sim WHERE store_id = ?", (store_id,))
    
    # Delete store
    cur.execute("DELETE FROM stores WHERE id = ?", (store_id,))
    
    conn.commit()
    conn.close()
    
    # Delete logo file if it exists
    if logo_path and logo_path[0] and os.path.exists(logo_path[0]):
        os.remove(logo_path[0])

def export_store_to_excel(store_id):
    conn = sqlite3.connect(DB_NAME)
    
    # Get store information
    store_info = pd.read_sql_query("SELECT id, name, location, logo FROM stores WHERE id = ?", conn, params=(store_id,))
    
    # Get all scans for this store
    scans_df = pd.read_sql_query("SELECT barcode, time FROM scans WHERE store_id = ? ORDER BY time DESC", conn, params=(store_id,))
    
    conn.close()
    
    # Create a comprehensive export with all data in one sheet
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Create a combined sheet with store info and scans
        combined_data = []
        
        # Add store information
        if not store_info.empty:
            store_row = store_info.iloc[0]
            combined_data.append(['STORE INFORMATION'])
            combined_data.append(['ID', store_row['id']])
            combined_data.append(['Name', store_row['name']])
            combined_data.append(['Location', store_row['location']])
            combined_data.append(['Logo', store_row['logo']])
            combined_data.append([])  # Empty row
        
        # Add scans information
        combined_data.append(['BARCODE SCANS'])
        combined_data.append(['Barcode', 'Timestamp'])
        
        if not scans_df.empty:
            for _, scan_row in scans_df.iterrows():
                combined_data.append([scan_row['barcode'], scan_row['time']])
        else:
            combined_data.append(['No scans recorded', ''])
        
        # Create DataFrame from combined data
        # Find the maximum row length
        max_len = max(len(row) for row in combined_data) if combined_data else 2
        # Pad shorter rows with empty strings
        padded_data = [row + [''] * (max_len - len(row)) for row in combined_data]
        
        combined_df = pd.DataFrame(padded_data)
        combined_df.to_excel(writer, sheet_name='Store Data', index=False, header=False)
    
    output.seek(0)
    return output

# --- HOME PAGE HELPERS ---
def get_dashboard_summary():
    conn = sqlite3.connect(DB_NAME)
    total_stores = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
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
    return total_stores, total_scans, scan_by_store, scan_over_time


def render_home_page():
    st.header("Welcome to KUHS Admin")
    st.markdown("Manage stores, review scan activity, and launch simulations from one polished dashboard.")

    total_stores, total_scans, scan_by_store, scan_over_time = get_dashboard_summary()
    active_store = get_active_store()

    col1, col2, col3 = st.columns(3)
    col1.metric("Registered Stores", total_stores)
    col2.metric("Total Scans", total_scans)
    col3.metric("Active Simulation", "Running" if active_store else "Stopped", active_store if active_store else "No active store")

    st.markdown("---")
    st.subheader("Recent activity")
    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.markdown("**Scans by store**")
        if not scan_by_store.empty:
            store_chart_data = scan_by_store.rename(columns={"store_name": "Store", "scan_count": "Scans"}).set_index("Store")
            st.bar_chart(store_chart_data)
        else:
            st.info("No scan data available yet.")

    with chart_col2:
        st.markdown("**Scan volume over time**")
        if not scan_over_time.empty:
            time_chart_data = scan_over_time.rename(columns={"scan_date": "Date", "scan_count": "Scans"}).set_index("Date")
            st.line_chart(time_chart_data)
        else:
            st.info("No historical scans found.")

    st.markdown("---")
    st.subheader("Quick actions")
    button_col1, button_col2 = st.columns(2)
    if button_col1.button("Start simulation"):
        st.session_state.current_page = "Simulate Action"
        st.experimental_rerun()
    if button_col2.button("View store history"):
        st.session_state.current_page = "Store History"
        st.experimental_rerun()


# --- PAGE 1: SIMULATE ACTION ---
if page == "Home":
    render_home_page()

elif page == "Simulate Action":
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
elif page == "Store History":
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
                # Check if logo file exists before displaying
                if row["logo"] and os.path.exists(row["logo"]):
                    st.image(row["logo"], width=150)
                else:
                    st.write("📷 No logo available")
                st.subheader(row["name"])
                st.write(f"📍 {row['location']}")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button(f"View Scans", key=f"view_{row['id']}"):
                        st.session_state.view_store = row["id"]
                        st.session_state.view_store_name = row["name"]
                with col2:
                    if st.button(f"🗑️ Delete", key=f"delete_{row['id']}", type="secondary"):
                        delete_store(row["id"])
                        st.success(f"Store '{row['name']}' deleted successfully!")
                        st.rerun()
                
                # Export button below the other buttons
                excel_data = export_store_to_excel(row["id"])
                st.download_button(
                    label="📊 Export to Excel",
                    data=excel_data,
                    file_name=f"{row['name'].replace(' ', '_')}_data.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"export_{row['id']}"
                )
        
        st.markdown("---")
        
        if "view_store" in st.session_state:
            st.subheader(f"📦 Inventory for {st.session_state.view_store_name}")
            df_scans = pd.read_sql_query(f"SELECT barcode, time FROM scans WHERE store_id = {st.session_state.view_store} ORDER BY time DESC", conn)
            
            if df_scans.empty:
                st.write("No items scanned for this store yet.")
            else:
                st.dataframe(df_scans, use_container_width=True, hide_index=True)

    conn.close()

