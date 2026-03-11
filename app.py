from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3, os, socket, uuid, hmac, hashlib, json
from datetime import datetime
from functools import wraps
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'shiva12_secret_2024')

# ── Razorpay Keys ── replace with your real keys from razorpay.com/dashboard
RAZORPAY_KEY_ID     = 'rzp_test_XXXXXXXXXXXXXXXX'   # paste your Key ID here
RAZORPAY_KEY_SECRET = 'XXXXXXXXXXXXXXXXXXXXXXXX'     # paste your Key Secret here
# On Railway use /tmp for writable storage, locally use current dir
DB = '/tmp/database.db' if os.environ.get('RAILWAY_PUBLIC_DOMAIN') else 'database.db'

# ─── Image upload config ──────────────────────────────────
UPLOAD_FOLDER = '/tmp/uploads' if os.environ.get('RAILWAY_PUBLIC_DOMAIN') else os.path.join('static', 'uploads')
ALLOWED_EXT     = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER']   = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024   # 5 MB max

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def save_image(file_field_name):
    """Save uploaded image, return filename or None."""
    if file_field_name not in request.files:
        return None
    f = request.files[file_field_name]
    if not f or f.filename == '':
        return None
    if not allowed_file(f.filename):
        return None
    ext      = f.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    return filename

def delete_image(filename):
    """Delete image file from disk."""
    if filename:
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(path):
            os.remove(path)

# ─── Auto-detect local IP ─────────────────────────────────
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

PORT     = int(os.environ.get('PORT', 5000))
LOCAL_IP = get_local_ip()
# On Railway, BASE_URL comes from environment variable RAILWAY_PUBLIC_DOMAIN
_railway_domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
BASE_URL = f"https://{_railway_domain}" if _railway_domain else f"http://{LOCAL_IP}:{PORT}" 

# ─── DB helper ────────────────────────────────────────────
def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

# ─── Auth decorators ──────────────────────────────────────
def owner_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if 'owner_id' not in session:
            return redirect(url_for('owner_login'))
        return f(*a, **kw)
    return wrap

def admin_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*a, **kw)
    return wrap

def kitchen_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if 'kitchen_shop_id' not in session:
            return redirect(url_for('kitchen_login'))
        return f(*a, **kw)
    return wrap

# ══════════════════════════════════════════════════════
#  CUSTOMER PORTAL
# ══════════════════════════════════════════════════════
@app.route('/')
def home():
    return redirect(url_for('admin_login'))

@app.route('/shop/<slug>')
def shop(slug):
    d = db()
    s = d.execute('SELECT * FROM shops WHERE slug=? AND is_active=1', (slug,)).fetchone()
    if not s:
        return "<h2 style='font-family:sans-serif;padding:40px;color:#c00'>Shop not found or is currently closed.</h2>", 404
    items = d.execute(
        'SELECT * FROM items WHERE shop_id=? AND is_available=1 ORDER BY category, name',
        (s['id'],)
    ).fetchall()
    d.close()
    return render_template('customer/shop.html', shop=s, items=items, rzp_key=RAZORPAY_KEY_ID)

@app.route('/api/place_order', methods=['POST'])
def place_order():
    data      = request.get_json()
    shop_id   = data['shop_id']
    items     = data['items']
    cname     = data.get('customer_name', 'Guest')
    phone     = data.get('phone', '')
    total     = data.get('total', 0)
    payment   = data.get('payment', 'COD')   # 'COD' or 'UPI'
    d         = db()
    today     = datetime.now().strftime('%Y-%m-%d')
    row       = d.execute(
        'SELECT MAX(token_number) as m FROM orders WHERE shop_id=? AND DATE(created_at)=?',
        (shop_id, today)
    ).fetchone()
    token     = (row['m'] or 0) + 1
    items_str = ', '.join(f"{i['name']} x{i['qty']}" for i in items)
    # UPI orders start as pending_payment until owner verifies
    # COD orders go straight to received
    initial_status = 'pending_payment' if payment == 'UPI' else 'received'
    oid = d.execute(
        'INSERT INTO orders(shop_id,customer_name,phone,items,total,status,payment,token_number,created_at)'
        ' VALUES(?,?,?,?,?,?,?,?,?)',
        (shop_id, cname, phone, items_str, total, initial_status, payment, token, datetime.now())
    ).lastrowid
    d.commit(); d.close()
    return jsonify(success=True, order_id=oid, token=token)


# ── Razorpay: Create Order ─────────────────────────────────────────
# Called from JS before showing the Razorpay checkout popup.
# Returns a Razorpay order_id that the frontend uses to open the modal.
@app.route('/api/razorpay/create_order', methods=['POST'])
def razorpay_create_order():
    import urllib.request
    import base64
    data      = request.get_json()
    amount    = int(float(data.get('amount', 0)) * 100)  # Razorpay needs paise (₹1 = 100 paise)
    shop_id   = data.get('shop_id')
    rp_payload = json.dumps({
        'amount'  : amount,
        'currency': 'INR',
        'payment_capture': 1
    }).encode()
    credentials = base64.b64encode(
        f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()
    ).decode()
    req = urllib.request.Request(
        'https://api.razorpay.com/v1/orders',
        data    = rp_payload,
        headers = {
            'Content-Type' : 'application/json',
            'Authorization': f'Basic {credentials}'
        }
    )
    try:
        with urllib.request.urlopen(req) as resp:
            rp_order = json.loads(resp.read())
        return jsonify(success=True, rp_order_id=rp_order['id'], amount=amount)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500


# ── Razorpay: Verify Payment + Save Order ─────────────────────────
# Called after the Razorpay popup completes successfully.
# Verifies the signature, then saves the order to DB.
@app.route('/api/razorpay/verify', methods=['POST'])
def razorpay_verify():
    data = request.get_json()

    # 1. Verify signature to confirm payment is genuine
    rp_order_id   = data.get('razorpay_order_id', '')
    rp_payment_id = data.get('razorpay_payment_id', '')
    rp_signature  = data.get('razorpay_signature', '')
    expected_sig  = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        f"{rp_order_id}|{rp_payment_id}".encode(),
        hashlib.sha256
    ).hexdigest()

    if expected_sig != rp_signature:
        return jsonify(success=False, error='Payment verification failed'), 400

    # 2. Save verified order to DB
    shop_id   = data['shop_id']
    items     = data['items']
    cname     = data.get('customer_name', 'Guest')
    phone     = data.get('phone', '')
    total     = data.get('total', 0)
    d         = db()
    today     = datetime.now().strftime('%Y-%m-%d')
    row       = d.execute(
        'SELECT MAX(token_number) as m FROM orders WHERE shop_id=? AND DATE(created_at)=?',
        (shop_id, today)
    ).fetchone()
    token     = (row['m'] or 0) + 1
    items_str = ', '.join(f"{i['name']} x{i['qty']}" for i in items)
    oid = d.execute(
        'INSERT INTO orders(shop_id,customer_name,phone,items,total,status,payment,token_number,created_at)'
        ' VALUES(?,?,?,?,?,"received","Online",?,?)',
        (shop_id, cname, phone, items_str, total, token, datetime.now())
    ).lastrowid
    d.commit(); d.close()
    return jsonify(success=True, order_id=oid, token=token)

@app.route('/order/<int:oid>')
def order_page(oid):
    d = db()
    o = d.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
    s = d.execute('SELECT * FROM shops WHERE id=?', (o['shop_id'],)).fetchone() if o else None
    d.close()
    if not o:
        return "Order not found", 404
    return render_template('customer/order_status.html', order=o, shop=s)

@app.route('/api/order_status/<int:oid>')
def api_order_status(oid):
    d   = db()
    row = d.execute('SELECT status, token_number FROM orders WHERE id=?', (oid,)).fetchone()
    d.close()
    if not row:
        return jsonify(error='not found'), 404
    return jsonify(status=row['status'], token=row['token_number'])

# ══════════════════════════════════════════════════════
#  KITCHEN PORTAL
# ══════════════════════════════════════════════════════
@app.route('/kitchen/login', methods=['GET', 'POST'])
def kitchen_login():
    if request.method == 'POST':
        d    = db()
        shop = d.execute(
            'SELECT * FROM shops WHERE kitchen_pin=? AND is_active=1',
            (request.form['pin'],)
        ).fetchone()
        d.close()
        if shop:
            session['kitchen_shop_id']   = shop['id']
            session['kitchen_shop_name'] = shop['name']
            return redirect(url_for('kitchen_dashboard'))
        flash('Wrong PIN. Try again.', 'error')
    return render_template('kitchen/login.html')

@app.route('/kitchen/logout')
def kitchen_logout():
    session.pop('kitchen_shop_id', None)
    session.pop('kitchen_shop_name', None)
    return redirect(url_for('kitchen_login'))

@app.route('/kitchen')
@kitchen_required
def kitchen_dashboard():
    d = db()
    orders = d.execute(
        "SELECT * FROM orders WHERE shop_id=? AND status IN ('pending_payment','received','preparing','ready') ORDER BY created_at DESC",
        (session['kitchen_shop_id'],)
    ).fetchall()
    d.close()
    return render_template('kitchen/dashboard.html', orders=orders,
                           shop_name=session['kitchen_shop_name'])

@app.route('/kitchen/update', methods=['POST'])
@kitchen_required
def kitchen_update():
    data   = request.get_json()
    oid    = data['order_id']
    status = data['status']
    if status not in ('preparing', 'ready', 'completed'):
        return jsonify(error='invalid status'), 400
    d = db()
    d.execute('UPDATE orders SET status=? WHERE id=? AND shop_id=?',
              (status, oid, session['kitchen_shop_id']))
    d.commit(); d.close()
    return jsonify(success=True, status=status)

@app.route('/api/kitchen_poll')
@kitchen_required
def kitchen_poll():
    d = db()
    orders = d.execute(
        "SELECT * FROM orders WHERE shop_id=? AND status IN ('pending_payment','received','preparing','ready') ORDER BY created_at DESC",
        (session['kitchen_shop_id'],)
    ).fetchall()
    d.close()
    return jsonify(orders=[dict(o) for o in orders])

# ══════════════════════════════════════════════════════
#  OWNER PORTAL
# ══════════════════════════════════════════════════════
@app.route('/owner/login', methods=['GET', 'POST'])
def owner_login():
    if request.method == 'POST':
        d = db()
        o = d.execute('SELECT * FROM owners WHERE username=? AND password=?',
                      (request.form['username'], request.form['password'])).fetchone()
        d.close()
        if o:
            session['owner_id']   = o['id']
            session['owner_name'] = o['name']
            return redirect(url_for('owner_dashboard'))
        flash('❌ Wrong username or password.', 'error')
    return render_template('owner/login.html')

@app.route('/owner/logout')
def owner_logout():
    session.pop('owner_id', None)
    session.pop('owner_name', None)
    return redirect(url_for('owner_login'))

@app.route('/owner')
@owner_required
def owner_dashboard():
    d    = db()
    shop = d.execute('SELECT * FROM shops WHERE owner_id=?', (session['owner_id'],)).fetchone()
    if not shop:
        d.close()
        return "<h2 style='font-family:sans-serif;padding:40px'>No shop linked. Contact admin.</h2>", 404
    today   = datetime.now().strftime('%Y-%m-%d')
    orders  = d.execute(
        'SELECT * FROM orders WHERE shop_id=? AND DATE(created_at)=? ORDER BY created_at DESC',
        (shop['id'], today)
    ).fetchall()
    upi_pending = sum(1 for o in orders if o['status'] == 'pending_payment')
    items   = d.execute(
        'SELECT * FROM items WHERE shop_id=? ORDER BY category, name',
        (shop['id'],)
    ).fetchall()
    revenue = sum(o['total'] for o in orders if o['status'] == 'completed')
    pending = sum(1 for o in orders if o['status'] in ('received', 'preparing'))
    done    = sum(1 for o in orders if o['status'] == 'completed')
    d.close()
    return render_template('owner/dashboard.html',
                           shop=shop, orders=orders, items=items,
                           revenue=revenue, pending=pending, done=done,
                           upi_pending=upi_pending,
                           total_orders=len(orders), base_url=BASE_URL)

# ── Item CRUD with image support ──────────────────────────
@app.route('/owner/item/add', methods=['POST'])
@owner_required
def owner_add_item():
    d        = db()
    shop     = d.execute('SELECT id FROM shops WHERE owner_id=?', (session['owner_id'],)).fetchone()
    img_file = save_image('image')
    d.execute(
        'INSERT INTO items(shop_id,name,description,price,category,image,is_available) VALUES(?,?,?,?,?,?,1)',
        (shop['id'], request.form['name'], request.form.get('description', ''),
         float(request.form['price']), request.form.get('category', 'General'),
         img_file)
    )
    d.commit(); d.close()
    flash('✅ Item added!', 'success')
    return redirect(url_for('owner_dashboard') + '#items')

@app.route('/owner/item/edit/<int:iid>', methods=['POST'])
@owner_required
def owner_edit_item(iid):
    d       = db()
    old     = d.execute('SELECT image FROM items WHERE id=?', (iid,)).fetchone()
    new_img = save_image('image')
    if new_img:
        # delete old image from disk
        delete_image(old['image'] if old else None)
        img = new_img
    else:
        img = old['image'] if old else None   # keep existing
    d.execute(
        'UPDATE items SET name=?,description=?,price=?,category=?,image=? WHERE id=?',
        (request.form['name'], request.form.get('description', ''),
         float(request.form['price']), request.form.get('category', 'General'),
         img, iid)
    )
    d.commit(); d.close()
    flash('✅ Item updated!', 'success')
    return redirect(url_for('owner_dashboard') + '#items')

@app.route('/owner/item/delete/<int:iid>', methods=['POST'])
@owner_required
def owner_delete_item(iid):
    d   = db()
    row = d.execute('SELECT image FROM items WHERE id=?', (iid,)).fetchone()
    delete_image(row['image'] if row else None)
    d.execute('DELETE FROM items WHERE id=?', (iid,))
    d.commit(); d.close()
    flash('🗑 Item deleted.', 'success')
    return redirect(url_for('owner_dashboard') + '#items')

@app.route('/owner/item/toggle/<int:iid>', methods=['POST'])
@owner_required
def owner_toggle_item(iid):
    d   = db()
    cur = d.execute('SELECT is_available FROM items WHERE id=?', (iid,)).fetchone()
    nv  = 0 if cur['is_available'] else 1
    d.execute('UPDATE items SET is_available=? WHERE id=?', (nv, iid))
    d.commit(); d.close()
    return jsonify(success=True, is_available=nv)

@app.route('/owner/order/update', methods=['POST'])
@owner_required
def owner_update_order():
    data   = request.get_json()
    action = data.get('status')
    oid    = data['order_id']
    d      = db()
    if action == 'confirm_payment':
        # UPI payment verified by owner — move to received so kitchen sees it
        d.execute("UPDATE orders SET status='received' WHERE id=?", (oid,))
    elif action == 'reject_payment':
        # Owner rejected — mark cancelled
        d.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    else:
        d.execute('UPDATE orders SET status=? WHERE id=?', (action, oid))
    d.commit(); d.close()
    return jsonify(success=True)


@app.route('/owner/order/mark_paid', methods=['POST'])
@owner_required
def owner_mark_paid():
    data = request.get_json()
    oid  = data['order_id']
    d    = db()
    # Move from pending_payment → received so kitchen can see it
    d.execute("UPDATE orders SET status='received' WHERE id=? AND status='pending_payment'", (oid,))
    d.commit(); d.close()
    return jsonify(success=True)

@app.route('/owner/shop/update', methods=['POST'])
@owner_required
def owner_update_shop():
    d    = db()
    shop = d.execute('SELECT id FROM shops WHERE owner_id=?', (session['owner_id'],)).fetchone()
    d.execute('UPDATE shops SET name=?,description=?,kitchen_pin=? WHERE id=?',
              (request.form['name'], request.form.get('description', ''),
               request.form.get('kitchen_pin', '1234'), shop['id']))
    d.commit(); d.close()
    flash('✅ Settings saved!', 'success')
    return redirect(url_for('owner_dashboard'))

# ══════════════════════════════════════════════════════
#  ADMIN PORTAL
# ══════════════════════════════════════════════════════
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form['username'] == 'admin' and request.form['password'] == 'admin123':
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        flash('❌ Wrong credentials.', 'error')
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    d      = db()
    shops  = d.execute(
        'SELECT s.*,o.name as owner_name,o.username FROM shops s LEFT JOIN owners o ON s.owner_id=o.id ORDER BY s.id DESC'
    ).fetchall()
    owners = d.execute('SELECT * FROM owners ORDER BY id DESC').fetchall()
    today  = datetime.now().strftime('%Y-%m-%d')
    orders = d.execute(
        'SELECT ord.*,s.name as shop_name FROM orders ord JOIN shops s ON ord.shop_id=s.id'
        ' WHERE DATE(ord.created_at)=? ORDER BY ord.created_at DESC',
        (today,)
    ).fetchall()
    total_rev = sum(o['total'] for o in orders if o['status'] == 'completed')
    d.close()
    return render_template('admin/dashboard.html',
                           shops=shops, owners=owners, orders=orders,
                           total_rev=total_rev, base_url=BASE_URL)

@app.route('/admin/shop/add', methods=['POST'])
@admin_required
def admin_add_shop():
    d    = db()
    slug = request.form['slug'].strip().lower().replace(' ', '-')
    if d.execute('SELECT id FROM shops WHERE slug=?', (slug,)).fetchone():
        d.close()
        flash(f'❌ Slug "{slug}" already exists.', 'error')
        return redirect(url_for('admin_dashboard'))
    if d.execute('SELECT id FROM owners WHERE username=?', (request.form['username'],)).fetchone():
        d.close()
        flash(f'❌ Username "{request.form["username"]}" already taken.', 'error')
        return redirect(url_for('admin_dashboard'))
    oid = d.execute('INSERT INTO owners(name,username,password) VALUES(?,?,?)',
                    (request.form['owner_name'], request.form['username'],
                     request.form['password'])).lastrowid
    d.execute(
        'INSERT INTO shops(owner_id,name,slug,description,kitchen_pin,is_active) VALUES(?,?,?,?,?,1)',
        (oid, request.form['shop_name'], slug,
         request.form.get('description', ''), request.form.get('kitchen_pin', '1234'))
    )
    d.commit(); d.close()
    flash(f'✅ Shop "{request.form["shop_name"]}" created!  Login → {request.form["username"]} / {request.form["password"]}', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/shop/edit/<int:sid>', methods=['POST'])
@admin_required
def admin_edit_shop(sid):
    d = db()
    d.execute('UPDATE shops SET name=?,description=?,kitchen_pin=?,is_active=? WHERE id=?',
              (request.form['name'], request.form.get('description', ''),
               request.form.get('kitchen_pin', '1234'),
               int(request.form.get('is_active', 1)), sid))
    new_u = request.form.get('owner_username', '').strip()
    new_p = request.form.get('owner_password', '').strip()
    if new_u or new_p:
        shop = d.execute('SELECT owner_id FROM shops WHERE id=?', (sid,)).fetchone()
        if new_u and new_p:
            d.execute('UPDATE owners SET username=?,password=? WHERE id=?',
                      (new_u, new_p, shop['owner_id']))
        elif new_u:
            d.execute('UPDATE owners SET username=? WHERE id=?', (new_u, shop['owner_id']))
        else:
            d.execute('UPDATE owners SET password=? WHERE id=?', (new_p, shop['owner_id']))
    d.commit(); d.close()
    flash('✅ Shop updated!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/shop/delete/<int:sid>', methods=['POST'])
@admin_required
def admin_delete_shop(sid):
    d    = db()
    shop = d.execute('SELECT owner_id FROM shops WHERE id=?', (sid,)).fetchone()
    # delete item images
    items = d.execute('SELECT image FROM items WHERE shop_id=?', (sid,)).fetchall()
    for item in items:
        delete_image(item['image'])
    d.execute('DELETE FROM items WHERE shop_id=?', (sid,))
    d.execute('DELETE FROM orders WHERE shop_id=?', (sid,))
    if shop:
        d.execute('DELETE FROM owners WHERE id=?', (shop['owner_id'],))
    d.execute('DELETE FROM shops WHERE id=?', (sid,))
    d.commit(); d.close()
    flash('🗑 Shop deleted.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/shop/toggle/<int:sid>', methods=['POST'])
@admin_required
def admin_toggle_shop(sid):
    d   = db()
    cur = d.execute('SELECT is_active FROM shops WHERE id=?', (sid,)).fetchone()
    nv  = 0 if cur['is_active'] else 1
    d.execute('UPDATE shops SET is_active=? WHERE id=?', (nv, sid))
    d.commit(); d.close()
    return jsonify(success=True, is_active=nv)

# ── QR code — uses real LAN IP so phone can open it ───────
@app.route('/admin/qr/<slug>')
@admin_required
def admin_qr(slug):
    import qrcode, io, base64
    url = f"{BASE_URL}/shop/{slug}"
    buf = io.BytesIO()
    qrcode.make(url).save(buf, 'PNG')
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'''<!DOCTYPE html>
<html><head><title>QR — {slug}</title>
<link rel="stylesheet" href="/static/style.css">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="display:flex;align-items:center;justify-content:center;min-height:100vh;
             background:var(--ink);padding:20px">
<div style="background:white;border-radius:20px;padding:32px;text-align:center;
            max-width:360px;width:100%">
  <div style="font-family:Syne,sans-serif;font-weight:800;font-size:1.6rem;
              color:#FF6B00;margin-bottom:4px">Shiva@12</div>
  <div style="font-size:.8rem;color:#888;margin-bottom:20px">
    Point phone camera at QR to order
  </div>

  <img src="data:image/png;base64,{b64}"
       style="width:240px;border-radius:10px;margin:0 auto 20px;display:block;
              border:4px solid #f0f0f0">

  <div style="background:#fff7f0;border:2px solid #FF6B00;border-radius:10px;
              padding:12px;margin-bottom:12px">
    <div style="font-size:.68rem;color:#888;text-transform:uppercase;
                letter-spacing:1px;margin-bottom:4px">Shop URL</div>
    <div style="font-size:.82rem;font-weight:600;color:#333;word-break:break-all">
      {url}
    </div>
  </div>

  <div style="background:#fff3e0;border-radius:8px;padding:10px;
              margin-bottom:20px;font-size:.78rem;color:#b45309">
    📱 Phone must be on <strong>same WiFi</strong> as this computer
  </div>

  <a href="/admin" style="color:#FF6B00;text-decoration:none;font-size:.85rem">
    ← Back to Admin
  </a>
</div>
</body></html>'''

# ─── Startup ──────────────────────────────────────────────
if __name__ == '__main__':
    print("\n" + "━" * 58)
    print("  🚀  Shiva@12 started!")
    print("━" * 58)
    print(f"  💻  This computer  →  http://127.0.0.1:{PORT}")
    print(f"  📱  Phone / Tablet →  {BASE_URL}   ← use this for QR")
    print("━" * 58)
    print(f"  ⚙️   Admin          →  {BASE_URL}/admin/login    (admin / admin123)")
    print(f"  🏪  Owner          →  {BASE_URL}/owner/login")
    print(f"  👨‍🍳  Kitchen        →  {BASE_URL}/kitchen/login")
    print(f"  🛒  Customer       →  {BASE_URL}/shop/ram-tea-stall")
    print("━" * 58)
    print("  ⚠️   Phone must be on the SAME WiFi network!")
    print("  ⚠️   Keep this terminal open while using the app.")
    print("━" * 58 + "\n")
    app.run(debug=True, host='0.0.0.0', port=PORT)