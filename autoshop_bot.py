"""
PLESK AUTOSHOP — Système de crédits via Plisio (POLLING)
=========================================================
Lancer : python autoshop_bot.py
Pas de webhook, pas de Flask, pas de port à exposer.
Un thread par facture vérifie le paiement toutes les 30s.
"""

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests, subprocess, datetime, string, random, logging, time, sqlite3, json, threading

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
BOT_TOKEN      = "TON_TOKEN_TELEGRAM_BOT"
PLISIO_API_KEY = "TA_CLE_API_PLISIO"
ADMIN_IDS      = [123456789]
LOG_GROUP_ID   = -100987654321
DB_PATH        = "shop.db"
PLISIO_API     = "https://plisio.net/api/v1"
COOLDOWN_SEC   = 10
POLL_INTERVAL  = 30    # secondes entre chaque vérif Plisio
POLL_TIMEOUT   = 3600  # abandon après 1h

CRYPTOS = {
    "BTC":  "₿ Bitcoin (BTC)",
    "ETH":  "Ξ Ethereum (ETH)",
    "SOL":  "◎ Solana (SOL)",
    "LTC":  "Ł Litecoin (LTC)",
    "USDT": "💵 USDT (ERC-20)",
}

def get_public_ip():
    for url in ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]:
        try: return requests.get(url, timeout=5).text.strip()
        except: continue
    return "127.0.0.1"

SERVER_IP = get_public_ip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# 📦 PLANS
# ==========================================
DEFAULT_PLANS = {
    "plan_7":  {"name": "Pack_7J",  "days": 7,  "price": 10.50, "desc": "1 NDD / 7 Jours",     "emoji": "⚡"},
    "plan_15": {"name": "Pack_15J", "days": 15, "price": 16.00, "desc": "2 NDD / 15 Jours",    "emoji": "🚀"},
    "plan_30": {"name": "Pack_30J", "days": 30, "price": 26.50, "desc": "Illimité / 30 Jours", "emoji": "💎"},
}
PLANS_FILE = "plans.json"

def load_plans():
    try:
        with open(PLANS_FILE) as f: return json.load(f)
    except:
        save_plans(DEFAULT_PLANS); return DEFAULT_PLANS

def save_plans(p):
    with open(PLANS_FILE, "w") as f: json.dump(p, f, indent=2)

def get_plans(): return load_plans()

# ==========================================
# 🗄️ BASE DE DONNÉES
# ==========================================
def init_db():
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS wallets (
        tg_id INTEGER PRIMARY KEY, tg_username TEXT NOT NULL,
        balance REAL DEFAULT 0.0, updated_at TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL,
        tg_username TEXT NOT NULL, amount_eur REAL NOT NULL, amount_crypto REAL NOT NULL,
        currency TEXT NOT NULL, plisio_id TEXT UNIQUE NOT NULL,
        status TEXT DEFAULT 'pending', created_at TEXT NOT NULL, confirmed_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL,
        tg_username TEXT NOT NULL, plan_id TEXT NOT NULL, plan_desc TEXT NOT NULL,
        credits_spent REAL NOT NULL, plesk_domain TEXT NOT NULL, plesk_user TEXT NOT NULL,
        plesk_pass TEXT NOT NULL, purchase_date TEXT NOT NULL, expire_date TEXT NOT NULL,
        active INTEGER DEFAULT 1)""")
    con.commit(); con.close()

def now(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

def db_get_balance(tg_id):
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("SELECT balance FROM wallets WHERE tg_id=?", (tg_id,))
    row = cur.fetchone(); con.close(); return row[0] if row else 0.0

def db_ensure_wallet(tg_id, tg_username):
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO wallets (tg_id,tg_username,balance,updated_at) VALUES (?,?,0.0,?)",
                (tg_id, tg_username, now()))
    cur.execute("UPDATE wallets SET tg_username=? WHERE tg_id=?", (tg_username, tg_id))
    con.commit(); con.close()

def db_add_balance(tg_id, tg_username, amount):
    db_ensure_wallet(tg_id, tg_username)
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("UPDATE wallets SET balance=balance+?, updated_at=? WHERE tg_id=?", (amount, now(), tg_id))
    con.commit(); con.close()

def db_deduct_balance(tg_id, amount):
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("UPDATE wallets SET balance=balance-?, updated_at=? WHERE tg_id=?", (amount, now(), tg_id))
    con.commit(); con.close()

def db_get_all_wallets():
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row; cur = con.cursor()
    cur.execute("SELECT * FROM wallets ORDER BY balance DESC")
    rows = cur.fetchall(); con.close(); return rows

def db_add_transaction(tg_id, tg_username, amount_eur, amount_crypto, currency, plisio_id):
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO transactions (tg_id,tg_username,amount_eur,amount_crypto,currency,plisio_id,status,created_at) VALUES (?,?,?,?,?,?,'pending',?)",
                (tg_id, tg_username, amount_eur, amount_crypto, currency, plisio_id, now()))
    con.commit(); con.close()

def db_get_transaction(plisio_id):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row; cur = con.cursor()
    cur.execute("SELECT * FROM transactions WHERE plisio_id=?", (plisio_id,))
    row = cur.fetchone(); con.close(); return row

def db_confirm_transaction(plisio_id):
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("UPDATE transactions SET status='confirmed', confirmed_at=? WHERE plisio_id=?", (now(), plisio_id))
    con.commit(); con.close()

def db_expire_transaction(plisio_id):
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("UPDATE transactions SET status='expired' WHERE plisio_id=?", (plisio_id,))
    con.commit(); con.close()

def db_get_transactions(tg_id):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row; cur = con.cursor()
    cur.execute("SELECT * FROM transactions WHERE tg_id=? ORDER BY id DESC LIMIT 15", (tg_id,))
    rows = cur.fetchall(); con.close(); return rows

def db_add_order(tg_id, tg_username, plan_id, plan_desc, credits, domain, username, password, expire):
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("INSERT INTO orders (tg_id,tg_username,plan_id,plan_desc,credits_spent,plesk_domain,plesk_user,plesk_pass,purchase_date,expire_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tg_id, tg_username, plan_id, plan_desc, credits, domain, username, password, now(), expire))
    con.commit(); con.close()

def db_get_orders(tg_id):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row; cur = con.cursor()
    cur.execute("SELECT * FROM orders WHERE tg_id=? ORDER BY id DESC", (tg_id,))
    rows = cur.fetchall(); con.close(); return rows

def db_get_all_orders():
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row; cur = con.cursor()
    cur.execute("SELECT * FROM orders ORDER BY id DESC")
    rows = cur.fetchall(); con.close(); return rows

def db_set_order_active(order_id, state):
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("UPDATE orders SET active=? WHERE id=?", (state, order_id))
    con.commit(); con.close()

def db_get_order(order_id):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row; cur = con.cursor()
    cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone(); con.close(); return row

def db_get_by_tg_query(query):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row; cur = con.cursor()
    cur.execute("SELECT * FROM orders WHERE tg_username=? OR CAST(tg_id AS TEXT)=? ORDER BY id DESC", (query, query))
    rows = cur.fetchall(); con.close(); return rows

def db_stats():
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("SELECT COUNT(*), SUM(credits_spent) FROM orders"); o = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM orders WHERE active=1"); active = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*), SUM(amount_eur) FROM transactions WHERE status='confirmed'"); t = cur.fetchone()
    cur.execute("SELECT plan_desc, COUNT(*) FROM orders GROUP BY plan_desc"); breakdown = cur.fetchall()
    con.close()
    return {"orders_total": o[0] or 0, "orders_revenue": o[1] or 0.0, "orders_active": active,
            "topups_total": t[0] or 0, "topups_revenue": t[1] or 0.0, "breakdown": breakdown}

# ==========================================
# 🛠️ UTILITAIRES
# ==========================================
_cooldown: dict[int, float] = {}

def gen_password(n=12): return ''.join(random.choices(string.ascii_letters + string.digits + "!@#$", k=n))
def gen_uid(n=6): return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))
def is_admin(uid): return uid in ADMIN_IDS
def has_username(u): return bool(u.username)

def is_rate_limited(uid):
    t = time.time()
    if t - _cooldown.get(uid, 0) < COOLDOWN_SEC: return True
    _cooldown[uid] = t; return False

def send_log(text):
    try: bot.send_message(LOG_GROUP_ID, text, parse_mode="Markdown")
    except Exception as e: logger.error(f"Log groupe : {e}")

def order_card(row, show_creds=False):
    status = "✅ Actif" if row["active"] else "❌ Inactif"
    card = (f"━━━━━━━━━━━━━━━━━━━━\n🆔 *ID :* `{row['id']}`\n"
            f"👤 *Telegram :* @{row['tg_username']} (`{row['tg_id']}`)\n"
            f"📦 *Offre :* {row['plan_desc']}\n💳 *Crédits :* `{row['credits_spent']:.2f} €`\n"
            f"📅 *Achat :* {row['purchase_date']}\n⏰ *Expire :* {row['expire_date']}\n"
            f"🌐 *Domaine :* `{row['plesk_domain']}`\n🔖 *Statut :* {status}\n")
    if show_creds:
        card += (f"👤 *Login :* `{row['plesk_user']}`\n🔑 *Pass :* `{row['plesk_pass']}`\n"
                 f"🖥️ *Panel :* `https://{SERVER_IP}:8443`\n")
    return card

# ==========================================
# 💳 PLISIO
# ==========================================
def plisio_create_invoice(amount_eur, currency, order_id):
    try:
        params = {"api_key": PLISIO_API_KEY, "currency": currency, "source_currency": "EUR",
                  "source_amount": str(amount_eur), "order_number": order_id,
                  "order_name": f"Recharge {amount_eur:.2f} credits",
                  "success_url": f"https://t.me/{bot.get_me().username}",
                  "cancel_url":  f"https://t.me/{bot.get_me().username}"}
        res = requests.get(f"{PLISIO_API}/invoices/new", params=params, timeout=15).json()
        if res.get("status") == "success":
            d = res["data"]
            return d.get("invoice_url"), d.get("txn_id"), float(d.get("invoice_total_sum", 0))
        logger.warning(f"Plisio invoice echec : {res}")
    except Exception as e: logger.error(f"Plisio create error : {e}")
    return None, None, None

def plisio_check_transaction(plisio_id):
    """Appelle GET /transactions/{id} et retourne le statut Plisio."""
    try:
        res = requests.get(f"{PLISIO_API}/transactions/{plisio_id}",
                           params={"api_key": PLISIO_API_KEY}, timeout=10).json()
        if res.get("status") == "success":
            return res["data"].get("status")
        logger.warning(f"Plisio check echec : {res}")
    except Exception as e: logger.error(f"Plisio check error : {e}")
    return None

# ==========================================
# 🔄 POLLING — Un thread par facture
# ==========================================
def poll_transaction(plisio_id):
    """
    Vérifie toutes les POLL_INTERVAL secondes si la facture est payée.
    Crédite le compte dès que status == 'completed'.
    """
    start = time.time()
    while time.time() - start < POLL_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        txn = db_get_transaction(plisio_id)
        if not txn or txn["status"] == "confirmed":
            return

        status = plisio_check_transaction(plisio_id)
        logger.info(f"Polling {plisio_id} → {status}")

        if status == "completed":
            db_confirm_transaction(plisio_id)
            db_add_balance(txn["tg_id"], txn["tg_username"], txn["amount_eur"])
            new_bal = db_get_balance(txn["tg_id"])
            logger.info(f"✅ Crédit +{txn['amount_eur']}€ → @{txn['tg_username']} (solde: {new_bal:.2f}€)")
            try:
                bot.send_message(txn["tg_id"],
                    f"✅ *Paiement confirmé !*\n\n"
                    f"💳 *+{txn['amount_eur']:.2f} crédits* ajoutés\n"
                    f"💰 *Solde actuel :* `{new_bal:.2f} €`\n\n"
                    f"Vous pouvez maintenant acheter un hébergement Plesk 🚀",
                    parse_mode="Markdown")
            except Exception as e: logger.error(f"Notif échouée : {e}")
            send_log(f"💰 *RECHARGE CONFIRMÉE*\n👤 @{txn['tg_username']} (`{txn['tg_id']}`)\n"
                     f"💳 +{txn['amount_eur']:.2f} € via {txn['currency']}\n"
                     f"💰 Nouveau solde : {new_bal:.2f} €\n🆔 `{plisio_id}`")
            return

        elif status in ("expired", "cancelled", "error"):
            db_expire_transaction(plisio_id)
            try:
                bot.send_message(txn["tg_id"],
                    f"❌ *Paiement {status}*\n\nLa facture n'a pas été réglée.\n"
                    f"Recommencez une recharge si besoin.", parse_mode="Markdown")
            except: pass
            return

    # Timeout dépassé sans paiement
    txn = db_get_transaction(plisio_id)
    if txn and txn["status"] == "pending":
        db_expire_transaction(plisio_id)
        logger.info(f"Polling timeout (1h) : {plisio_id}")

def start_polling(plisio_id):
    threading.Thread(target=poll_transaction, args=(plisio_id,), daemon=True).start()

# ==========================================
# 🖥️ PROVISIONNEMENT PLESK
# ==========================================
def provision_plesk(plan):
    uid = gen_uid(); domain = f"client-{uid}.local"; username = f"user_{uid}"; password = gen_password()
    expire = (datetime.datetime.now() + datetime.timedelta(days=plan["days"])).strftime("%Y-%m-%d")
    cmd = ["plesk", "bin", "subscription", "--create", domain, "-owner", "admin",
           "-service-plan", plan["name"], "-ip", SERVER_IP, "-login", username,
           "-passwd", password, "-expire", expire]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
        return {"domain": domain, "username": username, "password": password, "expire": expire}
    except subprocess.CalledProcessError as e: logger.error(f"Plesk error : {e.stderr}")
    except subprocess.TimeoutExpired: logger.error("Plesk timeout")
    return None

# ==========================================
# 🏠 MENUS
# ==========================================
def main_menu_markup():
    m = InlineKeyboardMarkup(row_width=2)
    m.add(InlineKeyboardButton("🛒 Acheter hébergement", callback_data="menu_shop"),
          InlineKeyboardButton("💳 Recharger solde",     callback_data="menu_topup"),
          InlineKeyboardButton("📦 Mes commandes",       callback_data="my_orders"),
          InlineKeyboardButton("🧾 Mes transactions",    callback_data="my_transactions"),
          InlineKeyboardButton("💰 Mon solde",           callback_data="my_balance"),
          InlineKeyboardButton("ℹ️ À propos",            callback_data="menu_about"))
    return m

@bot.message_handler(commands=['start'])
def send_main_menu(message):
    if not has_username(message.from_user):
        return bot.send_message(message.chat.id,
            "⚠️ *Un @username Telegram est obligatoire.*\n\n"
            "➡️ Paramètres → Modifier le profil → Nom d'utilisateur",
            parse_mode="Markdown")
    db_ensure_wallet(message.from_user.id, message.from_user.username)
    balance = db_get_balance(message.from_user.id)
    bot.send_message(message.chat.id,
        f"👋 *Bienvenue sur PLESK AUTOSHOP* 🛡️\n\n💰 *Votre solde :* `{balance:.2f} €`\n\n"
        f"⚡ Hébergement web professionnel\n💳 Rechargez en crypto · Payez en crédits\n"
        f"🚀 Livraison instantanée\n\nQue souhaitez-vous faire ?",
        reply_markup=main_menu_markup(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "back_main")
def back_main(call):
    bot.answer_callback_query(call.id); send_main_menu(call.message)

@bot.callback_query_handler(func=lambda c: c.data == "menu_about")
def menu_about(call):
    bot.answer_callback_query(call.id)
    m = InlineKeyboardMarkup(); m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.send_message(call.message.chat.id,
        f"ℹ️ *PLESK AUTOSHOP*\n\n🖥️ Hébergement Plesk clé en main\n💳 *1 crédit = 1 €*\n\n"
        f"Recharges : ₿ BTC · Ξ ETH · ◎ SOL · Ł LTC · 💵 USDT\n\n"
        f"🔒 Paiements via Plisio\n🖥️ Serveur : `{SERVER_IP}`",
        reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "my_balance")
def my_balance(call):
    db_ensure_wallet(call.from_user.id, call.from_user.username)
    balance = db_get_balance(call.from_user.id)
    m = InlineKeyboardMarkup(row_width=2)
    m.add(InlineKeyboardButton("💳 Recharger", callback_data="menu_topup"),
          InlineKeyboardButton("⬅️ Retour",    callback_data="back_main"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id,
        f"💰 *Votre solde*\n\n💳 Crédits : `{balance:.2f} €`\n\n_1 crédit = 1 €_",
        reply_markup=m, parse_mode="Markdown")

# ==========================================
# 💳 RECHARGE
# ==========================================
TOPUP_AMOUNTS = [5, 10, 20, 50, 100]

@bot.callback_query_handler(func=lambda c: c.data == "menu_topup")
def menu_topup(call):
    bot.answer_callback_query(call.id)
    m = InlineKeyboardMarkup(row_width=3)
    for amt in TOPUP_AMOUNTS:
        m.add(InlineKeyboardButton(f"💳 {amt} €", callback_data=f"topup_amt_{amt}"))
    m.add(InlineKeyboardButton("✏️ Montant personnalisé", callback_data="topup_custom"))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.send_message(call.message.chat.id,
        "💳 *RECHARGER LE SOLDE*\n\n1 crédit = 1 €\nChoisissez le montant :",
        reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "topup_custom")
def topup_custom(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id,
        "✏️ Entrez le montant en € (minimum 1€) :\n\n_Exemple : 25_", parse_mode="Markdown")
    bot.register_next_step_handler(msg, topup_custom_amount)

def topup_custom_amount(message):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount < 1: return bot.reply_to(message, "❌ Minimum 1€.")
        choose_crypto_topup(message, amount)
    except ValueError: bot.reply_to(message, "❌ Entrez un nombre valide.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("topup_amt_"))
def topup_amount(call):
    bot.answer_callback_query(call.id)
    choose_crypto_topup(call.message, float(call.data.split("_")[2]))

def choose_crypto_topup(message, amount):
    m = InlineKeyboardMarkup(row_width=1)
    for code, label in CRYPTOS.items():
        m.add(InlineKeyboardButton(label, callback_data=f"topup_pay_{amount}_{code}"))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="menu_topup"))
    bot.send_message(message.chat.id, f"💳 *Recharge de {amount:.2f} €*\n\nChoisissez votre crypto :",
        reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("topup_pay_"))
def topup_pay(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    if is_rate_limited(call.from_user.id):
        return bot.answer_callback_query(call.id, "⏳ Patientez.", show_alert=True)

    parts = call.data.split("_")   # topup_pay_<amount>_<currency>
    amount = float(parts[2]); currency = parts[3]
    order_id = f"topup-{call.from_user.id}-{int(time.time())}"
    bot.answer_callback_query(call.id, "⏳ Génération de la facture...")

    invoice_url, plisio_id, amount_crypto = plisio_create_invoice(amount, currency, order_id)
    if not invoice_url:
        return bot.send_message(call.message.chat.id,
            "❌ Service de paiement indisponible. Réessayez dans quelques minutes.")

    db_add_transaction(call.from_user.id, call.from_user.username,
                       amount, amount_crypto, currency, plisio_id)

    # Lance la vérification automatique en arrière-plan
    start_polling(plisio_id)

    m = InlineKeyboardMarkup(row_width=1)
    m.add(InlineKeyboardButton(f"💸 Payer {amount_crypto} {currency}", url=invoice_url))
    m.add(InlineKeyboardButton("⬅️ Retour au menu", callback_data="back_main"))
    bot.send_message(call.message.chat.id,
        f"🧾 *FACTURE CRÉÉE*\n\n"
        f"💳 *Recharge :* `{amount:.2f} €`\n"
        f"🔗 *Crypto :* {CRYPTOS.get(currency, currency)}\n"
        f"💰 *À envoyer :* `{amount_crypto}` {currency}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👆 Cliquez pour payer\n"
        f"✅ Crédité *automatiquement* dès confirmation\n"
        f"⏱️ Vérification toutes les {POLL_INTERVAL}s · expire après 1h\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 *Réf :* `{plisio_id}`",
        reply_markup=m, parse_mode="Markdown")

# ==========================================
# 🛒 SHOP
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "menu_shop")
def show_shop(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    db_ensure_wallet(call.from_user.id, call.from_user.username)
    balance = db_get_balance(call.from_user.id); plans = get_plans()
    m = InlineKeyboardMarkup(row_width=1)
    for pid, plan in plans.items():
        suffix = " ✅" if balance >= plan["price"] else " ❌ Solde insuff."
        m.add(InlineKeyboardButton(
            f"{plan['emoji']} {plan['days']}J — {plan['price']:.2f}€ — {plan['desc']}{suffix}",
            callback_data=f"buy_{pid}"))
    m.add(InlineKeyboardButton("💳 Recharger le solde", callback_data="menu_topup"))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.send_message(call.message.chat.id,
        f"🛒 *CATALOGUE*\n\n💰 Votre solde : `{balance:.2f} €`\n\n"
        f"✅ = solde suffisant · ❌ = recharge nécessaire",
        reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def buy_plan(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    plan_id = call.data[4:]; plans = get_plans()
    if plan_id not in plans:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)
    plan = plans[plan_id]; balance = db_get_balance(call.from_user.id)
    if balance < plan["price"]:
        missing = plan["price"] - balance
        m = InlineKeyboardMarkup(row_width=1)
        m.add(InlineKeyboardButton(f"💳 Recharger {missing:.2f}€", callback_data="menu_topup"))
        m.add(InlineKeyboardButton("⬅️ Retour", callback_data="menu_shop"))
        return bot.answer_callback_query(call.id), bot.send_message(call.message.chat.id,
            f"❌ *Solde insuffisant*\n\n💰 Solde : `{balance:.2f} €`\n"
            f"💳 Prix : `{plan['price']:.2f} €`\n📉 Manque : `{missing:.2f} €`",
            reply_markup=m, parse_mode="Markdown")
    m = InlineKeyboardMarkup(row_width=2)
    m.add(InlineKeyboardButton("✅ Confirmer l'achat", callback_data=f"confirm_{plan_id}"),
          InlineKeyboardButton("❌ Annuler",           callback_data="menu_shop"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id,
        f"🛒 *CONFIRMATION*\n\n📦 *Offre :* {plan['desc']}\n⏰ *Durée :* {plan['days']} jours\n"
        f"💳 *Coût :* `{plan['price']:.2f} crédits`\n"
        f"💰 *Solde après :* `{balance - plan['price']:.2f} €`\n\nConfirmer ?",
        reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("confirm_"))
def confirm_buy(call):
    plan_id = call.data[8:]; plans = get_plans()
    if plan_id not in plans:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)
    plan = plans[plan_id]; balance = db_get_balance(call.from_user.id)
    if balance < plan["price"]:
        return bot.answer_callback_query(call.id, "❌ Solde insuffisant.", show_alert=True)
    if is_rate_limited(call.from_user.id):
        return bot.answer_callback_query(call.id, "⏳ Patientez.", show_alert=True)
    bot.answer_callback_query(call.id, "⏳ Création de votre espace...")
    bot.edit_message_text("⏳ *Provisionnement en cours...*",
        call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    db_deduct_balance(call.from_user.id, plan["price"])
    infos = provision_plesk(plan)
    if infos:
        db_add_order(call.from_user.id, call.from_user.username, plan_id, plan["desc"],
                     plan["price"], infos["domain"], infos["username"], infos["password"], infos["expire"])
        new_balance = db_get_balance(call.from_user.id)
        m = InlineKeyboardMarkup(row_width=1)
        m.add(InlineKeyboardButton("📦 Mes commandes", callback_data="my_orders"),
              InlineKeyboardButton("💰 Mon solde",     callback_data="my_balance"),
              InlineKeyboardButton("🏠 Menu",          callback_data="back_main"))
        bot.send_message(call.message.chat.id,
            f"🎉 *SERVEUR LIVRÉ !*\n\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥️ *Panel :* `https://{SERVER_IP}:8443`\n\n"
            f"👤 *Login :* `{infos['username']}`\n🔑 *Pass :* `{infos['password']}`\n"
            f"📅 *Expire :* `{infos['expire']}`\n━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 *Crédits restants :* `{new_balance:.2f} €`",
            reply_markup=m, parse_mode="Markdown")
        send_log(f"🛒 *NOUVELLE VENTE*\n👤 @{call.from_user.username} (`{call.from_user.id}`)\n"
                 f"📦 {plan['desc']}\n💳 -{plan['price']:.2f} crédits\n📅 Expire : {infos['expire']}")
    else:
        db_add_balance(call.from_user.id, call.from_user.username, plan["price"])
        bot.send_message(call.message.chat.id, "⚠️ Erreur serveur. Vos crédits ont été remboursés.")
        send_log(f"⚠️ *ERREUR PLESK*\n@{call.from_user.username} — Remboursé : {plan['price']:.2f}€")

# ==========================================
# 📦 COMMANDES & TRANSACTIONS
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "my_orders")
def my_orders(call):
    rows = db_get_orders(call.from_user.id)
    if not rows: return bot.answer_callback_query(call.id, "📭 Aucune commande.", show_alert=True)
    text = "📦 *Vos commandes :*\n\n"
    for row in rows:
        s = "✅ Actif" if row["active"] else "❌ Expiré"
        text += (f"{s} *{row['plan_desc']}*\n⏰ Expire : `{row['expire_date']}`\n"
                 f"🖥️ `https://{SERVER_IP}:8443`\n"
                 f"👤 `{row['plesk_user']}` · 🔑 `{row['plesk_pass']}`\n\n")
    m = InlineKeyboardMarkup(); m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "my_transactions")
def my_transactions(call):
    rows = db_get_transactions(call.from_user.id)
    if not rows: return bot.answer_callback_query(call.id, "📭 Aucune transaction.", show_alert=True)
    text = "🧾 *Vos transactions :*\n\n"
    for row in rows:
        icon = "✅" if row["status"] == "confirmed" else ("❌" if row["status"] == "expired" else "⏳")
        text += (f"{icon} *+{row['amount_eur']:.2f} €* via `{row['currency']}`\n"
                 f"   📅 {row['created_at']} · `{row['status']}`\n"
                 f"   🆔 `{row['plisio_id']}`\n\n")
    m = InlineKeyboardMarkup(); m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

# ==========================================
# 👑 ADMIN
# ==========================================
def admin_markup():
    m = InlineKeyboardMarkup(row_width=2)
    m.add(InlineKeyboardButton("👥 Commandes",          callback_data="adm_orders_0"),
          InlineKeyboardButton("📊 Statistiques",       callback_data="adm_stats"),
          InlineKeyboardButton("💰 Soldes clients",     callback_data="adm_wallets"),
          InlineKeyboardButton("🔍 Chercher client",    callback_data="adm_search"),
          InlineKeyboardButton("✏️ Modifier les prix",  callback_data="adm_prices"),
          InlineKeyboardButton("💳 Créditer un client", callback_data="adm_credit"))
    return m

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if not is_admin(message.from_user.id): return
    s = db_stats()
    bot.send_message(message.chat.id,
        f"👑 *Panel Admin*\n\n🖥️ Serveur : `{SERVER_IP}`\n"
        f"🛒 Commandes : *{s['orders_total']}* (actives: {s['orders_active']})\n"
        f"💰 CA ventes : *{s['orders_revenue']:.2f} €*\n"
        f"💳 Recharges : *{s['topups_total']}* · *{s['topups_revenue']:.2f} €*",
        reply_markup=admin_markup(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def adm_stats(call):
    if not is_admin(call.from_user.id): return
    s = db_stats(); bd = "\n".join([f"  • {r[0]} : {r[1]}" for r in s["breakdown"]]) or "  Aucune"
    text = (f"📊 *Statistiques*\n\n🛒 Total : *{s['orders_total']}* · Actives : *{s['orders_active']}*\n"
            f"💰 CA : *{s['orders_revenue']:.2f} €*\n\n"
            f"💳 Recharges : *{s['topups_total']}* · *{s['topups_revenue']:.2f} €*\n\n"
            f"📦 *Par offre :*\n{bd}")
    m = InlineKeyboardMarkup(); m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_orders_"))
def adm_orders(call):
    if not is_admin(call.from_user.id): return
    page = int(call.data.split("_")[2]); per_page = 4
    rows = db_get_all_orders(); total = len(rows)
    if total == 0: return bot.answer_callback_query(call.id, "Aucune commande.", show_alert=True)
    start = page * per_page; chunk = rows[start:start + per_page]
    text = f"🛒 *Commandes ({start+1}–{min(start+per_page,total)} / {total})*\n\n"
    for row in chunk: text += order_card(row) + "\n"
    m = InlineKeyboardMarkup(row_width=3); nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm_orders_{page-1}"))
    nav.append(InlineKeyboardButton(f"· {page+1} ·", callback_data="noop"))
    if start + per_page < total: nav.append(InlineKeyboardButton("➡️", callback_data=f"adm_orders_{page+1}"))
    if nav: m.add(*nav)
    m.add(InlineKeyboardButton("🔍 Creds par ID", callback_data="adm_creds_ask"))
    m.add(InlineKeyboardButton("⬅️ Retour",       callback_data="adm_back"))
    try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="Markdown")
    except: bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_wallets")
def adm_wallets(call):
    if not is_admin(call.from_user.id): return
    rows = db_get_all_wallets()
    if not rows: return bot.answer_callback_query(call.id, "Aucun client.", show_alert=True)
    text = "💰 *Soldes clients :*\n\n"
    for row in rows: text += f"@{row['tg_username']} (`{row['tg_id']}`) — `{row['balance']:.2f} €`\n"
    m = InlineKeyboardMarkup(); m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.answer_callback_query(call.id)
    try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="Markdown")
    except: bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_credit")
def adm_credit(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id,
        "💳 *Créditer un client*\n\nEntrez : `@username montant`", parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_credit_do)

def adm_credit_do(message):
    if not is_admin(message.from_user.id): return
    try:
        parts = message.text.strip().split(); username = parts[0].lstrip("@"); amount = float(parts[1])
        con = sqlite3.connect(DB_PATH); cur = con.cursor()
        cur.execute("SELECT tg_id FROM wallets WHERE tg_username=?", (username,))
        row = cur.fetchone(); con.close()
        if not row: return bot.reply_to(message, "❌ Client introuvable.")
        db_add_balance(row[0], username, amount); new_bal = db_get_balance(row[0])
        bot.reply_to(message, f"✅ *+{amount:.2f} €* → @{username}\nSolde : `{new_bal:.2f} €`", parse_mode="Markdown")
        try: bot.send_message(row[0], f"💳 *{amount:.2f} crédits* ajoutés par un admin.\n💰 Solde : `{new_bal:.2f} €`", parse_mode="Markdown")
        except: pass
        send_log(f"💳 *CRÉDIT ADMIN*\n@{username} +{amount:.2f}€ → {new_bal:.2f}€")
    except (IndexError, ValueError):
        bot.reply_to(message, "❌ Format : `@username montant`", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_search")
def adm_search(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id, "🔍 *@username* ou *ID Telegram* :", parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_search_result)

def adm_search_result(message):
    if not is_admin(message.from_user.id): return
    query = message.text.strip().lstrip("@"); orders = db_get_by_tg_query(query)
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row; cur = con.cursor()
    cur.execute("SELECT * FROM wallets WHERE tg_username=? OR CAST(tg_id AS TEXT)=?", (query, query))
    wallet = cur.fetchone(); con.close()
    if not orders and not wallet: return bot.reply_to(message, "❌ Aucun client trouvé.")
    text = f"🔍 *Résultats pour* `{query}` :\n\n"
    if wallet: text += f"💰 *Solde :* `{wallet['balance']:.2f} €`\n\n"
    for row in orders: text += order_card(row, show_creds=True) + "\n"
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_creds_ask")
def adm_creds_ask(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id, "🔐 ID de la commande :")
    bot.register_next_step_handler(msg, adm_creds_show)

def adm_creds_show(message):
    if not is_admin(message.from_user.id): return
    try:
        row = db_get_order(int(message.text.strip()))
        if not row: return bot.reply_to(message, "❌ Commande introuvable.")
        m = InlineKeyboardMarkup(row_width=2)
        m.add(InlineKeyboardButton("🚫 Désactiver", callback_data=f"adm_off_{row['id']}"),
              InlineKeyboardButton("✅ Réactiver",  callback_data=f"adm_on_{row['id']}"))
        bot.send_message(message.chat.id, order_card(row, show_creds=True), reply_markup=m, parse_mode="Markdown")
    except ValueError: bot.reply_to(message, "❌ Entrez un nombre entier.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_off_") or c.data.startswith("adm_on_"))
def adm_toggle(call):
    if not is_admin(call.from_user.id): return
    parts = call.data.split("_"); action = parts[1]; rid = int(parts[2])
    state = 0 if action == "off" else 1; db_set_order_active(rid, state)
    label = "désactivé ❌" if state == 0 else "réactivé ✅"; bot.answer_callback_query(call.id, f"Compte {label}.")
    row = db_get_order(rid)
    if row: bot.edit_message_text(order_card(row, show_creds=True),
        call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_prices")
def adm_prices(call):
    if not is_admin(call.from_user.id): return
    plans = get_plans(); m = InlineKeyboardMarkup(row_width=1)
    for pid, plan in plans.items():
        m.add(InlineKeyboardButton(f"{plan['emoji']} {plan['desc']} — {plan['price']:.2f}€",
                                   callback_data=f"adm_editprice_{pid}"))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    text = "✏️ *Modifier les prix*\n\n" + "\n".join([f"• {p['desc']} : `{p['price']:.2f} €`" for p in plans.values()])
    try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="Markdown")
    except: bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_editprice_"))
def adm_editprice(call):
    if not is_admin(call.from_user.id): return
    plan_id = call.data.replace("adm_editprice_", ""); plans = get_plans(); plan = plans.get(plan_id)
    if not plan: return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)
    msg = bot.send_message(call.message.chat.id,
        f"✏️ Nouveau prix pour *{plan['desc']}* (actuel : `{plan['price']:.2f}€`) :", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda m: adm_saveprice(m, plan_id))

def adm_saveprice(message, plan_id):
    if not is_admin(message.from_user.id): return
    try:
        new_price = float(message.text.strip().replace(",", "."))
        if new_price <= 0: return bot.reply_to(message, "❌ Prix doit être positif.")
        plans = get_plans(); old = plans[plan_id]["price"]; plans[plan_id]["price"] = new_price; save_plans(plans)
        bot.reply_to(message, f"✅ *{plans[plan_id]['desc']}* : `{old:.2f}€` → `{new_price:.2f}€`", parse_mode="Markdown")
        send_log(f"✏️ *PRIX MODIFIÉ*\n{plans[plan_id]['desc']}\n{old:.2f}€ → {new_price:.2f}€")
    except ValueError: bot.reply_to(message, "❌ Entrez un nombre valide.")

@bot.callback_query_handler(func=lambda c: c.data == "adm_back")
def adm_back(call):
    if not is_admin(call.from_user.id): return
    s = db_stats()
    bot.edit_message_text(
        f"👑 *Panel Admin*\n\n🖥️ Serveur : `{SERVER_IP}`\n"
        f"🛒 Commandes : *{s['orders_total']}* (actives: {s['orders_active']})\n"
        f"💰 CA : *{s['orders_revenue']:.2f} €*\n"
        f"💳 Recharges : *{s['topups_total']}* · *{s['topups_revenue']:.2f} €*",
        call.message.chat.id, call.message.message_id, reply_markup=admin_markup(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def noop(call): bot.answer_callback_query(call.id)

# ==========================================
# 🚀 DÉMARRAGE
# ==========================================
if __name__ == "__main__":
    init_db()
    logger.info(f"🚀 Démarrage — IP : {SERVER_IP}")
    logger.info("🔄 Mode polling — pas de webhook, pas de port à ouvrir")
    bot.infinity_polling(timeout=20, long_polling_timeout=15)
