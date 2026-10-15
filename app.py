import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from flask import Flask, Response, abort, flash, redirect, render_template, request, session, url_for
    from werkzeug.utils import secure_filename
except ModuleNotFoundError as error:
    if error.name != "flask":
        raise
    print("Instalando Flask para iniciar a loja...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Flask>=3.0,<4.0"])
    from flask import Flask, Response, abort, flash, redirect, render_template, request, session, url_for


ROOT = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL.removeprefix("postgres://")
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL.removeprefix("postgresql+psycopg://")
USE_POSTGRES = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))
DB_PATH = Path(os.environ.get("SHOP_DB", ROOT / "instance" / "loja.db")).expanduser()
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH
APP_ENV = os.environ.get("APP_ENV", "development").lower()
app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
app.secret_key = os.environ.get("SECRET_KEY") or "pod-rush-local-only-secret-key-change-before-deploy"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = APP_ENV == "production"
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
UPLOAD_DIR = ROOT / "static" / "uploads"


if APP_ENV == "production":
    if len(os.environ.get("SECRET_KEY", "")) < 32:
        raise RuntimeError("Configure SECRET_KEY com pelo menos 32 caracteres aleatórios em produção.")
    if len(os.environ.get("SHOP_ADMIN_PASSWORD", "")) < 14 or os.environ["SHOP_ADMIN_PASSWORD"] == "admin":
        raise RuntimeError("Configure SHOP_ADMIN_PASSWORD com pelo menos 14 caracteres em produção.")
    if not USE_POSTGRES:
        raise RuntimeError("Configure DATABASE_URL para usar PostgreSQL em produção.")


class DatabaseConnection:
    """Pequena camada para usar a mesma API SQL com SQLite e PostgreSQL."""

    def __init__(self, connection, postgres=False):
        self.connection = connection
        self.postgres = postgres

    def execute(self, query, values=()):
        if self.postgres:
            query = query.replace("?", "%s")
        return self.connection.execute(query, values)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type:
                self.connection.rollback()
            else:
                self.connection.commit()
        finally:
            self.connection.close()


def connect_db():
    if USE_POSTGRES:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as error:
            raise RuntimeError("Instale as dependências do requirements.txt para acessar PostgreSQL.") from error
        connection = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        return DatabaseConnection(connection, postgres=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return DatabaseConnection(connection)


def product_rows(query, values=()):
    with connect_db() as db:
        return db.execute(query, values).fetchall()


def seed_db():
    with connect_db() as db:
        id_type = "BIGSERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
        fk_type = "BIGINT" if USE_POSTGRES else "INTEGER"
        binary_type = "BYTEA" if USE_POSTGRES else "BLOB"
        timestamp_column = "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text" if USE_POSTGRES else "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        schema = [f"""CREATE TABLE IF NOT EXISTS products (
          id {id_type},
          name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          category TEXT NOT NULL DEFAULT 'Diversos',
          price INTEGER NOT NULL CHECK(price >= 0),
          compare_price INTEGER,
          stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0),
          image TEXT NOT NULL DEFAULT '',
          flavors TEXT NOT NULL DEFAULT '',
          badge TEXT NOT NULL DEFAULT '',
          featured INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          created_at {timestamp_column}
        )""", """CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )""", f"""CREATE TABLE IF NOT EXISTS orders (
          id {id_type},
          customer_name TEXT NOT NULL,
          email TEXT NOT NULL,
          phone TEXT NOT NULL DEFAULT '',
          address TEXT NOT NULL,
          city TEXT NOT NULL,
          postal_code TEXT NOT NULL,
          notes TEXT NOT NULL DEFAULT '',
          subtotal INTEGER NOT NULL,
          shipping INTEGER NOT NULL DEFAULT 0,
          total INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'Recebido',
          created_at {timestamp_column}
        )""", f"""CREATE TABLE IF NOT EXISTS order_items (
          id {id_type},
          order_id {fk_type} NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
          product_id {fk_type} REFERENCES products(id) ON DELETE SET NULL,
          product_name TEXT NOT NULL,
          unit_price INTEGER NOT NULL,
          quantity INTEGER NOT NULL
        )""", f"""CREATE TABLE IF NOT EXISTS uploaded_images (
          filename TEXT PRIMARY KEY,
          content_type TEXT NOT NULL,
          data {binary_type} NOT NULL
        )"""]
        for statement in schema:
            db.execute(statement)
        if USE_POSTGRES:
            product_columns = {row["column_name"] for row in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'products'").fetchall()}
        else:
            product_columns = {row["name"] for row in db.execute("PRAGMA table_info(products)").fetchall()}
        if "flavors" not in product_columns:
            db.execute("ALTER TABLE products ADD COLUMN flavors TEXT NOT NULL DEFAULT ''")
        if "badge" not in product_columns:
            db.execute("ALTER TABLE products ADD COLUMN badge TEXT NOT NULL DEFAULT ''")
        db.execute("INSERT INTO settings (key, value) VALUES ('whatsapp', '') ON CONFLICT(key) DO NOTHING")
        db.execute("INSERT INTO settings (key, value) VALUES ('store_open', '1') ON CONFLICT(key) DO NOTHING")
        db.execute("INSERT INTO settings (key, value) VALUES ('store_name', 'Pod Rush') ON CONFLICT(key) DO NOTHING")
        db.execute("INSERT INTO settings (key, value) VALUES ('store_tagline', 'Catálogo e estoque em tempo real') ON CONFLICT(key) DO NOTHING")


def cart_state():
    cart = session.get("cart", {})
    if not isinstance(cart, dict):
        cart = {}
    cleaned = {}
    for key, quantity in cart.items():
        try:
            product_id, quantity = int(key), int(quantity)
        except (TypeError, ValueError):
            continue
        if product_id > 0 and quantity > 0:
            cleaned[str(product_id)] = min(quantity, 99)
    session["cart"] = cleaned
    return cleaned


def cart_details():
    cart = cart_state()
    if not cart:
        return [], 0, 0
    ids = [int(key) for key in cart]
    placeholders = ",".join("?" for _ in ids)
    products = product_rows(f"SELECT * FROM products WHERE active = 1 AND id IN ({placeholders})", ids)
    items, subtotal, count = [], 0, 0
    for product in products:
        quantity = cart[str(product["id"])]
        quantity = min(quantity, product["stock"])
        if quantity < 1:
            continue
        line_total = product["price"] * quantity
        items.append({"product": product, "quantity": quantity, "line_total": line_total})
        subtotal += line_total
        count += quantity
    return items, subtotal, count


@app.template_filter("money")
def money(cents):
    return (f"R$ {int(cents or 0) / 100:,.2f}").replace(",", "_").replace(".", ",").replace("_", ".")


@app.context_processor
def common_context():
    _, _, cart_count = cart_details()
    settings = {row["key"]: row["value"] for row in product_rows("SELECT key,value FROM settings")}
    has_products = bool(product_rows("SELECT 1 FROM products WHERE active=1 LIMIT 1"))
    return {"cart_count": cart_count, "has_products": has_products, "admin_logged_in": bool(session.get("shop_admin")), "store_name": settings.get("store_name", "Pod Rush"), "store_tagline": settings.get("store_tagline", "Loja oficial"), "whatsapp": ''.join(ch for ch in settings.get("whatsapp", "") if ch.isdigit()), "store_open": settings.get("store_open", "1") == "1", "today": datetime.now()}


@app.template_filter("product_image")
def product_image(image):
    if image and image.startswith("uploads/") and ".." not in Path(image).parts:
        return url_for("uploaded_product_image", filename=Path(image).name)
    key = image if image in {"headphones", "bottle", "lamp", "bag", "notebook", "camera", "stand", "vase", "abstract"} else "abstract"
    return url_for("product_art", image_key=key)


@app.get("/")
def home():
    q = request.args.get("q", "").strip()[:80]
    sort = request.args.get("sort", "featured")
    category = request.args.get("category", "").strip()[:60]
    where, params = ["active = 1"], []
    if q:
        where.append("(name LIKE ? OR description LIKE ? OR category LIKE ? OR flavors LIKE ?)")
        params.extend([f"%{q}%"] * 4)
    if category:
        where.append("category = ?")
        params.append(category)
    order = {"price_asc": "price ASC", "price_desc": "price DESC", "newest": "id DESC"}.get(sort, "featured DESC, id")
    products = product_rows(f"SELECT * FROM products WHERE {' AND '.join(where)} ORDER BY {order}", params)
    categories = product_rows("SELECT DISTINCT category FROM products WHERE active = 1 ORDER BY category")
    featured = product_rows("SELECT * FROM products WHERE active = 1 AND featured = 1 ORDER BY id LIMIT 4")
    return render_template("store.html", products=products, categories=categories, featured=featured, q=q, category=category, sort=sort, has_products=bool(product_rows("SELECT 1 FROM products WHERE active=1 LIMIT 1")))


@app.get("/produto/<int:product_id>")
def product_page(product_id):
    product = product_rows("SELECT * FROM products WHERE id = ? AND active = 1", (product_id,))
    if not product:
        abort(404)
    related = product_rows("SELECT * FROM products WHERE active = 1 AND id != ? ORDER BY featured DESC, RANDOM() LIMIT 4", (product_id,))
    return render_template("product.html", product=product[0], related=related)


@app.post("/carrinho/adicionar/<int:product_id>")
def add_to_cart(product_id):
    rows = product_rows("SELECT id, name, stock FROM products WHERE id = ? AND active = 1", (product_id,))
    if not rows or rows[0]["stock"] < 1:
        flash("Esse produto não está disponível agora.", "error")
        return redirect(request.referrer or url_for("home"))
    cart = cart_state()
    quantity = max(1, min(int(request.form.get("quantity", 1) or 1), 99))
    cart[str(product_id)] = min(cart.get(str(product_id), 0) + quantity, rows[0]["stock"])
    session["cart"] = cart
    flash(f"{rows[0]['name']} foi adicionado ao carrinho.", "success")
    return redirect(request.referrer or url_for("home"))


@app.get("/carrinho")
def cart_page():
    items, subtotal, count = cart_details()
    return render_template("cart.html", items=items, subtotal=subtotal, count=count)


@app.post("/carrinho/atualizar")
def update_cart():
    current = cart_state()
    updated = {}
    for key, value in request.form.items():
        if not key.startswith("quantity_"):
            continue
        product_id = key.removeprefix("quantity_")
        try:
            quantity = int(value)
        except ValueError:
            continue
        if quantity > 0 and product_id in current:
            product = product_rows("SELECT stock FROM products WHERE id = ? AND active = 1", (int(product_id),))
            if product and product[0]["stock"]:
                updated[product_id] = min(quantity, product[0]["stock"], 99)
    session["cart"] = updated
    flash("Carrinho atualizado.", "success")
    return redirect(url_for("cart_page"))


@app.post("/carrinho/remover/<int:product_id>")
def remove_from_cart(product_id):
    cart = cart_state()
    cart.pop(str(product_id), None)
    session["cart"] = cart
    return redirect(url_for("cart_page"))


@app.route("/finalizar", methods=["GET", "POST"])
def checkout():
    items, subtotal, count = cart_details()
    if not items:
        flash("Seu carrinho está vazio.", "error")
        return redirect(url_for("home"))
    shipping = 0 if subtotal >= 15000 else 1490
    if request.method == "POST":
        fields = {name: request.form.get(name, "").strip() for name in ("customer_name", "email", "phone", "address", "city", "postal_code", "notes")}
        required = ("customer_name", "email", "address", "city", "postal_code")
        if any(not fields[key] for key in required):
            flash("Preencha nome, e-mail e endereço para continuar.", "error")
            return render_template("checkout.html", items=items, subtotal=subtotal, shipping=shipping, total=subtotal + shipping)
        if "@" not in fields["email"] or len(fields["email"]) > 160:
            flash("Confira o e-mail informado.", "error")
            return render_template("checkout.html", items=items, subtotal=subtotal, shipping=shipping, total=subtotal + shipping)
        with connect_db() as db:
            for item in items:
                available = db.execute("SELECT stock FROM products WHERE id = ?", (item["product"]["id"],)).fetchone()
                if not available or available["stock"] < item["quantity"]:
                    flash("O estoque mudou enquanto você comprava. Revise o carrinho.", "error")
                    return redirect(url_for("cart_page"))
            cursor = db.execute("""INSERT INTO orders
              (customer_name, email, phone, address, city, postal_code, notes, subtotal, shipping, total)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""", (*[fields[k] for k in ("customer_name", "email", "phone", "address", "city", "postal_code", "notes")], subtotal, shipping, subtotal + shipping))
            order_id = cursor.fetchone()["id"]
            for item in items:
                product = item["product"]
                db.execute("INSERT INTO order_items (order_id, product_id, product_name, unit_price, quantity) VALUES (?, ?, ?, ?, ?)", (order_id, product["id"], product["name"], product["price"], item["quantity"]))
                db.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (item["quantity"], product["id"]))
        session["cart"] = {}
        return redirect(url_for("order_confirmation", order_id=order_id))
    return render_template("checkout.html", items=items, subtotal=subtotal, shipping=shipping, total=subtotal + shipping)


@app.get("/pedido/<int:order_id>")
def order_confirmation(order_id):
    order = product_rows("SELECT * FROM orders WHERE id = ?", (order_id,))
    if not order:
        abort(404)
    items = product_rows("SELECT * FROM order_items WHERE order_id = ?", (order_id,))
    return render_template("order.html", order=order[0], items=items)


def admin_required():
    if not session.get("shop_admin"):
        flash("Entre no painel para continuar.", "error")
        return False
    return True


@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = os.environ.get("SHOP_ADMIN_PASSWORD", "admin")
        if request.form.get("password", "") == password:
            session["shop_admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Senha incorreta.", "error")
    if session.get("shop_admin"):
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_login.html")


@app.post("/admin/sair")
def admin_logout():
    session.pop("shop_admin", None)
    return redirect(url_for("home"))


@app.get("/admin/loja")
def admin_dashboard():
    if not admin_required():
        return redirect(url_for("admin_login"))
    products = product_rows("SELECT * FROM products ORDER BY active DESC, id DESC")
    orders = product_rows("SELECT * FROM orders ORDER BY id DESC LIMIT 12")
    stats = product_rows("SELECT (SELECT COUNT(*) FROM products WHERE active=1) products, (SELECT COUNT(*) FROM orders) orders, (SELECT COALESCE(SUM(total),0) FROM orders) revenue")[0]
    return render_template("admin.html", products=products, orders=orders, stats=stats)


@app.route("/admin/configuracoes", methods=["GET", "POST"])
def admin_settings():
    if not admin_required():
        return redirect(url_for("admin_login"))
    if request.method == "POST":
        name = request.form.get("store_name", "").strip()[:60]
        tagline = request.form.get("store_tagline", "").strip()[:100]
        whatsapp = ''.join(ch for ch in request.form.get("whatsapp", "") if ch.isdigit())[:15]
        store_open = "1" if request.form.get("store_open") == "on" else "0"
        if not name:
            flash("O nome da loja não pode ficar vazio.", "error")
        else:
            with connect_db() as db:
                db.execute("INSERT INTO settings (key,value) VALUES ('store_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (name,))
                db.execute("INSERT INTO settings (key,value) VALUES ('store_tagline',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (tagline,))
                db.execute("INSERT INTO settings (key,value) VALUES ('whatsapp',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (whatsapp,))
                db.execute("INSERT INTO settings (key,value) VALUES ('store_open',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (store_open,))
            flash("Identidade da loja atualizada.", "success")
            return redirect(url_for("admin_settings"))
    values = {row["key"]: row["value"] for row in product_rows("SELECT key,value FROM settings")}
    return render_template("admin_settings.html", values=values)


@app.route("/admin/produtos/novo", methods=["GET", "POST"])
@app.route("/admin/produtos/<int:product_id>", methods=["GET", "POST"])
def edit_product(product_id=None):
    if not admin_required():
        return redirect(url_for("admin_login"))
    found = product_rows("SELECT * FROM products WHERE id = ?", (product_id,)) if product_id else []
    existing = dict(found[0]) if found else None
    if product_id and existing is None:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            price = round(float(request.form.get("price", "0").replace(",", ".")) * 100)
            compare_input = request.form.get("compare_price", "0").replace(",", ".") or "0"
            compare = round(float(compare_input) * 100) or None
            stock = max(0, int(request.form.get("stock", "0")))
        except ValueError:
            flash("Confira preço, preço anterior e estoque.", "error")
            return render_template("product_form.html", product=existing)
        if not name or price <= 0:
            flash("Nome e preço maior que zero são obrigatórios.", "error")
            return render_template("product_form.html", product=existing)
        image = existing.get("image", "abstract") if existing else "abstract"
        image_file = request.files.get("image_file")
        if image_file and image_file.filename:
            extension = Path(image_file.filename).suffix.lower()
            if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
                flash("Envie uma imagem PNG, JPG ou WEBP.", "error")
                return render_template("product_form.html", product=existing)
            from uuid import uuid4
            filename = secure_filename(f"{uuid4().hex}{extension}")
            content_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[extension]
            with connect_db() as db:
                db.execute("INSERT INTO uploaded_images (filename, content_type, data) VALUES (?, ?, ?) ON CONFLICT(filename) DO UPDATE SET content_type=excluded.content_type, data=excluded.data", (filename, content_type, image_file.read()))
            image = f"uploads/{filename}"
        flavors = "\n".join(dict.fromkeys(value.strip()[:70] for value in request.form.get("flavors", "").replace("\r", "").split("\n") if value.strip()))
        badge = request.form.get("badge", "").strip()[:32]
        values = (name, request.form.get("description", "").strip(), request.form.get("category", "Diversos").strip() or "Diversos", price, compare, stock, image, flavors, badge, int(request.form.get("featured") == "on"), int(request.form.get("active") == "on"))
        with connect_db() as db:
            if existing:
                db.execute("UPDATE products SET name=?, description=?, category=?, price=?, compare_price=?, stock=?, image=?, flavors=?, badge=?, featured=?, active=? WHERE id=?", (*values, product_id))
            else:
                db.execute("INSERT INTO products (name, description, category, price, compare_price, stock, image, flavors, badge, featured, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        flash("Produto salvo.", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("product_form.html", product=existing)


@app.get("/static/img/product-<image_key>.svg")
def product_art(image_key):
    allowed = {"headphones", "bottle", "lamp", "bag", "notebook", "camera", "stand", "vase", "abstract"}
    if image_key not in allowed:
        abort(404)
    from flask import Response
    palettes = {
        "headphones": ("#f2e5d9", "#4f3d45", "#d3957b"), "bottle": ("#dfe5d7", "#abb5a1", "#ea6441"),
        "lamp": ("#f1e1c9", "#d99443", "#bc8144"), "bag": ("#e8dce7", "#80677d", "#bd9aaf"),
        "notebook": ("#eee7d7", "#c87859", "#f0d4bb"), "camera": ("#eee5ce", "#d87150", "#f1dbb8"),
        "stand": ("#e6e7e2", "#85908d", "#bbc8c4"), "vase": ("#eee1d7", "#c49a83", "#e5bea5"),
        "abstract": ("#eee9df", "#d87a56", "#819080"),
    }
    background, main, accent = palettes[image_key]
    if image_key == "headphones":
        shape = f'<path d="M122 125a78 78 0 0 1 156 0" fill="none" stroke="{main}" stroke-width="24" stroke-linecap="round"/><rect x="108" y="122" width="42" height="90" rx="18" fill="{accent}"/><rect x="250" y="122" width="42" height="90" rx="18" fill="{accent}"/>'
    elif image_key == "bottle":
        shape = f'<rect x="163" y="80" width="75" height="190" rx="25" fill="{main}"/><rect x="181" y="58" width="39" height="33" rx="7" fill="{accent}"/><rect x="175" y="150" width="51" height="48" rx="3" fill="#ed6240"/><text x="200" y="180" text-anchor="middle" fill="white" font-size="17" font-family="Arial" font-weight="700">GO</text>'
    elif image_key == "lamp":
        shape = f'<path d="M139 117Q145 56 200 56t61 61z" fill="{main}"/><rect x="193" y="112" width="14" height="125" rx="5" fill="{accent}"/><ellipse cx="200" cy="244" rx="54" ry="12" fill="{accent}"/>'
    elif image_key == "bag":
        shape = f'<path d="M125 135h150l-12 112q-3 17-20 17h-86q-17 0-20-17z" fill="{main}"/><path d="M158 145v-35q0-40 42-40t42 40v35" fill="none" stroke="{main}" stroke-width="18" stroke-linecap="round"/>'
    elif image_key == "notebook":
        shape = f'<g transform="rotate(-8 200 170)"><rect x="139" y="68" width="123" height="202" rx="8" fill="{main}"/><path d="M165 126h70m-70 15h70m-70 15h56" stroke="{accent}" stroke-width="3"/></g>'
    elif image_key == "camera":
        shape = f'<rect x="107" y="110" width="186" height="126" rx="18" fill="{main}"/><path d="M131 110l17-23h50l16 23" fill="{main}"/><circle cx="200" cy="173" r="42" fill="{accent}"/><circle cx="200" cy="173" r="26" fill="#647276"/>'
    elif image_key == "stand":
        shape = f'<g transform="rotate(-5 200 150)"><rect x="112" y="76" width="176" height="115" rx="9" fill="{accent}" stroke="{main}" stroke-width="12"/><path d="M191 191l-23 65m41-65 23 65m-90 0h116" stroke="{main}" stroke-width="12" stroke-linecap="round"/></g>'
    elif image_key == "vase":
        shape = f'<path d="M175 81h50v35q0 13 19 39t14 82q0 42-58 42t-58-42q0-56 20-82t13-39z" fill="{main}"/><path d="M175 81h50" stroke="{accent}" stroke-width="8" stroke-linecap="round"/>'
    else:
        shape = f'<path d="M126 205q-24-58 36-83 20-47 73-23 65 1 46 65 21 55-38 84-53 39-89-1-43 6-28-42z" fill="{main}"/><circle cx="203" cy="167" r="43" fill="{accent}" opacity=".6"/>'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 340"><defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="{background}"/><stop offset="1" stop-color="{accent}" stop-opacity=".3"/></linearGradient><filter id="s"><feDropShadow dx="7" dy="12" stdDeviation="9" flood-opacity=".16"/></filter></defs><path fill="url(#bg)" d="M0 0h400v340H0z"/><circle cx="200" cy="174" r="127" fill="none" stroke="#fff" stroke-opacity=".5"/><text x="17" y="26" font-family="Arial" font-size="9" font-weight="700" letter-spacing="1.2" fill="#666157">POD RUSH SHOP ✳</text><g filter="url(#s)">{shape}</g></svg>'
    return Response(svg, mimetype="image/svg+xml")


@app.post("/admin/produtos/<int:product_id>/excluir")
def delete_product(product_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    with connect_db() as db:
        db.execute("UPDATE products SET active = 0 WHERE id = ?", (product_id,))
    flash("Produto retirado da vitrine.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/pedidos/<int:order_id>/status")
def update_order_status(order_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    status = request.form.get("status", "")
    if status not in {"Recebido", "Em preparação", "Enviado", "Concluído", "Cancelado"}:
        abort(400)
    with connect_db() as db:
        db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    flash("Status do pedido atualizado.", "success")
    return redirect(url_for("admin_dashboard"))


@app.get("/sobre")
def about():
    return render_template("about.html")


@app.get("/uploads/<path:filename>")
def uploaded_product_image(filename):
    safe_name = secure_filename(Path(filename).name)
    if not safe_name or safe_name != filename:
        abort(404)
    images = product_rows("SELECT content_type, data FROM uploaded_images WHERE filename = ?", (safe_name,))
    if images:
        return Response(bytes(images[0]["data"]), mimetype=images[0]["content_type"], headers={"Cache-Control": "public, max-age=31536000, immutable"})
    # Compatibilidade com fotos enviadas antes da migração para armazenamento no banco.
    legacy_file = UPLOAD_DIR / safe_name
    if legacy_file.is_file() and legacy_file.resolve().parent == UPLOAD_DIR.resolve():
        from flask import send_file
        return send_file(legacy_file)
    abort(404)


seed_db()


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=APP_ENV != "production")
