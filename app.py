from flask import Flask, render_template_string, request, redirect, url_for, session, flash, send_file
from functools import wraps
import os
import tempfile
import sqlite3
import time
from datetime import datetime

# Import directly from your provided laboratorysystem.py
from laboratorysystem import init_db, AuthController, TrackerController, DB_NAME, get_db_path

app = Flask(__name__)
app.secret_key = "lab7-development-secret-change-me"

# ==========================================
# BORROW & RETURN CONTROLLERS EXTENSION
# ==========================================
def init_borrow_db():
    conn = sqlite3.connect(get_db_path(DB_NAME))
    cursor = conn.cursor()
    # Borrow requests table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS borrow_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hardware_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            username TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            request_date REAL NOT NULL,
            FOREIGN KEY (hardware_id) REFERENCES hardware(id)
        )
    ''')
    # Return requests table (New Admin Approval workflow)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS return_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hardware_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            username TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            condition TEXT NOT NULL,
            remarks TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            request_date REAL NOT NULL,
            FOREIGN KEY (hardware_id) REFERENCES hardware(id)
        )
    ''')
    conn.commit()
    conn.close()

class BorrowController(TrackerController):
    def request_borrow(self, hardware_id, username, quantity):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name, quantity FROM hardware WHERE id=?", (hardware_id,))
        row = cursor.fetchone()
        if not row: return False, "Item not found."
        
        item_name, available = row
        if quantity > available:
            return False, f"Cannot borrow {quantity}. Only {available} available in stock."
        
        cursor.execute('''
            INSERT INTO borrow_requests (hardware_id, item_name, username, quantity, status, request_date)
            VALUES (?, ?, ?, ?, 'PENDING', ?)
        ''', (hardware_id, item_name, username, quantity, time.time()))
        conn.commit()
        conn.close()
        return True, f"Borrow request for {quantity}x {item_name} submitted to admin."

    def get_user_borrows(self, username):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM borrow_requests WHERE username=? ORDER BY request_date DESC", (username,))
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_pending_borrows(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM borrow_requests WHERE status='PENDING' ORDER BY request_date ASC")
        rows = cursor.fetchall()
        conn.close()
        return rows
        
    def process_borrow_request(self, req_id, decision):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT hardware_id, quantity, status FROM borrow_requests WHERE id=?", (req_id,))
        req = cursor.fetchone()
        if not req or req[2] != 'PENDING':
            return False, "Invalid or already processed request."
        
        hardware_id, qty, _ = req
        if decision == 'approve':
            cursor.execute("SELECT quantity FROM hardware WHERE id=?", (hardware_id,))
            avail = cursor.fetchone()[0]
            if qty > avail:
                return False, "Not enough stock available to approve this borrow request."
            
            new_qty = avail - qty
            new_status = self.stock_status(new_qty)
            cursor.execute("UPDATE hardware SET quantity=?, status=? WHERE id=?", (new_qty, new_status, hardware_id))
            cursor.execute("UPDATE borrow_requests SET status='APPROVED' WHERE id=?", (req_id,))
            msg = "Borrow request approved. Stock updated."
        else:
            cursor.execute("UPDATE borrow_requests SET status='REJECTED' WHERE id=?", (req_id,))
            msg = "Borrow request rejected."
            
        conn.commit()
        conn.close()
        return True, msg

class ReturnManager(TrackerController):
    def request_return(self, hardware_id, username, quantity, condition, remarks):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM hardware WHERE id=?", (hardware_id,))
        row = cursor.fetchone()
        if not row: return False, "Item not found."
        
        cursor.execute('''
            INSERT INTO return_requests (hardware_id, item_name, username, quantity, condition, remarks, status, request_date)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
        ''', (hardware_id, row[0], username, quantity, condition, remarks, time.time()))
        conn.commit()
        conn.close()
        return True, f"Return request for {quantity}x {row[0]} submitted for admin approval."

    def get_user_returns(self, username):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM return_requests WHERE username=? ORDER BY request_date DESC", (username,))
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_pending_returns(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM return_requests WHERE status='PENDING' ORDER BY request_date ASC")
        rows = cursor.fetchall()
        conn.close()
        return rows
        
    def process_return_request(self, req_id, decision, admin_username):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT hardware_id, quantity, condition, remarks, username, status FROM return_requests WHERE id=?", (req_id,))
        req = cursor.fetchone()
        
        if not req or req[5] != 'PENDING':
            return False, "Invalid or already processed return request."
            
        hw_id, qty, cond, remarks, ret_by, _ = req
        
        if decision == 'approve':
            # Call original TrackerController function to log in equipment_returns and update hardware stock
            ok, msg = self.record_return(hw_id, qty, cond, remarks, ret_by)
            if ok:
                cursor.execute("UPDATE return_requests SET status='APPROVED' WHERE id=?", (req_id,))
                conn.commit()
                conn.close()
                return True, "Return approved. Item restocked."
            return False, msg
        else:
            cursor.execute("UPDATE return_requests SET status='REJECTED' WHERE id=?", (req_id,))
            conn.commit()
            conn.close()
            return True, "Return request rejected."

# ==========================================
# UNIFIED CSS & LAYOUT TEMPLATES
# ==========================================

STYLE = """
<style>
    :root {
        --dark-bg: #0f172a;        
        --dark-card: #1e293b;      
        --light-bg: #f8fafc;       
        --white: #ffffff;
        --accent: #8b5cf6;         
        --text-main: #334155;
        --text-muted: #94a3b8;
        --border-color: #e2e8f0;
    }
    body { margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; background: var(--light-bg); color: var(--text-main); }
    
    .auth-wrapper { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background-color: var(--dark-bg); display: flex; justify-content: center; align-items: center; z-index: 1000; padding: 20px; }
    .auth-container { display: flex; width: 100%; max-width: 950px; height: 550px; background: white; border-radius: 20px; overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.4); }
    .auth-left { width: 35%; padding: 40px 30px; display: flex; flex-direction: column; position: relative; }
    .auth-right { width: 65%; padding: 40px; background: radial-gradient(ellipse at 70% 30%, #f7dfa4 0%, #3574a3 40%, var(--dark-bg) 100%); color: white; display: flex; flex-direction: column; justify-content: center; position: relative; }
    .pill-input { width: 100%; padding: 12px 15px 12px 35px; margin-bottom: 15px; border: 1.5px solid #cbd5e1; border-radius: 25px; box-sizing: border-box; font-weight: 600; font-size: 0.85em; }
    .input-icon { position: absolute; left: 12px; top: 12px; opacity: 0.5; font-size: 1.1em;}
    .pill-btn { width: 100%; padding: 12px; background: var(--dark-bg); color: white; border: none; border-radius: 25px; font-weight: bold; cursor: pointer; transition: 0.3s; }
    .pill-btn:hover { background: #1e293b; }

    .app-container { display: flex; flex-direction: column; min-height: 100vh; }
    .top-section { background: var(--dark-bg); padding: 20px 40px 80px 40px; border-radius: 0 0 30px 30px; color: white; }
    .navbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }
    .logo { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 1.1em; letter-spacing: 0.5px; }
    .logo-icon { width: 28px; height: 28px; background: var(--accent); border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 12px; }
    
    .nav-links { display: flex; gap: 5px; background: var(--dark-card); padding: 5px 10px; border-radius: 30px; }
    .nav-links a { color: var(--text-muted); text-decoration: none; font-weight: 600; font-size: 0.85em; padding: 8px 16px; border-radius: 20px; transition: 0.3s; }
    .nav-links a:hover { color: white; }
    .nav-links a.active { background: rgba(255,255,255,0.1); color: white; }
    
    .user-profile { display: flex; align-items: center; gap: 15px; }
    .avatar { width: 35px; height: 35px; background: #e2e8f0; color: var(--dark-bg); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 1.1em; }
    
    .page-title { font-size: 1.5em; margin: 0 0 20px 0; font-weight: 600; }
    .stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; }
    .stat-card { background: var(--dark-card); padding: 20px; border-radius: 16px; position: relative; }
    .stat-card p { margin: 0 0 10px 0; color: var(--text-muted); font-size: 0.85em; font-weight: 600; }
    .stat-card h3 { margin: 0; font-size: 2em; color: white; }
    .stat-icon { position: absolute; right: 20px; bottom: 20px; width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.9em; }
    
    .bottom-section { display: flex; gap: 30px; padding: 0 40px 40px 40px; margin-top: -40px; }
    .sidebar { width: 280px; background: white; padding: 25px; border-radius: 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.05); height: fit-content; z-index: 10; }
    .main-content { flex: 1; z-index: 10; }
    
    .sidebar h4 { margin: 0 0 20px 0; font-size: 1.1em; color: var(--dark-bg); }
    .sidebar label { display: block; font-size: 0.8em; font-weight: 600; color: var(--text-muted); margin-bottom: 5px; }
    .sidebar input, .sidebar select { width: 100%; padding: 10px 12px; margin-bottom: 15px; border: 1px solid var(--border-color); border-radius: 10px; box-sizing: border-box; background: #f8fafc; outline: none; font-family: inherit; }
    .sidebar input:focus, .sidebar select:focus { border-color: var(--accent); }
    .sidebar button { width: 100%; padding: 12px; background: var(--dark-bg); color: white; border: none; border-radius: 10px; font-weight: 600; cursor: pointer; transition: 0.3s; margin-top: 10px; }
    .sidebar button:hover { background: var(--accent); }
    .filter-item { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; font-size: 0.9em; font-weight: 600; color: var(--text-main); }
    
    table { width: 100%; border-collapse: separate; border-spacing: 0 12px; margin-top: -12px; }
    th { text-align: left; padding: 0 20px; color: var(--text-muted); font-weight: 600; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.5px; }
    td { padding: 16px 20px; background: white; border-top: 1px solid var(--border-color); border-bottom: 1px solid var(--border-color); color: var(--text-main); font-size: 0.9em; font-weight: 500; }
    td:first-child { border-left: 1px solid var(--border-color); border-radius: 16px 0 0 16px; font-weight: 700; color: var(--dark-bg); }
    td:last-child { border-right: 1px solid var(--border-color); border-radius: 0 16px 16px 0; }
    tr { transition: transform 0.2s, box-shadow 0.2s; }
    tr:hover td { background: #fdfdfd; box-shadow: 0 5px 15px rgba(0,0,0,0.02); }
    
    .badge { padding: 5px 12px; border-radius: 20px; font-size: 0.85em; font-weight: 700; }
    .badge.green { background: #dcfce7; color: #166534; }
    .badge.yellow { background: #fef9c3; color: #854d0e; }
    .badge.red { background: #fee2e2; color: #991b1b; }
    
    .flash-message { padding: 12px 20px; border-radius: 12px; margin-bottom: 20px; font-weight: 600; font-size: 0.9em; }
    .flash-success { background: #dcfce7; color: #166534; border: 1px solid #bbf7d0; }
    .flash-danger { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }
    
    .btn-small { padding: 6px 12px; border-radius: 8px; font-size: 0.85em; font-weight: 600; border: none; cursor: pointer; }
    .btn-danger { background: #fee2e2; color: #991b1b; }
    .btn-danger:hover { background: #fecaca; }
</style>
"""

def generate_base_layout(active_page, title, stats, sidebar_html, content_html, username, role):
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head><meta charset="UTF-8"><title>{title} - Campus Hardware</title>{STYLE}</head>
    <body>
        <div class="app-container">
            <!-- DARK HEADER SECTION -->
            <div class="top-section">
                <div class="navbar">
                    <div class="logo">
                        <div class="logo-icon">CH</div>
                        Campus Hardware
                    </div>
                    <div class="nav-links">
                        <a href="/dashboard" class="{'active' if active_page == 'dashboard' else ''}">Dashboard</a>
                        <a href="/inventory" class="{'active' if active_page == 'inventory' else ''}">Inventory</a>
                        <a href="/borrow" class="{'active' if active_page == 'borrow' else ''}">Borrow</a>
                        <a href="/returns" class="{'active' if active_page == 'returns' else ''}">Returns</a>
                        <a href="/reports" class="{'active' if active_page == 'reports' else ''}">Reports</a>
                        <a href="/account" class="{'active' if active_page == 'account' else ''}">Account</a>
                        {'<a href="/admin" class="active" style="color:#f59e0b;">Admin</a>' if active_page == 'admin' else ('<a href="/admin" style="color:#fcd34d;">Admin</a>' if role == 'ADMIN' else '')}
                    </div>
                    <div class="user-profile">
                        <span style="font-size: 0.9em; font-weight: 600;">{username}</span>
                        <div class="avatar">{username[0].upper()}</div>
                        <a href="/logout" style="color: #94a3b8; text-decoration: none; font-size: 1.2em; margin-left: 10px;" title="Logout">⏻</a>
                    </div>
                </div>
                
                <h1 class="page-title">{title}</h1>
                
                {f'''
                <div class="stats-row">
                    <div class="stat-card"><p>Total Item Types</p><h3>{stats['items']}</h3><div class="stat-icon" style="background: rgba(139, 92, 246, 0.2); color: #a78bfa;">📦</div></div>
                    <div class="stat-card"><p>Total Active Stock</p><h3>{stats['quantity']}</h3><div class="stat-icon" style="background: rgba(52, 211, 153, 0.2); color: #34d399;">✓</div></div>
                    <div class="stat-card"><p>Low Stock Items</p><h3>{stats['low_stock']}</h3><div class="stat-icon" style="background: rgba(251, 191, 36, 0.2); color: #fbbf24;">⚠</div></div>
                    <div class="stat-card"><p>Needs Attention</p><h3>{stats['repair']}</h3><div class="stat-icon" style="background: rgba(248, 113, 113, 0.2); color: #f87171;">✕</div></div>
                </div>
                ''' if active_page == 'dashboard' else ''}
            </div>
            
            <!-- LIGHT BOTTOM SECTION -->
            <div class="bottom-section" style="margin-top: {'-40px' if active_page == 'dashboard' else '-60px'};">
                <div class="sidebar">
                    {sidebar_html}
                </div>
                
                <div class="main-content">
                    {{% with messages = get_flashed_messages(with_categories=true) %}}
                        {{% if messages %}}
                            {{% for category, message in messages %}}
                                <div class="flash-message flash-{{{{ category }}}}">{{{{ message }}}}</div>
                            {{% endfor %}}
                        {{% endif %}}
                    {{% endwith %}}
                    {content_html}
                </div>
            </div>
        </div>
    </body>
    </html>
    """

LOGIN_HTML = STYLE + """
<div class="auth-wrapper">
    <div class="auth-container">
        <div class="auth-left">
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 30px;">
                <div style="width: 20px; height: 20px; background: var(--dark-bg); border-radius: 4px;"></div>
                <div style="line-height: 1.1; font-weight: 800; font-size: 0.9em; color: var(--dark-bg);">Campus<br>Hardware</div>
            </div>
            <div style="text-align: center; margin-bottom: 30px;">
                <div style="width: 60px; height: 60px; background: var(--dark-bg); border-radius: 50%; margin: 0 auto; display: flex; justify-content: center; align-items: center; color: white; font-size: 1.5em;">👤</div>
            </div>
            {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div style="color: {% if category == 'success' %}#166534{% else %}#991b1b{% endif %}; font-size: 0.85em; text-align: center; margin-bottom: 15px; font-weight: 700;">{{ message }}</div>{% endfor %}{% endif %}{% endwith %}
            <form method="POST" action="/login">
                <div style="position: relative;"><span class="input-icon">👤</span><input type="text" name="username" class="pill-input" placeholder="USERNAME" required></div>
                <div style="position: relative;"><span class="input-icon">🔒</span><input type="password" name="password" class="pill-input" placeholder="PASSWORD" required></div>
                <button type="submit" class="pill-btn">LOGIN</button>
            </form>
            <div style="text-align: center; font-size: 0.75em; font-weight: 600; margin-top: 15px;"><a href="/register" style="color: #64748b; text-decoration: none;">Create an account</a></div>
        </div>
        <div class="auth-right">
            <div style="position: absolute; top: 30px; right: 40px; font-size: 0.8em; font-weight: 700;"><a href="/register" style="background: var(--dark-bg); color: white; text-decoration: none; padding: 8px 20px; border-radius: 20px;">SIGN UP</a></div>
            <div style="margin-left: 20px;">
                <h1 style="font-size: 3.5em; margin-bottom: 10px; text-shadow: 0 5px 15px rgba(0,0,0,0.2);">Welcome.</h1>
                <p style="font-size: 0.9em; opacity: 0.9; max-width: 300px; line-height: 1.6;">Securely manage and track campus hardware inventory seamlessly.</p>
            </div>
        </div>
    </div>
</div>
"""

REGISTER_HTML = STYLE + """
<div class="auth-wrapper">
    <div class="auth-container">
        <div class="auth-left">
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 20px;">
                <div style="width: 20px; height: 20px; background: var(--dark-bg); border-radius: 4px;"></div>
                <div style="line-height: 1.1; font-weight: 800; font-size: 0.9em; color: var(--dark-bg);">Campus<br>Hardware</div>
            </div>
            <div style="text-align: center; margin-bottom: 20px;"><h3 style="color: var(--dark-bg); margin:0;">Register</h3></div>
            {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div style="color: {% if category == 'success' %}#166534{% else %}#991b1b{% endif %}; font-size: 0.85em; text-align: center; margin-bottom: 15px; font-weight: 700;">{{ message }}</div>{% endfor %}{% endif %}{% endwith %}
            <form method="POST" action="/register">
                <div style="position: relative;"><span class="input-icon">👤</span><input type="text" name="username" class="pill-input" placeholder="USERNAME" required></div>
                <div style="position: relative;"><span class="input-icon">✉️</span><input type="email" name="email" class="pill-input" placeholder="EMAIL ADDRESS" required></div>
                <div style="position: relative;"><span class="input-icon">🔒</span><input type="password" name="password" class="pill-input" placeholder="PASSWORD" required></div>
                <button type="submit" class="pill-btn">CREATE ACCOUNT</button>
            </form>
            <div style="text-align: center; font-size: 0.75em; font-weight: 600; margin-top: 15px;"><a href="/login" style="color: #64748b; text-decoration: none;">Already have an account? Sign in</a></div>
        </div>
        <div class="auth-right">
            <div style="position: absolute; top: 30px; right: 40px; font-size: 0.8em; font-weight: 700;"><a href="/login" style="background: var(--dark-bg); color: white; text-decoration: none; padding: 8px 20px; border-radius: 20px;">SIGN IN</a></div>
            <div style="margin-left: 20px;">
                <h1 style="font-size: 3.5em; margin-bottom: 10px; text-shadow: 0 5px 15px rgba(0,0,0,0.2);">Join Us.</h1>
                <p style="font-size: 0.9em; opacity: 0.9; max-width: 300px; line-height: 1.6;">Create an account to begin managing campus assets effectively.</p>
            </div>
        </div>
    </div>
</div>
"""

# ==========================================
# FLASK ROUTES
# ==========================================

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "username" not in session:
            flash("Please log in first.", "danger")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "ADMIN":
            flash("Administrator access required.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped

@app.route("/")
def index():
    if "username" in session: return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        auth = AuthController()
        ok, result = auth.login_user(request.form.get("username", "").strip(), request.form.get("password", "").strip())
        if ok:
            session.clear()
            session["username"] = result["username"]
            session["role"] = result["role"]
            return redirect(url_for("dashboard"))
        flash(result, "danger")
    return render_template_string(LOGIN_HTML)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        auth = AuthController()
        ok, msg = auth.register_user(request.form.get("username", "").strip(), request.form.get("email", "").strip(), request.form.get("password", "").strip())
        if ok:
            flash(msg, "success")
            return redirect(url_for("login"))
        flash(msg, "danger")
    return render_template_string(REGISTER_HTML)

@app.route("/dashboard")
@login_required
def dashboard():
    tracker = TrackerController()
    stats = tracker.get_dashboard_stats()
    
    username = session['username']
    is_admin = session.get('role') == 'ADMIN'
    
    conn = tracker._connect()
    c = conn.cursor()
    
    # Fetch data based on role
    if is_admin:
        c.execute("SELECT id, 'Borrow' as type, item_name, username, quantity, status, request_date FROM borrow_requests ORDER BY request_date DESC LIMIT 10")
        borrows = c.fetchall()
        c.execute("SELECT id, 'Return' as type, item_name, username, quantity, status, request_date FROM return_requests ORDER BY request_date DESC LIMIT 10")
        returns = c.fetchall()
    else:
        c.execute("SELECT id, 'Borrow' as type, item_name, username, quantity, status, request_date FROM borrow_requests WHERE username=? ORDER BY request_date DESC LIMIT 10", (username,))
        borrows = c.fetchall()
        c.execute("SELECT id, 'Return' as type, item_name, username, quantity, status, request_date FROM return_requests WHERE username=? ORDER BY request_date DESC LIMIT 10", (username,))
        returns = c.fetchall()
    conn.close()
    
    # Combine and sort all activity by date descending
    all_activity = borrows + returns
    all_activity.sort(key=lambda x: x[6], reverse=True)
    
    # Split into pending vs processed
    pending = [x for x in all_activity if x[5] == 'PENDING'][:10]
    history = [x for x in all_activity if x[5] != 'PENDING'][:10]
    
    sidebar = """
    <h4>Activity Filters</h4>
    <div class="filter-item"><input type="radio" checked style="width:auto; margin:0;"> All Activity</div>
    <div class="filter-item"><input type="radio" style="width:auto; margin:0;"> Borrows Only</div>
    <div class="filter-item"><input type="radio" style="width:auto; margin:0;"> Returns Only</div>
    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
    <h4>System Status</h4>
    <div class="filter-item" style="color:#166534;">● Database Online</div>
    <div class="filter-item" style="color:#166534;">● Workflows Active</div>
    """
    
    content = """
    <h3 style="margin: 0 0 15px 0;">Pending Requests</h3>
    <table style="margin-bottom: 30px;">
        <thead><tr><th>Type</th><th>Item Name</th><th>User</th><th>Qty</th><th>Status</th><th>Date</th></tr></thead>
        <tbody>
            {% for act in pending %}
            <tr>
                <td><span class="badge" style="background:var(--dark-bg); color:white;">{{ act[1] }}</span></td>
                <td style="font-weight: 700;">{{ act[2] }}</td>
                <td>{{ act[3] }}</td>
                <td style="font-weight: 700;">{{ act[4] }}x</td>
                <td><span class="badge yellow">{{ act[5] }}</span></td>
                <td style="color: var(--text-muted); font-size:0.85em;">{{ dt(act[6]) }}</td>
            </tr>
            {% else %}<tr><td colspan="6" style="text-align:center; padding: 20px;">No pending activity.</td></tr>{% endfor %}
        </tbody>
    </table>

    <h3 style="margin: 0 0 15px 0;">Recent History</h3>
    <table>
        <thead><tr><th>Type</th><th>Item Name</th><th>User</th><th>Qty</th><th>Status</th><th>Date</th></tr></thead>
        <tbody>
            {% for act in history %}
            <tr>
                <td><span class="badge" style="background:#e2e8f0; color:var(--dark-bg);">{{ act[1] }}</span></td>
                <td style="font-weight: 700;">{{ act[2] }}</td>
                <td>{{ act[3] }}</td>
                <td style="font-weight: 700;">{{ act[4] }}x</td>
                <td><span class="badge {% if act[5] == 'APPROVED' %}green{% else %}red{% endif %}">{{ act[5] }}</span></td>
                <td style="color: var(--text-muted); font-size:0.85em;">{{ dt(act[6]) }}</td>
            </tr>
            {% else %}<tr><td colspan="6" style="text-align:center; padding: 20px;">No recent history found.</td></tr>{% endfor %}
        </tbody>
    </table>
    """
    
    html = generate_base_layout('dashboard', 'Dashboard Overview', stats, sidebar, content, session['username'], session['role'])
    return render_template_string(html, pending=pending, history=history, dt=lambda ts: datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M'))

@app.route("/inventory")
@login_required
def inventory():
    tracker = TrackerController()
    items = tracker.fetch_all_items()
    
    sidebar = f"""
    <h4>Add New Hardware</h4>
    <form method="POST" action="/inventory/add">
        <label>Item Name</label><input type="text" name="item_name" required {'disabled' if session['role'] != 'ADMIN' else ''}>
        <label>Category</label><input type="text" name="category" required {'disabled' if session['role'] != 'ADMIN' else ''}>
        <div style="display:flex; gap:10px;">
            <div style="flex:1;"><label>Qty</label><input type="number" name="quantity" required {'disabled' if session['role'] != 'ADMIN' else ''}></div>
            <div style="flex:1;"><label>Price</label><input type="number" step="0.01" name="unit_price" required {'disabled' if session['role'] != 'ADMIN' else ''}></div>
        </div>
        <label>Condition</label>
        <select name="condition" {'disabled' if session['role'] != 'ADMIN' else ''}>
            <option value="New">New</option><option value="Good" selected>Good</option>
            <option value="Fair">Fair</option><option value="Damaged">Damaged</option><option value="Under Repair">Under Repair</option>
        </select>
        <label>Remarks</label><input type="text" name="remarks" {'disabled' if session['role'] != 'ADMIN' else ''}>
        {'<button type="submit">Add to Inventory</button>' if session['role'] == 'ADMIN' else '<p style="font-size:0.8em; color:red;">Admin access required to add.</p>'}
    </form>
    """
    
    content = """
    <table>
        <thead><tr><th>ID</th><th>Item</th><th>Category</th><th>Qty</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>
            {% for item in items %}
            <tr>
                <td>#{{ item[0] }}</td>
                <td style="font-weight: 700;">{{ item[1] }}<br><span style="font-size: 0.85em; color: var(--text-muted); font-weight: 400;">{{ item[6] }}</span></td>
                <td>{{ item[2] }}</td>
                <td style="font-weight: 700;">{{ item[3] }}x</td>
                <td><span class="badge {% if item[5] == 'In Stock' %}green{% elif item[5] == 'Low Stock' %}yellow{% else %}red{% endif %}">{{ item[5] }}</span></td>
                <td>
                    {% if session.get('role') == 'ADMIN' %}
                    <form method="POST" action="/inventory/delete/{{ item[0] }}" onsubmit="return confirm('Delete item?');">
                        <button type="submit" class="btn-small btn-danger">Delete</button>
                    </form>
                    {% else %}
                    <span style="color:var(--border-color);">--</span>
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    """
    
    html = generate_base_layout('inventory', 'Inventory Management', {}, sidebar, content, session['username'], session['role'])
    return render_template_string(html, items=items)

@app.route("/inventory/add", methods=["POST"])
@login_required
@admin_required
def inventory_add():
    ok, msg = TrackerController().add_item(request.form.get("item_name"), request.form.get("category"), int(request.form.get("quantity", 0)), float(request.form.get("unit_price", 0)), request.form.get("condition"), request.form.get("remarks", ""))
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("inventory"))

@app.route("/inventory/delete/<int:item_id>", methods=["POST"])
@login_required
@admin_required
def inventory_delete(item_id):
    ok, msg = TrackerController().delete_item(item_id)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("inventory"))

@app.route("/borrow", methods=["GET", "POST"])
@login_required
def borrow():
    borrow_controller = BorrowController()
    if request.method == "POST":
        ok, msg = borrow_controller.request_borrow(request.form.get("hardware_id"), session["username"], int(request.form.get("quantity", 1)))
        flash(msg, "success" if ok else "danger")
        return redirect(url_for("borrow"))
    
    items = TrackerController().fetch_all_items()
    my_borrows = borrow_controller.get_user_borrows(session["username"])
    
    sidebar = """
    <h4>Request to Borrow</h4>
    <form method="POST" action="/borrow">
        <label>Select Item to Borrow</label>
        <select name="hardware_id" required>
            {% for item in items %}{% if item[3] > 0 %}<option value="{{ item[0] }}">{{ item[1] }} ({{ item[3] }} avail)</option>{% endif %}{% endfor %}
        </select>
        <label>Quantity</label><input type="number" name="quantity" min="1" required>
        <button type="submit">Submit Request</button>
    </form>
    """
    
    content = """
    <h3 style="margin: 0 0 15px 0;">My Borrow Requests</h3>
    <table>
        <thead><tr><th>Req ID</th><th>Item Name</th><th>Qty</th><th>Status</th><th>Date</th></tr></thead>
        <tbody>
            {% for b in borrows %}
            <tr>
                <td>#{{ b[0] }}</td>
                <td style="font-weight: 700;">{{ b[2] }}</td>
                <td style="font-weight: 700;">{{ b[4] }}x</td>
                <td><span class="badge {% if b[5] == 'APPROVED' %}green{% elif b[5] == 'PENDING' %}yellow{% else %}red{% endif %}">{{ b[5] }}</span></td>
                <td style="color: var(--text-muted); font-size:0.85em;">{{ dt(b[6]) }}</td>
            </tr>
            {% else %}<tr><td colspan="5" style="text-align:center;">No borrow requests found.</td></tr>{% endfor %}
        </tbody>
    </table>
    """
    
    html = generate_base_layout('borrow', 'Borrow Equipment', {}, sidebar, content, session['username'], session['role'])
    return render_template_string(html, items=items, borrows=my_borrows, dt=lambda ts: datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M'))

@app.route("/returns", methods=["GET", "POST"])
@login_required
def returns():
    return_mgr = ReturnManager()
    if request.method == "POST":
        ok, msg = return_mgr.request_return(request.form.get("hardware_id"), session["username"], int(request.form.get("returned_quantity", 1)), request.form.get("return_condition"), request.form.get("remarks", ""))
        flash(msg, "success" if ok else "danger")
        return redirect(url_for("returns"))
    
    items = return_mgr.fetch_all_items()
    my_returns = return_mgr.get_user_returns(session["username"])
    
    sidebar = """
    <h4>Request to Return</h4>
    <form method="POST" action="/returns">
        <label>Select Equipment</label>
        <select name="hardware_id" required>
            {% for item in items %}<option value="{{ item[0] }}">{{ item[1] }}</option>{% endfor %}
        </select>
        <label>Returned Qty</label><input type="number" name="returned_quantity" min="1" required>
        <label>Condition</label>
        <select name="return_condition">
            <option value="New">New</option><option value="Good" selected>Good</option>
            <option value="Fair">Fair</option><option value="Damaged">Damaged</option><option value="Under Repair">Under Repair</option>
        </select>
        <label>Remarks</label><input type="text" name="remarks">
        <button type="submit">Submit Return</button>
    </form>
    """
    
    content = """
    <h3 style="margin: 0 0 15px 0;">My Return Requests</h3>
    <table>
        <thead><tr><th>Req ID</th><th>Equipment Name</th><th>Qty</th><th>Condition</th><th>Status</th><th>Date</th></tr></thead>
        <tbody>
            {% for req in requests %}
            <tr>
                <td>#{{ req[0] }}</td>
                <td style="font-weight: 700;">{{ req[2] }}</td>
                <td style="font-weight: 700;">{{ req[4] }}x</td>
                <td><span class="badge" style="background:#e2e8f0; color:var(--dark-bg);">{{ req[5] }}</span></td>
                <td><span class="badge {% if req[7] == 'APPROVED' %}green{% elif req[7] == 'PENDING' %}yellow{% else %}red{% endif %}">{{ req[7] }}</span></td>
                <td style="color: var(--text-muted); font-size:0.85em;">{{ dt(req[8]) }}</td>
            </tr>
            {% else %}<tr><td colspan="6" style="text-align:center;">No return requests found.</td></tr>{% endfor %}
        </tbody>
    </table>
    """
    
    html = generate_base_layout('returns', 'Equipment Returns', {}, sidebar, content, session['username'], session['role'])
    return render_template_string(html, items=items, requests=my_returns, dt=lambda ts: datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M'))

@app.route("/reports")
@login_required
def reports():
    sidebar = "<h4>Available Exports</h4><p style='font-size:0.9em; color:var(--text-muted);'>Generate CSV files compatible with Excel.</p>"
    content = """
    <div style="background:white; padding:40px; border-radius:20px; text-align:center; box-shadow:0 10px 25px rgba(0,0,0,0.02);">
        <h3 style="margin-bottom:30px;">Download System Data</h3>
        <a href="/reports/export_inventory" style="display:inline-block; margin-right:15px; padding:15px 30px; background:var(--dark-bg); color:white; text-decoration:none; border-radius:12px; font-weight:bold;">Download Inventory CSV</a>
        <a href="/reports/export_returns" style="display:inline-block; padding:15px 30px; background:white; color:var(--dark-bg); border: 2px solid var(--dark-bg); text-decoration:none; border-radius:12px; font-weight:bold;">Download Returns CSV</a>
    </div>
    """
    return render_template_string(generate_base_layout('reports', 'Reports & Export', {}, sidebar, content, session['username'], session['role']))

@app.route("/reports/export_inventory")
@login_required
def export_inventory():
    path = os.path.join(tempfile.gettempdir(), "inventory_report.csv")
    TrackerController().export_inventory_csv(path)
    return send_file(path, as_attachment=True, download_name="inventory_report.csv")

@app.route("/reports/export_returns")
@login_required
def export_returns():
    path = os.path.join(tempfile.gettempdir(), "returns_report.csv")
    TrackerController().export_returns_csv(path)
    return send_file(path, as_attachment=True, download_name="returns_report.csv")

@app.route("/account")
@login_required
def account():
    profile = AuthController().get_user_profile(session["username"])
    sidebar = f"""
    <h4>Profile Details</h4>
    <div class="filter-item"><strong>User:</strong> {profile['username']}</div>
    <div class="filter-item"><strong>Email:</strong> {profile['email']}</div>
    <div class="filter-item"><strong>Role:</strong> <span class="badge green">{profile['role']}</span></div>
    """
    content = """
    <div style="display:flex; gap:20px;">
        <div style="flex:1; background:white; padding:30px; border-radius:20px;">
            <h3 style="margin-top:0;">Change Password</h3>
            <form method="POST" action="/account/change_password">
                <input type="password" name="new_password" placeholder="Enter new password" style="width:100%; padding:10px 12px; margin-bottom:15px; border:1px solid var(--border-color); border-radius:10px;" required>
                <button type="submit" style="width:100%; padding:12px; background:var(--dark-bg); color:white; border:none; border-radius:10px; cursor:pointer; font-weight:bold;">Update Password</button>
            </form>
        </div>
        <div style="flex:1; background:white; padding:30px; border-radius:20px;">
            <h3 style="margin-top:0;">Account Recovery</h3>
            <p style="color:var(--text-muted); font-size:0.9em; margin-bottom:20px;">Request administrator assistance for locked accounts.</p>
            <form method="POST" action="/account/request_reset">
                <button type="submit" style="width:100%; padding:12px; background:white; color:var(--dark-bg); border:2px solid var(--dark-bg); border-radius:10px; cursor:pointer; font-weight:bold;">Submit Reset Request</button>
            </form>
        </div>
    </div>
    """
    return render_template_string(generate_base_layout('account', 'Account Settings', {}, sidebar, content, session['username'], session['role']))

@app.route("/account/change_password", methods=["POST"])
@login_required
def change_password():
    ok, msg = AuthController().change_password(session["username"], request.form.get("new_password"))
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("account"))

@app.route("/account/request_reset", methods=["POST"])
@login_required
def request_reset():
    profile = AuthController().get_user_profile(session["username"])
    ok, msg = AuthController().request_password_reset(profile["username"], profile["email"])
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("account"))

@app.route("/admin")
@login_required
@admin_required
def admin():
    auth = AuthController()
    borrow_ctrl = BorrowController()
    return_mgr = ReturnManager()
    
    requests = auth.get_pending_reset_requests()
    users = auth.get_all_users()
    pending_borrows = borrow_ctrl.get_pending_borrows()
    pending_returns = return_mgr.get_pending_returns()
    
    sidebar = """
    <h4>Admin Controls</h4>
    <p style="font-size:0.85em; color:var(--text-muted);">Manage users, handle account unlock requests, and approve hardware borrow/return workflows.</p>
    """
    
    content = """
    <h3 style="margin-bottom:15px;">Pending Borrow Approvals</h3>
    <table style="margin-bottom:30px;">
        <thead><tr><th>Req ID</th><th>User</th><th>Item</th><th>Qty</th><th>Action</th></tr></thead>
        <tbody>
            {% for req in pending_borrows %}
            <tr>
                <td>#{{ req[0] }}</td><td><strong>{{ req[3] }}</strong></td><td>{{ req[2] }}</td><td>{{ req[4] }}x</td>
                <td>
                    <form method="POST" action="/admin/borrow/{{ req[0] }}" style="display:inline;">
                        <button name="decision" value="approve" type="submit" class="btn-small" style="background:#dcfce7; color:#166534;">Approve</button>
                        <button name="decision" value="reject" type="submit" class="btn-small btn-danger">Reject</button>
                    </form>
                </td>
            </tr>
            {% else %}<tr><td colspan="5" style="text-align:center;">No pending borrow requests.</td></tr>{% endfor %}
        </tbody>
    </table>

    <h3 style="margin-bottom:15px;">Pending Return Approvals</h3>
    <table style="margin-bottom:30px;">
        <thead><tr><th>Req ID</th><th>User</th><th>Item</th><th>Qty</th><th>Condition</th><th>Action</th></tr></thead>
        <tbody>
            {% for req in pending_returns %}
            <tr>
                <td>#{{ req[0] }}</td><td><strong>{{ req[3] }}</strong></td><td>{{ req[2] }}</td><td>{{ req[4] }}x</td>
                <td><span class="badge" style="background:#e2e8f0; color:var(--dark-bg);">{{ req[5] }}</span></td>
                <td>
                    <form method="POST" action="/admin/return/{{ req[0] }}" style="display:inline;">
                        <button name="decision" value="approve" type="submit" class="btn-small" style="background:#dcfce7; color:#166534;">Approve</button>
                        <button name="decision" value="reject" type="submit" class="btn-small btn-danger">Reject</button>
                    </form>
                </td>
            </tr>
            {% else %}<tr><td colspan="6" style="text-align:center;">No pending return requests.</td></tr>{% endfor %}
        </tbody>
    </table>

    <h3 style="margin-bottom:15px;">Pending Password Resets</h3>
    <table style="margin-bottom:30px;">
        <thead><tr><th>Ticket ID</th><th>Username</th><th>Email</th><th>Action</th></tr></thead>
        <tbody>
            {% for req in requests %}
            <tr>
                <td>#{{ req[0] }}</td><td><strong>{{ req[1] }}</strong></td><td>{{ req[2] }}</td>
                <td>
                    <form method="POST" action="/admin/reset/{{ req[0] }}" style="display:inline;">
                        <button name="decision" value="approve" type="submit" class="btn-small" style="background:#dcfce7; color:#166534;">Approve</button>
                        <button name="decision" value="reject" type="submit" class="btn-small btn-danger">Reject</button>
                    </form>
                </td>
            </tr>
            {% else %}<tr><td colspan="4" style="text-align:center;">No pending recovery requests.</td></tr>{% endfor %}
        </tbody>
    </table>
    
    <h3 style="margin-bottom:15px;">User Directory</h3>
    <table>
        <thead><tr><th>User</th><th>Email</th><th>Role</th><th>Status</th><th>Management</th></tr></thead>
        <tbody>
            {% for u in users %}
            <tr>
                <td><strong>{{ u[0] }}</strong></td><td>{{ u[1] }}</td>
                <td><span class="badge" style="background:#e2e8f0; color:var(--dark-bg);">{{ u[2] }}</span></td>
                <td><span class="badge {% if u[4] > 0 %}red{% else %}green{% endif %}">{% if u[4] > 0 %}LOCKED{% else %}ACTIVE{% endif %}</span></td>
                <td>
                    {% if u[4] > 0 %}
                    <form method="POST" action="/admin/unlock/{{ u[0] }}" style="display:inline;">
                        <button type="submit" class="btn-small" style="background:var(--dark-bg); color:white;">Force Unlock</button>
                    </form>
                    {% else %}<span style="color:#cbd5e1;">--</span>{% endif %}
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    """
    
    html = generate_base_layout('admin', 'Administrator Panel', {}, sidebar, content, session['username'], session['role'])
    return render_template_string(html, requests=requests, users=users, pending_borrows=pending_borrows, pending_returns=pending_returns)

@app.route("/admin/reset/<int:req_id>", methods=["POST"])
@login_required
@admin_required
def admin_reset(req_id):
    ok, msg = AuthController().approve_reset_request(req_id, session["username"], request.form.get("decision"))
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("admin"))

@app.route("/admin/borrow/<int:req_id>", methods=["POST"])
@login_required
@admin_required
def admin_process_borrow(req_id):
    ok, msg = BorrowController().process_borrow_request(req_id, request.form.get("decision"))
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("admin"))

@app.route("/admin/return/<int:req_id>", methods=["POST"])
@login_required
@admin_required
def admin_process_return(req_id):
    ok, msg = ReturnManager().process_return_request(req_id, request.form.get("decision"), session["username"])
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("admin"))

@app.route("/admin/unlock/<username>", methods=["POST"])
@login_required
@admin_required
def admin_unlock(username):
    ok, msg = AuthController().unlock_user_account(username)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("admin"))

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))

if __name__ == "__main__":
    init_db()
    init_borrow_db()
    print("=" * 50)
    print(" SERVER RUNNING: ACTIVITY DASHBOARD ENABLED ")
    print(" Open http://127.0.0.1:5000 in Google Chrome ")
    print("=" * 50)
    app.run(debug=True)