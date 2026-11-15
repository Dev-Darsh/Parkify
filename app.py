from datetime import datetime, timedelta
import os, sqlite3, uuid
from functools import wraps
from flask import Flask, redirect, render_template, render_template_string, request, session, url_for, flash, jsonify
from markupsafe import Markup
import qrcode
from parking_ml import predict_demand

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database.db')
QR_DIR = os.path.join(BASE_DIR, 'static', 'qrcodes')
os.makedirs(QR_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = 'parkify_college_demo_secret_key'


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(reset=False):
    conn = db(); c = conn.cursor()
    if reset:
        for t in ['notifications','waiting_list','payments','bookings','parking_slots','parking_lots','users']:
            c.execute(f'DROP TABLE IF EXISTS {t}')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'customer')''')
    c.execute('''CREATE TABLE IF NOT EXISTS parking_lots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        address TEXT DEFAULT '', total_slots INTEGER NOT NULL,
        available_slots INTEGER NOT NULL, owner TEXT NOT NULL,
        price REAL NOT NULL, hours TEXT DEFAULT '8:00 AM - 10:00 PM')''')
    c.execute('''CREATE TABLE IF NOT EXISTS parking_slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, parking_id INTEGER NOT NULL,
        slot_number TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Available')''')
    c.execute('''CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL, customer_name TEXT NOT NULL,
        lot_id INTEGER NOT NULL, lot_name TEXT NOT NULL,
        booking_date TEXT NOT NULL, start_time TEXT NOT NULL,
        end_time TEXT NOT NULL, duration REAL NOT NULL,
        amount REAL NOT NULL, payment_status TEXT DEFAULT 'Paid',
        status TEXT NOT NULL, created_at TEXT NOT NULL,
        arrival_deadline TEXT NOT NULL, extension_used INTEGER DEFAULT 0,
        assigned_slot_id INTEGER, assigned_slot TEXT,
        check_in_time TEXT, check_out_time TEXT,
        refund_amount REAL DEFAULT 0, qr_filename TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id TEXT,
        amount REAL, payment_status TEXT, refund_amount REAL DEFAULT 0,
        created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS waiting_list (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        customer_name TEXT, lot_id INTEGER, lot_name TEXT,
        booking_date TEXT, start_time TEXT, end_time TEXT,
        duration REAL, amount REAL, queue_position INTEGER,
        status TEXT DEFAULT 'Waiting', created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        message TEXT, created_at TEXT, is_read INTEGER DEFAULT 0)''')
    booking_columns = {row[1] for row in c.execute('PRAGMA table_info(bookings)').fetchall()}
    if 'total_fee' not in booking_columns:
        c.execute('ALTER TABLE bookings ADD COLUMN total_fee REAL DEFAULT 0')

    existing_columns = {row[1] for row in c.execute('PRAGMA table_info(waiting_list)').fetchall()}
    for column, definition in (
        ('offered_at', 'TEXT'),
        ('offer_expires_at', 'TEXT'),
        ('booking_id', 'TEXT'),
    ):
        if column not in existing_columns:
            c.execute(f'ALTER TABLE waiting_list ADD COLUMN {column} {definition}')
    c.execute("""UPDATE waiting_list
                 SET offered_at=COALESCE(offered_at, created_at),
                     offer_expires_at=COALESCE(
                         offer_expires_at,
                         datetime(COALESCE(offered_at, created_at), '+15 minutes')
                     )
                 WHERE status='Offered'""")
    conn.commit()
    seed(conn)
    conn.close()


def seed(conn):
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        users = [
            ('Demo Customer','customer@parkify.com','1234','customer'),
            ('Demo Owner','owner@parkify.com','1234','owner'),
            ('Demo Admin','admin@parkify.com','1234','admin')]
        c.executemany('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)', users)
    c.execute('SELECT COUNT(*) FROM parking_lots')
    if c.fetchone()[0] == 0:
        c.execute('''INSERT INTO parking_lots(name,address,total_slots,available_slots,owner,price,hours)
                     VALUES(?,?,?,?,?,?,?)''', ('City Mall Parking','MG Road, Kochi',40,40,'Demo Owner',50,'8:00 AM - 10:00 PM'))
        c.execute('''INSERT INTO parking_lots(name,address,total_slots,available_slots,owner,price,hours)
                     VALUES(?,?,?,?,?,?,?)''', ('Care Hospital Parking','Palarivattom, Kochi',25,25,'Demo Owner',40,'24 Hours'))
        c.execute('''INSERT INTO parking_lots(name,address,total_slots,available_slots,owner,price,hours)
                     VALUES(?,?,?,?,?,?,?)''', ('Grand Theatre Parking','Town Hall Road, Kochi',30,30,'Demo Owner',30,'10:00 AM - 11:00 PM'))
    c.execute('SELECT id FROM parking_lots')
    for row in c.fetchall():
        lot_id = row[0]
        c.execute('SELECT COUNT(*) FROM parking_slots WHERE parking_id=?',(lot_id,))
        if c.fetchone()[0] == 0:
            # 40 -> A1-A20/B1-B20; smaller lots use A-series
            c.execute('SELECT total_slots FROM parking_lots WHERE id=?',(lot_id,))
            total = c.fetchone()[0]
            slots=[]
            for i in range(total):
                letter = chr(65 + i//20)
                num = i%20 + 1
                slots.append((lot_id,f'{letter}{num}','Available'))
            c.executemany('INSERT INTO parking_slots(parking_id,slot_number,status) VALUES(?,?,?)',slots)
    conn.commit()


def add_notification(user_id, message):
    conn = db(); conn.execute('INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)',
                               (user_id,message,now_str())); conn.commit(); conn.close()


def now(): return datetime.now()
def now_str(dt=None): return (dt or now()).strftime('%Y-%m-%d %H:%M:%S')

def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session or (role and session.get('role') != role):
                return redirect(url_for('login_page'))
            return fn(*args, **kwargs)
        return wrapper
    return deco


def get_lot(lot_id):
    conn=db(); row=conn.execute('SELECT * FROM parking_lots WHERE id=?',(lot_id,)).fetchone(); conn.close(); return row


def create_qr(booking_id, user_name, lot_name):
    filename=f'{booking_id}.png'
    qrcode.make(f'PARKIFY|BOOKING:{booking_id}|USER:{user_name}|PARKING:{lot_name}').save(os.path.join(QR_DIR,filename))
    return filename


def _offer_next_waitlist(c, lot_id, booking_date, start_time):
    """Reserve one newly-free space and offer it to the next queued customer."""
    available = c.execute(
        "SELECT available_slots FROM parking_lots WHERE id=?", (lot_id,)
    ).fetchone()
    if not available or available["available_slots"] <= 0:
        return None

    candidate = c.execute(
        """SELECT * FROM waiting_list
           WHERE lot_id=? AND booking_date=? AND start_time=? AND status='Waiting'
           ORDER BY queue_position, id LIMIT 1""",
        (lot_id, booking_date, start_time)
    ).fetchone()
    if not candidate:
        return None

    changed = c.execute(
        "UPDATE parking_lots SET available_slots=available_slots-1 "
        "WHERE id=? AND available_slots>0", (lot_id,)
    ).rowcount
    if not changed:
        return None

    offered_at = now()
    expires_at = offered_at + timedelta(minutes=15)
    c.execute(
        """UPDATE waiting_list
           SET status='Offered', offered_at=?, offer_expires_at=?
           WHERE id=? AND status='Waiting'""",
        (now_str(offered_at), now_str(expires_at), candidate["id"])
    )
    c.execute(
        "INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)",
        (candidate["user_id"],
         f'Parking is available at {candidate["lot_name"]}. Accept and complete demo payment within 15 minutes (until {expires_at.strftime("%H:%M:%S")}).',
         now_str(offered_at))
    )
    return candidate["id"]


def promote_waitlist(lot_id, booking_date, start_time):
    """Offer one available space to the first eligible waiting-list customer."""
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        offered_id = _offer_next_waitlist(conn.cursor(), lot_id, booking_date, start_time)
        conn.commit()
        return offered_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cleanup():
    """Expire offers and no-shows, release capacity, then promote the next queue."""
    conn = db()
    released_groups = []
    current = now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        c = conn.cursor()

        expired = c.execute(
            """SELECT * FROM waiting_list
               WHERE status='Offered' AND offer_expires_at IS NOT NULL
               AND offer_expires_at<=?""",
            (now_str(current),)
        ).fetchall()
        for w in expired:
            changed = c.execute(
                "UPDATE waiting_list SET status='Expired' WHERE id=? AND status='Offered'",
                (w["id"],)
            ).rowcount
            if changed:
                c.execute(
                    "UPDATE parking_lots SET available_slots=MIN(total_slots,available_slots+1) WHERE id=?",
                    (w["lot_id"],)
                )
                released_groups.append((w["lot_id"], w["booking_date"], w["start_time"]))
                c.execute(
                    "INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)",
                    (w["user_id"], "Your 15-minute waiting-list offer expired. The space was offered to the next customer.", now_str(current))
                )

        pending = c.execute("SELECT * FROM bookings WHERE status='Confirmed'").fetchall()
        for b in pending:
            deadline = datetime.strptime(b["arrival_deadline"], "%Y-%m-%d %H:%M:%S")
            if current > deadline:
                changed = c.execute(
                    "UPDATE bookings SET status='No-Show' WHERE id=? AND status='Confirmed'",
                    (b["id"],)
                ).rowcount
                if changed:
                    c.execute(
                        "UPDATE parking_lots SET available_slots=MIN(total_slots,available_slots+1) WHERE id=?",
                        (b["lot_id"],)
                    )
                    c.execute(
                        "INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)",
                        (b["user_id"], "Your parking reservation expired as a no-show. No refund is provided.", now_str(current))
                    )
                    released_groups.append((b["lot_id"], b["booking_date"], b["start_time"]))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for lot_id, booking_date, start_time in released_groups:
        promote_waitlist(lot_id, booking_date, start_time)


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/register', methods=['POST'])
def register():
    name=request.form.get('name','').strip(); email=request.form.get('email','').strip().lower(); password=request.form.get('password',''); role=request.form.get('role','customer')
    if role not in ('customer','owner'): role='customer'
    try:
        conn=db(); conn.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)',(name,email,password,role)); conn.commit(); conn.close()
        flash('Registration successful. Please log in.','success'); return redirect(url_for('login_page'))
    except sqlite3.IntegrityError:
        flash('Email already registered.','danger'); return redirect(url_for('home'))

@app.route('/login_page')
def login_page(): return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
    conn=db(); user=conn.execute('SELECT * FROM users WHERE email=? AND password=?',(email,password)).fetchone(); conn.close()
    if not user:
        flash('Invalid email or password.','danger'); return redirect(url_for('login_page'))
    session.update(user_id=user['id'],name=user['name'],role=user['role'])
    return redirect(url_for('admin_dashboard' if user['role']=='admin' else 'owner_dashboard' if user['role']=='owner' else 'dashboard'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

@app.route('/dashboard')
@login_required('customer')
def dashboard():
    cleanup()
    selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    try:
        selected_hour = int(request.args.get('hour', datetime.now().hour))
        if not 0 <= selected_hour <= 23:
            raise ValueError
        forecast_day = datetime.strptime(selected_date, '%Y-%m-%d').weekday()
    except (ValueError, TypeError):
        selected_date = datetime.now().strftime('%Y-%m-%d')
        selected_hour = datetime.now().hour
        forecast_day = datetime.now().weekday()
    conn=db()
    lots=conn.execute('SELECT * FROM parking_lots').fetchall()
    unread=conn.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0',(session['user_id'],)).fetchone()[0]
    conn.close()
    insights=[]
    for lot in lots:
        forecast = predict_demand(lot, selected_hour, forecast_day)
        insights.append({'lot': lot, **forecast})
    # Alternatives favour lower predicted demand, more available capacity, then lower price.
    alternatives = sorted(insights, key=lambda x: (x['class_id'], -int(x['lot']['available_slots']), float(x['lot']['price'])))
    return render_template('dashboard.html', lots=lots, unread=unread, user_name=session['name'],
                           insights=insights, alternatives=alternatives, selected_date=selected_date,
                           selected_hour=selected_hour, forecast_day=forecast_day)

@app.route('/book/<int:lot_id>', methods=['GET','POST'])
@login_required('customer')
def book(lot_id):
    cleanup(); lot=get_lot(lot_id)
    if not lot: return redirect(url_for('dashboard'))
    if request.method=='GET': return render_template('booking.html',lot=lot)
    date=request.form.get('booking_date'); start=request.form.get('start_time'); duration=float(request.form.get('duration',1))
    if not date or not start or duration<=0: flash('Please enter valid booking details.','danger'); return redirect(url_for('book',lot_id=lot_id))
    try: start_dt=datetime.strptime(f'{date} {start}','%Y-%m-%d %H:%M'); end_dt=start_dt+timedelta(hours=duration)
    except ValueError: flash('Invalid date or time.','danger'); return redirect(url_for('book',lot_id=lot_id))
    amount=round(lot['price']*duration,2)
    conn=db(); c=conn.cursor()
    # For prototype: capacity is reserved against the selected lot. No slot is assigned.
    if lot['available_slots'] <= 0:
        count=c.execute("SELECT COALESCE(MAX(queue_position),0)+1 FROM waiting_list WHERE lot_id=? AND booking_date=? AND start_time=? AND status IN ('Waiting','Offered')",(lot_id,date,start)).fetchone()[0]
        c.execute('''INSERT INTO waiting_list(user_id,customer_name,lot_id,lot_name,booking_date,start_time,end_time,duration,amount,queue_position,status,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(session['user_id'],session['name'],lot_id,lot['name'],date,start,end_dt.strftime('%H:%M'),duration,amount,count,'Waiting',now_str()))
        conn.commit(); conn.close(); flash(f'Parking is full. You are # {count} on the waiting list.','warning'); return redirect(url_for('my_bookings'))
    # Prevent obvious duplicate active booking for same user/lot/date/time.
    existing=c.execute("SELECT id FROM bookings WHERE user_id=? AND lot_id=? AND booking_date=? AND start_time=? AND status IN ('Confirmed','Checked-In')",(session['user_id'],lot_id,date,start)).fetchone()
    if existing:
        conn.close(); flash('You already have an active booking for this parking facility at this time.','danger'); return redirect(url_for('my_bookings'))
    booking_id='PKF-'+uuid.uuid4().hex[:7].upper()
    deadline=start_dt+timedelta(minutes=15)
    qr_filename=create_qr(booking_id,session['name'],lot['name'])
    c.execute('UPDATE parking_lots SET available_slots=available_slots-1 WHERE id=? AND available_slots>0',(lot_id,))
    c.execute('''INSERT INTO bookings(booking_id,user_id,customer_name,lot_id,lot_name,booking_date,start_time,end_time,duration,amount,payment_status,status,created_at,arrival_deadline,qr_filename)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(booking_id,session['user_id'],session['name'],lot_id,lot['name'],date,start,end_dt.strftime('%H:%M'),duration,amount,'Paid','Confirmed',now_str(),now_str(deadline),qr_filename))
    c.execute('INSERT INTO payments(booking_id,amount,payment_status,created_at) VALUES(?,?,?,?)',(booking_id,amount,'Paid (Demo)',now_str()))
    conn.commit(); conn.close(); add_notification(session['user_id'],f'Booking {booking_id} confirmed. QR is ready; slot will be assigned on arrival.')
    return redirect(url_for('booking_success',booking_id=booking_id))

@app.route('/booking/<booking_id>')
@login_required('customer')
def booking_success(booking_id):
    conn=db(); b=conn.execute('SELECT * FROM bookings WHERE booking_id=? AND user_id=?',(booking_id,session['user_id'])).fetchone(); conn.close()
    if not b: return redirect(url_for('my_bookings'))
    return render_template('booking_success.html',b=b)

@app.route('/my_bookings')
@login_required('customer')
def my_bookings():
    cleanup()
    conn=db()
    bookings=conn.execute('SELECT * FROM bookings WHERE user_id=? ORDER BY id DESC',(session['user_id'],)).fetchall()
    raw_waits=conn.execute('SELECT * FROM waiting_list WHERE user_id=? ORDER BY id DESC',(session['user_id'],)).fetchall()
    conn.close()
    waits=[]
    for row in raw_waits:
        item=dict(row)
        if item['status']=='Offered':
            expiry=item.get('offer_expires_at') or ''
            action_url=url_for('waiting_offer', wait_id=item['id'])
            item['status']=Markup(
                f'Offered — <a href="{action_url}"><strong>Accept &amp; Pay</strong></a>'
                + (f' <small>(until {expiry})</small>' if expiry else '')
            )
        waits.append(item)
    return render_template('my_bookings.html',bookings=bookings,waits=waits)


@app.route('/waiting-list/<int:wait_id>/offer', methods=['GET','POST'])
@login_required('customer')
def waiting_offer(wait_id):
    cleanup()
    conn=db()
    w=conn.execute(
        'SELECT * FROM waiting_list WHERE id=? AND user_id=?',
        (wait_id, session['user_id'])
    ).fetchone()
    if not w:
        conn.close()
        flash('Waiting-list entry not found.','danger')
        return redirect(url_for('my_bookings'))

    if request.method == 'GET':
        conn.close()
        if w['status'] != 'Offered':
            flash(f'This waiting-list entry is currently {w["status"]}.','warning')
            return redirect(url_for('my_bookings'))
        expires = w['offer_expires_at'] or ''
        return render_template_string('''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Parkify — Waiting-list offer</title>
<style>body{font-family:Arial,sans-serif;background:#f3f6fb;margin:0;padding:32px;color:#17243a}
.card{max-width:620px;margin:40px auto;background:white;padding:30px;border-radius:16px;box-shadow:0 8px 28px #0001}
button{background:#0d6efd;color:white;border:0;border-radius:8px;padding:13px 22px;font-size:16px;cursor:pointer}
.muted{color:#64748b}.amount{font-size:24px;font-weight:bold}</style></head><body><main class="card">
<h1>Parking space available!</h1><p class="muted">Your waiting-list offer is active for 15 minutes.</p>
<h2>{{ w["lot_name"] }}</h2><p>{{ w["booking_date"] }} · {{ w["start_time"] }}–{{ w["end_time"] }}</p>
<p class="amount">₹{{ "%.2f"|format(w["amount"]) }}</p><p><strong>Offer expires:</strong> {{ expires }}</p>
<p class="muted">Accepting creates a confirmed booking and a demo payment record. No physical slot is assigned until arrival.</p>
<form method="post"><button type="submit">Accept offer &amp; pay (demo)</button></form>
<p><a href="{{ url_for('my_bookings') }}">Back to My Bookings</a></p></main></body></html>''', w=w, expires=expires)

    expires_text = w['offer_expires_at']
    if w['status'] != 'Offered' or not expires_text or now() >= datetime.strptime(expires_text, '%Y-%m-%d %H:%M:%S'):
        conn.close()
        cleanup()
        flash('This offer has expired. Check your waiting-list status for the next update.','warning')
        return redirect(url_for('my_bookings'))

    start_dt=datetime.strptime(f'{w["booking_date"]} {w["start_time"]}','%Y-%m-%d %H:%M')
    deadline=start_dt+timedelta(minutes=15)
    booking_id='PKF-'+uuid.uuid4().hex[:7].upper()
    qr_filename=create_qr(booking_id,session['name'],w['lot_name'])
    c=conn.cursor()
    changed=c.execute(
        "UPDATE waiting_list SET status='Accepted', booking_id=? WHERE id=? AND user_id=? AND status='Offered'",
        (booking_id, wait_id, session['user_id'])
    ).rowcount
    if not changed:
        conn.close()
        flash('This offer is no longer available.','warning')
        return redirect(url_for('my_bookings'))

    c.execute(
        """INSERT INTO bookings(booking_id,user_id,customer_name,lot_id,lot_name,
           booking_date,start_time,end_time,duration,amount,payment_status,status,
           created_at,arrival_deadline,qr_filename)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (booking_id,session['user_id'],session['name'],w['lot_id'],w['lot_name'],
         w['booking_date'],w['start_time'],w['end_time'],w['duration'],w['amount'],
         'Paid','Confirmed',now_str(),now_str(deadline),qr_filename)
    )
    c.execute(
        'INSERT INTO payments(booking_id,amount,payment_status,created_at) VALUES(?,?,?,?)',
        (booking_id,w['amount'],'Paid (Demo - Waiting List)',now_str())
    )
    conn.commit()
    conn.close()
    add_notification(session['user_id'],f'Waiting-list offer accepted. Booking {booking_id} is confirmed and your QR is ready.')
    flash('Offer accepted! Demo payment recorded and booking confirmed.','success')
    return redirect(url_for('booking_success', booking_id=booking_id))

@app.route('/cancel/<booking_id>',methods=['POST'])
@login_required('customer')
def cancel_booking(booking_id):
    conn=db(); b=conn.execute('SELECT * FROM bookings WHERE booking_id=? AND user_id=?',(booking_id,session['user_id'])).fetchone()
    if not b or b['status'] not in ('Confirmed',):
        conn.close(); flash('This booking cannot be cancelled.','danger'); return redirect(url_for('my_bookings'))
    refund=round(b['amount']*0.70,2)
    c=conn.cursor(); c.execute("UPDATE bookings SET status='Cancelled',refund_amount=? WHERE id=?",(refund,b['id'])); c.execute('UPDATE payments SET payment_status="Partially Refunded",refund_amount=? WHERE booking_id=?',(refund,booking_id)); c.execute('UPDATE parking_lots SET available_slots=MIN(total_slots,available_slots+1) WHERE id=?',(b['lot_id'],)); conn.commit(); conn.close()
    promote_waitlist(b['lot_id'],b['booking_date'],b['start_time']); add_notification(session['user_id'],f'Booking {booking_id} cancelled. Partial refund of ₹{refund} recorded.')
    flash(f'Booking cancelled. Partial refund: ₹{refund}.','success'); return redirect(url_for('my_bookings'))

@app.route('/extend/<booking_id>',methods=['POST'])
@login_required('customer')
def extend_booking(booking_id):
    conn=db(); b=conn.execute('SELECT * FROM bookings WHERE booking_id=? AND user_id=?',(booking_id,session['user_id'])).fetchone()
    if not b or b['status']!='Confirmed' or b['extension_used']:
        conn.close(); flash('Extension is not available for this booking.','danger'); return redirect(url_for('my_bookings'))
    deadline=datetime.strptime(b['arrival_deadline'],'%Y-%m-%d %H:%M:%S')
    if now()>deadline:
        conn.close(); flash('The arrival deadline has already passed.','danger'); return redirect(url_for('my_bookings'))
    new_deadline=deadline+timedelta(minutes=15); conn.execute('UPDATE bookings SET arrival_deadline=?,extension_used=1 WHERE id=?',(now_str(new_deadline),b['id'])); conn.commit(); conn.close(); add_notification(session['user_id'],f'Arrival time extended by 15 minutes for {booking_id}.'); flash('Your arrival deadline was extended by 15 minutes.','success'); return redirect(url_for('my_bookings'))

@app.route('/join_waitlist/<int:lot_id>',methods=['POST'])
@login_required('customer')
def join_waitlist(lot_id):
    # This route exists for the UI; normal booking route also automatically waitlists when full.
    return redirect(url_for('book',lot_id=lot_id))

@app.route('/notifications')
@login_required('customer')
def notifications():
    conn=db(); rows=conn.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC',(session['user_id'],)).fetchall(); conn.execute('UPDATE notifications SET is_read=1 WHERE user_id=?',(session['user_id'],)); conn.commit(); conn.close(); return render_template('notifications.html',notifications=rows)

@app.route('/owner_dashboard')
@login_required('owner')
def owner_dashboard():
    cleanup(); conn=db(); lots=conn.execute('SELECT * FROM parking_lots WHERE owner=?',(session['name'],)).fetchall(); bookings=conn.execute('SELECT * FROM bookings ORDER BY id DESC LIMIT 20').fetchall(); conn.close(); return render_template('owner_dashboard.html',lots=lots,bookings=bookings,user_name=session['name'])

@app.route('/add_parking',methods=['POST'])
@login_required('owner')
def add_parking():
    name=request.form.get('lot_name'); address=request.form.get('address',''); total=int(request.form.get('total_slots',1)); price=float(request.form.get('price',0)); hours=request.form.get('hours','8:00 AM - 10:00 PM')
    conn=db(); c=conn.cursor(); c.execute('INSERT INTO parking_lots(name,address,total_slots,available_slots,owner,price,hours) VALUES(?,?,?,?,?,?,?)',(name,address,total,total,session['name'],price,hours)); lot_id=c.lastrowid
    for i in range(total): c.execute('INSERT INTO parking_slots(parking_id,slot_number,status) VALUES(?,?,?)',(lot_id,f'{chr(65+i//20)}{i%20+1}','Available'))
    conn.commit(); conn.close(); flash('Parking facility added successfully.','success'); return redirect(url_for('owner_dashboard'))

@app.route('/owner/scan',methods=['POST'])
@login_required('owner')
def owner_scan():
    raw=request.form.get('booking_id','').strip().upper(); return process_checkin(raw)

@app.route('/api/scan',methods=['POST'])
@login_required('owner')
def api_scan():
    data=request.get_json() or {}; raw=(data.get('qr_data') or data.get('booking_id') or '').strip().upper()
    booking_id=raw.split('BOOKING:')[-1].split('|')[0].strip() if 'BOOKING:' in raw else raw
    result=assign_or_checkout(booking_id)
    return jsonify(result)


def process_checkin(booking_id):
    result=assign_or_checkout(booking_id)
    flash(result['message'], 'success' if result['success'] else 'danger'); return redirect(url_for('owner_dashboard'))


def assign_or_checkout(booking_id):
    cleanup(); conn=db(); c=conn.cursor(); b=c.execute('SELECT * FROM bookings WHERE booking_id=?',(booking_id,)).fetchone()
    if not b: conn.close(); return {'success':False,'message':'Invalid booking QR / Booking ID.'}
    if b['status']=='Confirmed':
        if now()>datetime.strptime(b['arrival_deadline'],'%Y-%m-%d %H:%M:%S'):
            conn.close(); return {'success':False,'message':'This booking is already a no-show.'}
        slot=c.execute("SELECT * FROM parking_slots WHERE parking_id=? AND status='Available' ORDER BY id LIMIT 1",(b['lot_id'],)).fetchone()
        if not slot:
            conn.close(); return {'success':False,'message':'No physical slot is free right now. Please ask the owner to resolve capacity.'}
        c.execute("UPDATE parking_slots SET status='Occupied' WHERE id=?",(slot['id'],))
        c.execute("UPDATE bookings SET status='Checked-In',assigned_slot_id=?,assigned_slot=?,check_in_time=? WHERE id=?",(slot['id'],slot['slot_number'],now_str(),b['id']))
        conn.commit(); conn.close(); return {'success':True,'action':'CHECK_IN','message':f'QR verified. Slot {slot["slot_number"]} dynamically assigned to {b["booking_id"]}.'}
    if b['status']=='Checked-In':
        slot=b['assigned_slot_id']; rate=get_lot(b['lot_id'])['price']; in_time=datetime.strptime(b['check_in_time'],'%Y-%m-%d %H:%M:%S'); hours=max(1,round((now()-in_time).total_seconds()/3600,2)); fee=round(hours*rate,2)
        c.execute("UPDATE bookings SET status='Checked-Out',check_out_time=?,total_fee=? WHERE id=?",(now_str(),fee,b['id']))
        if slot: c.execute("UPDATE parking_slots SET status='Available' WHERE id=?",(slot,))
        c.execute('UPDATE parking_lots SET available_slots=MIN(total_slots,available_slots+1) WHERE id=?',(b['lot_id'],))
        conn.commit(); conn.close(); promote_waitlist(b['lot_id'],b['booking_date'],b['start_time']); return {'success':True,'action':'CHECK_OUT','message':f'Check-out complete. Final fee: ₹{fee}. Slot {b["assigned_slot"] or "-"} is available again.'}
    conn.close(); return {'success':False,'message':f'Booking is not active. Current status: {b["status"]}.'}

@app.route('/admin_dashboard')
@login_required('admin')
def admin_dashboard():
    conn=db(); stats={
        'users':conn.execute("SELECT COUNT(*) FROM users WHERE role='customer'").fetchone()[0],
        'owners':conn.execute("SELECT COUNT(*) FROM users WHERE role='owner'").fetchone()[0],
        'parking':conn.execute('SELECT COUNT(*) FROM parking_lots').fetchone()[0],
        'bookings':conn.execute('SELECT COUNT(*) FROM bookings').fetchone()[0],
        'revenue':conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_status LIKE 'Paid%'").fetchone()[0]}
    users=conn.execute('SELECT id,name,email,role FROM users ORDER BY id DESC').fetchall(); lots=conn.execute('SELECT * FROM parking_lots').fetchall(); bookings=conn.execute('SELECT * FROM bookings ORDER BY id DESC LIMIT 30').fetchall(); conn.close(); return render_template('admin_dashboard.html',stats=stats,users=users,lots=lots,bookings=bookings)


if __name__=='__main__':
    init_db(reset=False)
    app.run(debug=True)
