import sqlite3, datetime, os

DB = '/tmp/database.db' if os.environ.get('RAILWAY_PUBLIC_DOMAIN') else 'database.db'
conn = sqlite3.connect(DB)
c = conn.cursor()

c.executescript('''
DROP TABLE IF EXISTS owners;
DROP TABLE IF EXISTS shops;
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS orders;

CREATE TABLE owners (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
);

CREATE TABLE shops (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     INTEGER NOT NULL,
    name         TEXT NOT NULL,
    slug         TEXT UNIQUE NOT NULL,
    description  TEXT DEFAULT '',
    kitchen_pin  TEXT DEFAULT '1234',
    is_active    INTEGER DEFAULT 1,
    FOREIGN KEY(owner_id) REFERENCES owners(id)
);

CREATE TABLE items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id      INTEGER NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT DEFAULT '',
    price        REAL NOT NULL,
    category     TEXT DEFAULT 'General',
    image        TEXT DEFAULT NULL,
    is_available INTEGER DEFAULT 1,
    FOREIGN KEY(shop_id) REFERENCES shops(id)
);

CREATE TABLE orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id       INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    phone         TEXT DEFAULT '',
    items         TEXT NOT NULL,
    total         REAL NOT NULL,
    status        TEXT DEFAULT 'received',
    payment       TEXT DEFAULT 'COD',
    token_number  INTEGER,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(shop_id) REFERENCES shops(id)
);
''')

# ── Sample data ───────────────────────────────────────────
c.execute("INSERT INTO owners(name,username,password) VALUES('Ram Kumar','ram','ram123')")
o1 = c.lastrowid
c.execute("INSERT INTO owners(name,username,password) VALUES('Priya Sharma','priya','priya123')")
o2 = c.lastrowid

c.execute("INSERT INTO shops(owner_id,name,slug,description,kitchen_pin) VALUES(?,?,?,?,?)",
          (o1,'Ram Tea Stall','ram-tea-stall','Best chai & snacks in town!','5678'))
s1 = c.lastrowid
c.execute("INSERT INTO shops(owner_id,name,slug,description,kitchen_pin) VALUES(?,?,?,?,?)",
          (o2,'Priya Tiffins','priya-tiffins','Fresh home-cooked meals','9999'))
s2 = c.lastrowid

# Items — no images for sample data (image=None)
items1 = [
    (s1,'Masala Chai',   'Ginger & cardamom',     10.0,'Drinks', None),
    (s1,'Filter Coffee', 'South Indian style',     15.0,'Drinks', None),
    (s1,'Cold Coffee',   'Iced with cream',        30.0,'Drinks', None),
    (s1,'Samosa',        'Crispy potato filling',  12.0,'Snacks', None),
    (s1,'Vada Pav',      'Mumbai street style',    18.0,'Snacks', None),
    (s1,'Bread Omelette','Fluffy eggs on toast',   25.0,'Snacks', None),
    (s1,'Gulab Jamun',   'Soft & syrupy (2 pcs)',  20.0,'Sweets', None),
]
c.executemany(
    "INSERT INTO items(shop_id,name,description,price,category,image) VALUES(?,?,?,?,?,?)",
    items1
)

items2 = [
    (s2,'Dal Rice',  'Home-cooked dal',      45.0,'Meals',  None),
    (s2,'Roti Sabzi','3 rotis + veg curry',  50.0,'Meals',  None),
    (s2,'Curd Rice', 'South Indian classic', 40.0,'Meals',  None),
    (s2,'Lassi',     'Sweet chilled lassi',  20.0,'Drinks', None),
]
c.executemany(
    "INSERT INTO items(shop_id,name,description,price,category,image) VALUES(?,?,?,?,?,?)",
    items2
)

now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
orders = [
    (s1,'Ravi Kumar','9876543210','Masala Chai x2, Samosa x1',32.0,'completed',31,now),
    (s1,'Priya S',   '',         'Cold Coffee x1',            30.0,'ready',    32,now),
    (s1,'Arjun M',   '',         'Vada Pav x2, Chai x1',     46.0,'preparing',33,now),
    (s1,'Sneha R',   '',         'Samosa x2',                 24.0,'received', 34,now),
]
c.executemany(
    "INSERT INTO orders(shop_id,customer_name,phone,items,total,status,token_number,created_at)"
    " VALUES(?,?,?,?,?,?,?,?)",
    orders
)

conn.commit()
conn.close()

print("✅  Database ready!\n")
print("━" * 55)
print("  PORTAL          URL")
print("━" * 55)
print("  Admin         → http://127.0.0.1:5000/admin/login")
print("  Owner (Ram)   → http://127.0.0.1:5000/owner/login")
print("  Kitchen       → http://127.0.0.1:5000/kitchen/login")
print("  Shop          → http://127.0.0.1:5000/shop/ram-tea-stall")
print()
print("  CREDENTIALS")
print("  Admin     admin   / admin123")
print("  Ram       ram     / ram123       kitchen PIN: 5678")
print("  Priya     priya   / priya123     kitchen PIN: 9999")
print("━" * 55)
print()
print("  When you run app.py it will print your PHONE URL.")
print("━" * 55)