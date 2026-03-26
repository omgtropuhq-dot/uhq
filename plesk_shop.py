"""
PLESK STOCK SHOP — Bot Telegram
================================
• Admin ajoute des comptes Plesk en stock (format user:pass)
• Clients rechargent en crypto via Plisio (polling, pas de webhook)
• Livraison automatique depuis le stock à l'achat
• 1 crédit = 1 €

INSTALL :
    pip install pyTelegramBotAPI requests

CONFIG :
    Remplis BOT_TOKEN, PLISIO_API_KEY, ADMIN_IDS, LOG_GROUP_ID

LANCER :
    python plesk_shop.py
"""

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import datetime
import string
import random
import logging
import time
import sqlite3
import json
import threading

# ==========================================
# ⚙️  CONFIGURATION
# ==========================================
BOT_TOKEN      = "8628435813:AAG-RpDUGSpNaTWbPFgwaQuJe7ffRHY7E24"
PLISIO_API_KEY = "b-WCYaL8vgyJobhvc-0eEt3nnwkHmPkmJhUdXB8JJeYR7DNegbyJpo0Z9ngKATNM"
ADMIN_IDS      = [8704755112]          # Ton ID Telegram
LOG_GROUP_ID   = -5142753842        # ID groupe logs (ou ton propre ID)
DB_PATH        = "shop.db"
PLISIO_API     = "https://plisio.net/api/v1"
PLESK_URL      = "https://hungry-boyd.82-165-77-124.plesk.page/"
COOLDOWN_SEC   = 10

POLL_INTERVAL  = 30    # secondes entre chaque vérif paiement
POLL_TIMEOUT   = 60    # minutes avant abandon

CRYPTOS = {
    "BTC":  "₿ Bitcoin (BTC)",
    "ETH":  "Ξ Ethereum (ETH)",
    "SOL":  "◎ Solana (SOL)",
    "LTC":  "Ł Litecoin (LTC)",
    "USDT": "💵 USDT (ERC-20)",
}

# ==========================================
# 📋 LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# 📦 PLANS
# ==========================================
DEFAULT_PLANS = {
    "plan_7":  {"days": 7,  "price": 10.50, "desc": "7 Jours",  "emoji": "⚡"},
    "plan_15": {"days": 15, "price": 16.00, "desc": "15 Jours", "emoji": "🚀"},
    "plan_30": {"days": 30, "price": 26.50, "desc": "30 Jours", "emoji": "💎"},
}
PLANS_FILE = "plans.json"

def load_plans():
    try:
        with open(PLANS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        save_plans(DEFAULT_PLANS)
        return DEFAULT_PLANS

def save_plans(plans):
    with open(PLANS_FILE, "w") as f:
        json.dump(plans, f, indent=2)

def get_plans():
    return load_plans()

# ==========================================
# 🗄️  BASE DE DONNÉES
# ==========================================
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Soldes clients
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            tg_id       INTEGER PRIMARY KEY,
            tg_username TEXT    NOT NULL,
            balance     REAL    DEFAULT 0.0,
            updated_at  TEXT    NOT NULL
        )
    """)

    # Transactions de recharge crypto
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id         INTEGER NOT NULL,
            tg_username   TEXT    NOT NULL,
            amount_eur    REAL    NOT NULL,
            amount_crypto REAL    NOT NULL,
            currency      TEXT    NOT NULL,
            plisio_id     TEXT    UNIQUE NOT NULL,
            status        TEXT    DEFAULT 'pending',
            created_at    TEXT    NOT NULL,
            confirmed_at  TEXT
        )
    """)

    # Stock de comptes Plesk
    # status : available | sold
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id    TEXT NOT NULL,
            username   TEXT NOT NULL,
            password   TEXT NOT NULL,
            added_at   TEXT NOT NULL,
            status     TEXT DEFAULT 'available'
        )
    """)

    # Commandes (comptes livrés aux clients)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id         INTEGER NOT NULL,
            tg_username   TEXT    NOT NULL,
            plan_id       TEXT    NOT NULL,
            plan_desc     TEXT    NOT NULL,
            credits_spent REAL    NOT NULL,
            stock_id      INTEGER NOT NULL,
            plesk_user    TEXT    NOT NULL,
            plesk_pass    TEXT    NOT NULL,
            purchase_date TEXT    NOT NULL
        )
    """)

    con.commit()
    con.close()
    logger.info("DB initialisée.")

# ---- Helpers ----
def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

def db_con():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

# ---- Wallets ----
def db_get_balance(tg_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT balance FROM wallets WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else 0.0

def db_ensure_wallet(tg_id, tg_username):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO wallets (tg_id, tg_username, balance, updated_at)
        VALUES (?, ?, 0.0, ?)
    """, (tg_id, tg_username, now()))
    cur.execute("UPDATE wallets SET tg_username = ? WHERE tg_id = ?", (tg_username, tg_id))
    con.commit()
    con.close()

def db_add_balance(tg_id, tg_username, amount):
    db_ensure_wallet(tg_id, tg_username)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE tg_id = ?
    """, (amount, now(), tg_id))
    con.commit()
    con.close()

def db_deduct_balance(tg_id, amount):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE tg_id = ?
    """, (amount, now(), tg_id))
    con.commit()
    con.close()

def db_get_all_wallets():
    con = db_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM wallets ORDER BY balance DESC")
    rows = cur.fetchall()
    con.close()
    return rows

# ---- Stock ----
def db_add_stock(plan_id, username, password):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO stock (plan_id, username, password, added_at, status)
        VALUES (?, ?, ?, ?, 'available')
    """, (plan_id, username, password, now()))
    con.commit()
    con.close()

def db_count_stock(plan_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM stock WHERE plan_id = ? AND status = 'available'", (plan_id,))
    count = cur.fetchone()[0]
    con.close()
    return count

def db_count_all_stock():
    """Retourne un dict {plan_id: count}"""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT plan_id, COUNT(*) FROM stock
        WHERE status = 'available'
        GROUP BY plan_id
    """)
    rows = cur.fetchall()
    con.close()
    return {r[0]: r[1] for r in rows}

def db_pop_stock(plan_id):
    """Prend un compte dispo du stock et le marque vendu. Retourne (stock_id, user, pass) ou None."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT id, username, password FROM stock
        WHERE plan_id = ? AND status = 'available'
        ORDER BY id ASC LIMIT 1
    """, (plan_id,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE stock SET status = 'sold' WHERE id = ?", (row[0],))
        con.commit()
    con.close()
    return row if row else None

def db_get_all_stock():
    con = db_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM stock ORDER BY id DESC")
    rows = cur.fetchall()
    con.close()
    return rows

def db_delete_stock(stock_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM stock WHERE id = ? AND status = 'available'", (stock_id,))
    affected = cur.rowcount
    con.commit()
    con.close()
    return affected > 0

# ---- Transactions ----
def db_add_transaction(tg_id, tg_username, amount_eur, amount_crypto, currency, plisio_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO transactions
            (tg_id, tg_username, amount_eur, amount_crypto, currency, plisio_id, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (tg_id, tg_username, amount_eur, amount_crypto, currency, plisio_id, now()))
    con.commit()
    con.close()

def db_get_transaction(plisio_id):
    con = db_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM transactions WHERE plisio_id = ?", (plisio_id,))
    row = cur.fetchone()
    con.close()
    return row

def db_confirm_transaction(plisio_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        UPDATE transactions SET status = 'confirmed', confirmed_at = ? WHERE plisio_id = ?
    """, (now(), plisio_id))
    con.commit()
    con.close()

def db_expire_transaction(plisio_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE transactions SET status = 'expired' WHERE plisio_id = ?", (plisio_id,))
    con.commit()
    con.close()

def db_get_transactions(tg_id):
    con = db_con()
    cur = con.cursor()
    cur.execute("""
        SELECT * FROM transactions WHERE tg_id = ? ORDER BY id DESC LIMIT 15
    """, (tg_id,))
    rows = cur.fetchall()
    con.close()
    return rows

# ---- Commandes ----
def db_add_order(tg_id, tg_username, plan_id, plan_desc, credits, stock_id, username, password):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO orders
            (tg_id, tg_username, plan_id, plan_desc, credits_spent,
             stock_id, plesk_user, plesk_pass, purchase_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (tg_id, tg_username, plan_id, plan_desc, credits, stock_id, username, password, now()))
    con.commit()
    con.close()

def db_get_orders(tg_id):
    con = db_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM orders WHERE tg_id = ? ORDER BY id DESC", (tg_id,))
    rows = cur.fetchall()
    con.close()
    return rows

def db_get_all_orders():
    con = db_con()
    cur = con.cursor()
    cur.execute("SELECT * FROM orders ORDER BY id DESC")
    rows = cur.fetchall()
    con.close()
    return rows

def db_stats():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*), SUM(credits_spent) FROM orders")
    o = cur.fetchone()
    cur.execute("SELECT COUNT(*), SUM(amount_eur) FROM transactions WHERE status = 'confirmed'")
    t = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM stock WHERE status = 'available'")
    stock_dispo = cur.fetchone()[0]
    cur.execute("SELECT plan_id, COUNT(*) FROM stock WHERE status = 'available' GROUP BY plan_id")
    stock_detail = cur.fetchall()
    con.close()
    return {
        "orders_total":   o[0] or 0,
        "orders_revenue": o[1] or 0.0,
        "topups_total":   t[0] or 0,
        "topups_revenue": t[1] or 0.0,
        "stock_dispo":    stock_dispo,
        "stock_detail":   stock_detail,
    }

# ==========================================
# 🛠️  UTILITAIRES
# ==========================================
_cooldown: dict[int, float] = {}

def is_admin(uid):   return uid in ADMIN_IDS
def has_username(u): return bool(u.username)

def is_rate_limited(uid):
    t = time.time()
    if t - _cooldown.get(uid, 0) < COOLDOWN_SEC:
        return True
    _cooldown[uid] = t
    return False

def send_log(text):
    try:
        bot.send_message(LOG_GROUP_ID, text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Log groupe : {e}")

def main_menu_markup():
    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("🛒 Acheter",           callback_data="menu_shop"),
        InlineKeyboardButton("💳 Recharger",         callback_data="menu_topup"),
        InlineKeyboardButton("📦 Mes commandes",     callback_data="my_orders"),
        InlineKeyboardButton("🧾 Mes transactions",  callback_data="my_transactions"),
        InlineKeyboardButton("💰 Mon solde",         callback_data="my_balance"),
        InlineKeyboardButton("ℹ️ À propos",          callback_data="menu_about"),
    )
    return m

# ==========================================
# 💳 PLISIO
# ==========================================
def plisio_create_invoice(amount_eur: float, currency: str, order_id: str):
    try:
        params = {
            "api_key":         PLISIO_API_KEY,
            "currency":        currency,
            "source_currency": "EUR",
            "source_amount":   str(amount_eur),
            "order_number":    order_id,
            "order_name":      f"Recharge {amount_eur:.2f} credits",
        }
        res = requests.get(f"{PLISIO_API}/invoices/new", params=params, timeout=15).json()
        if res.get("status") == "success":
            data = res["data"]
            return (
                data.get("invoice_url"),
                data.get("txn_id"),
                float(data.get("invoice_total_sum", 0))
            )
        logger.warning(f"Plisio invoice echec : {res}")
    except Exception as e:
        logger.error(f"Plisio create error : {e}")
    return None, None, None

def plisio_get_status(plisio_id: str):
    try:
        res = requests.get(
            f"{PLISIO_API}/transactions/{plisio_id}",
            params={"api_key": PLISIO_API_KEY},
            timeout=10
        ).json()
        if res.get("status") == "success":
            return res["data"].get("status")
    except Exception as e:
        logger.error(f"Plisio poll error : {e}")
    return None

def poll_payment(tg_id: int, tg_username: str, plisio_id: str, amount_eur: float, currency: str):
    deadline = time.time() + POLL_TIMEOUT * 60
    logger.info(f"Polling démarré : {plisio_id} ({tg_username})")

    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)

        txn = db_get_transaction(plisio_id)
        if txn and txn["status"] == "confirmed":
            return  # Déjà traité

        status = plisio_get_status(plisio_id)
        logger.info(f"Poll {plisio_id} → {status}")

        if status == "completed":
            db_confirm_transaction(plisio_id)
            db_add_balance(tg_id, tg_username, amount_eur)
            new_bal = db_get_balance(tg_id)
            try:
                bot.send_message(
                    tg_id,
                    f"✅ *Paiement confirmé !*\n\n"
                    f"💳 *+{amount_eur:.2f} crédits* ajoutés\n"
                    f"💰 *Solde :* `{new_bal:.2f} €`\n\n"
                    f"Tu peux maintenant acheter un accès Plesk 🚀",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Notif Telegram : {e}")
            send_log(
                f"💰 *RECHARGE CONFIRMÉE*\n"
                f"👤 @{tg_username} (`{tg_id}`)\n"
                f"💳 +{amount_eur:.2f}€ via {currency}\n"
                f"💰 Nouveau solde : {new_bal:.2f}€"
            )
            return

        elif status in ("error", "cancelled", "expired"):
            db_expire_transaction(plisio_id)
            try:
                bot.send_message(
                    tg_id,
                    f"❌ *Paiement {status}*\n\nLa facture a expiré ou été annulée.\n"
                    f"Utilise /start pour en créer une nouvelle.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            return

    # Timeout
    db_expire_transaction(plisio_id)
    try:
        bot.send_message(
            tg_id,
            f"⏰ *Facture expirée*\n\nAucun paiement reçu en {POLL_TIMEOUT} minutes.\n"
            f"Utilise /start pour recommencer.",
            parse_mode="Markdown"
        )
    except Exception:
        pass

# ==========================================
# 🏠 MENU PRINCIPAL
# ==========================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    if not has_username(message.from_user):
        return bot.send_message(
            message.chat.id,
            "⚠️ *Un @username Telegram est obligatoire.*\n\n"
            "➡️ Paramètres → Modifier le profil → Nom d'utilisateur\n"
            "Puis reviens avec /start.",
            parse_mode="Markdown"
        )
    db_ensure_wallet(message.from_user.id, message.from_user.username)
    balance = db_get_balance(message.from_user.id)
    bot.send_message(
        message.chat.id,
        f"👋 *Bienvenue sur PLESK SHOP* 🛡️\n\n"
        f"💰 *Votre solde :* `{balance:.2f} €`\n\n"
        f"🖥️ Accès Plesk prêts à l'emploi\n"
        f"💳 Recharge en crypto · Paiement en crédits\n"
        f"🚀 Livraison instantanée\n\n"
        f"Que souhaitez-vous faire ?",
        reply_markup=main_menu_markup(),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "back_main")
def back_main(call):
    bot.answer_callback_query(call.id)
    db_ensure_wallet(call.from_user.id, call.from_user.username)
    balance = db_get_balance(call.from_user.id)
    bot.send_message(
        call.message.chat.id,
        f"🏠 *Menu principal*\n\n💰 Solde : `{balance:.2f} €`",
        reply_markup=main_menu_markup(),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "menu_about")
def menu_about(call):
    bot.answer_callback_query(call.id)
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.send_message(
        call.message.chat.id,
        "ℹ️ *PLESK SHOP*\n\n"
        "🖥️ Accès Plesk clé en main\n"
        "💳 *1 crédit = 1 €*\n\n"
        "Cryptos acceptées :\n"
        "₿ BTC · Ξ ETH · ◎ SOL · Ł LTC · 💵 USDT\n\n"
        f"🔗 Panel : `{PLESK_URL}`",
        reply_markup=m, parse_mode="Markdown"
    )

# ==========================================
# 💰 SOLDE
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "my_balance")
def my_balance(call):
    db_ensure_wallet(call.from_user.id, call.from_user.username)
    balance = db_get_balance(call.from_user.id)
    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("💳 Recharger", callback_data="menu_topup"),
        InlineKeyboardButton("⬅️ Retour",    callback_data="back_main"),
    )
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"💰 *Votre solde*\n\n`{balance:.2f} €` disponibles\n\n_1 crédit = 1 €_",
        reply_markup=m, parse_mode="Markdown"
    )

# ==========================================
# 💳 RECHARGE
# ==========================================
TOPUP_AMOUNTS = [5, 10, 20, 50, 100]

@bot.callback_query_handler(func=lambda c: c.data == "menu_topup")
def menu_topup(call):
    bot.answer_callback_query(call.id)
    m = InlineKeyboardMarkup(row_width=3)
    for amt in TOPUP_AMOUNTS:
        m.add(InlineKeyboardButton(f"{amt} €", callback_data=f"topup_amt_{amt}"))
    m.add(InlineKeyboardButton("✏️ Montant libre", callback_data="topup_custom"))
    m.add(InlineKeyboardButton("⬅️ Retour",        callback_data="back_main"))
    bot.send_message(
        call.message.chat.id,
        "💳 *RECHARGER LE SOLDE*\n\n1 crédit = 1 €\nChoisissez le montant :",
        reply_markup=m, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "topup_custom")
def topup_custom(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "✏️ Entrez le montant en € (min 1€) :\n_Exemple : 25_",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, topup_custom_amount)

def topup_custom_amount(message):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount < 1:
            return bot.reply_to(message, "❌ Minimum 1€.")
        choose_crypto(message, amount)
    except ValueError:
        bot.reply_to(message, "❌ Nombre invalide.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("topup_amt_"))
def topup_amount_cb(call):
    bot.answer_callback_query(call.id)
    amount = float(call.data.split("_")[2])
    choose_crypto(call.message, amount)

def choose_crypto(message, amount: float):
    m = InlineKeyboardMarkup(row_width=1)
    for code, label in CRYPTOS.items():
        m.add(InlineKeyboardButton(label, callback_data=f"topup_pay_{amount}_{code}"))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="menu_topup"))
    bot.send_message(
        message.chat.id,
        f"💳 Recharge *{amount:.2f} €* — Choisissez votre crypto :",
        reply_markup=m, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("topup_pay_"))
def topup_pay(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    if is_rate_limited(call.from_user.id):
        return bot.answer_callback_query(call.id, "⏳ Patientez.", show_alert=True)

    parts    = call.data.split("_")   # topup_pay_<amount>_<currency>
    amount   = float(parts[2])
    currency = parts[3]
    label    = CRYPTOS.get(currency, currency)
    order_id = f"topup-{call.from_user.id}-{int(time.time())}"

    bot.answer_callback_query(call.id, "⏳ Création de la facture...")

    invoice_url, plisio_id, amount_crypto = plisio_create_invoice(amount, currency, order_id)

    if not invoice_url:
        return bot.send_message(
            call.message.chat.id,
            "❌ Service de paiement indisponible. Réessayez dans quelques minutes."
        )

    db_add_transaction(
        tg_id=call.from_user.id,
        tg_username=call.from_user.username,
        amount_eur=amount,
        amount_crypto=amount_crypto,
        currency=currency,
        plisio_id=plisio_id
    )

    # Lance le polling en arrière-plan
    threading.Thread(
        target=poll_payment,
        args=(call.from_user.id, call.from_user.username, plisio_id, amount, currency),
        daemon=True
    ).start()

    m = InlineKeyboardMarkup(row_width=1)
    m.add(InlineKeyboardButton(f"💸 Payer {amount_crypto} {currency}", url=invoice_url))
    m.add(InlineKeyboardButton("🏠 Menu", callback_data="back_main"))

    bot.send_message(
        call.message.chat.id,
        f"🧾 *FACTURE CRÉÉE*\n\n"
        f"💳 Recharge : `{amount:.2f} €`\n"
        f"🔗 Crypto : {label}\n"
        f"💰 À envoyer : `{amount_crypto} {currency}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👆 Clique pour payer\n"
        f"✅ Crédits ajoutés automatiquement après confirmation\n"
        f"⏱️ Valable {POLL_TIMEOUT} min\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 `{plisio_id}`",
        reply_markup=m, parse_mode="Markdown"
    )

# ==========================================
# 🛒 BOUTIQUE
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "menu_shop")
def show_shop(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    db_ensure_wallet(call.from_user.id, call.from_user.username)
    balance = db_get_balance(call.from_user.id)
    plans   = get_plans()
    stock   = db_count_all_stock()

    m = InlineKeyboardMarkup(row_width=1)
    for pid, plan in plans.items():
        dispo  = stock.get(pid, 0)
        suffix = f" ✅" if balance >= plan["price"] else " ❌"
        label  = f"{plan['emoji']} {plan['desc']} — {plan['price']:.2f}€ — Stock: {dispo}{suffix}"
        m.add(InlineKeyboardButton(label, callback_data=f"buy_{pid}"))
    m.add(InlineKeyboardButton("💳 Recharger", callback_data="menu_topup"))
    m.add(InlineKeyboardButton("⬅️ Retour",    callback_data="back_main"))

    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"🛒 *BOUTIQUE*\n\n"
        f"💰 Votre solde : `{balance:.2f} €`\n\n"
        f"✅ = solde OK · ❌ = recharge nécessaire",
        reply_markup=m, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def buy_plan(call):
    plan_id = call.data[4:]
    plans   = get_plans()
    if plan_id not in plans:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)

    plan    = plans[plan_id]
    balance = db_get_balance(call.from_user.id)
    dispo   = db_count_stock(plan_id)

    if dispo == 0:
        return bot.answer_callback_query(call.id, "⚠️ Stock épuisé pour cette offre.", show_alert=True)

    if balance < plan["price"]:
        missing = plan["price"] - balance
        m = InlineKeyboardMarkup(row_width=1)
        m.add(InlineKeyboardButton(f"💳 Recharger {missing:.2f}€", callback_data="menu_topup"))
        m.add(InlineKeyboardButton("⬅️ Retour", callback_data="menu_shop"))
        bot.answer_callback_query(call.id)
        return bot.send_message(
            call.message.chat.id,
            f"❌ *Solde insuffisant*\n\n"
            f"💰 Solde : `{balance:.2f} €`\n"
            f"💳 Prix : `{plan['price']:.2f} €`\n"
            f"📉 Manque : `{missing:.2f} €`",
            reply_markup=m, parse_mode="Markdown"
        )

    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("✅ Confirmer", callback_data=f"confirm_{plan_id}"),
        InlineKeyboardButton("❌ Annuler",   callback_data="menu_shop"),
    )
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"🛒 *CONFIRMATION*\n\n"
        f"📦 Offre : *{plan['desc']}*\n"
        f"💳 Coût : `{plan['price']:.2f} crédits`\n"
        f"💰 Solde après : `{balance - plan['price']:.2f} €`\n\n"
        f"Confirmer ?",
        reply_markup=m, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("confirm_"))
def confirm_buy(call):
    plan_id = call.data[8:]
    plans   = get_plans()
    if plan_id not in plans:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)

    plan    = plans[plan_id]
    balance = db_get_balance(call.from_user.id)

    if balance < plan["price"]:
        return bot.answer_callback_query(call.id, "❌ Solde insuffisant.", show_alert=True)
    if is_rate_limited(call.from_user.id):
        return bot.answer_callback_query(call.id, "⏳ Patientez.", show_alert=True)

    # Prend un compte dans le stock
    stock_row = db_pop_stock(plan_id)
    if not stock_row:
        return bot.answer_callback_query(call.id, "⚠️ Stock épuisé, réessayez.", show_alert=True)

    # Débit
    db_deduct_balance(call.from_user.id, plan["price"])

    stock_id = stock_row[0] if not hasattr(stock_row, 'keys') else stock_row["id"]
    username = stock_row[1] if not hasattr(stock_row, 'keys') else stock_row["username"]
    password = stock_row[2] if not hasattr(stock_row, 'keys') else stock_row["password"]

    db_add_order(
        tg_id=call.from_user.id,
        tg_username=call.from_user.username,
        plan_id=plan_id,
        plan_desc=plan["desc"],
        credits=plan["price"],
        stock_id=stock_id,
        username=username,
        password=password
    )

    new_balance = db_get_balance(call.from_user.id)

    bot.answer_callback_query(call.id, "✅ Livraison en cours...")

    m = InlineKeyboardMarkup(row_width=1)
    m.add(
        InlineKeyboardButton("📦 Mes commandes",  callback_data="my_orders"),
        InlineKeyboardButton("🏠 Menu principal", callback_data="back_main"),
    )
    bot.edit_message_text(
        f"🎉 *ACCÈS LIVRÉ !*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 *URL Panel Plesk :*\n`{PLESK_URL}`\n\n"
        f"👤 *Identifiant :* `{username}`\n"
        f"🔑 *Mot de passe :* `{password}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 *Crédits restants :* `{new_balance:.2f} €`\n\n"
        f"_Retrouve tes accès via 📦 Mes commandes._",
        call.message.chat.id, call.message.message_id,
        reply_markup=m, parse_mode="Markdown"
    )

    send_log(
        f"🛒 *VENTE*\n"
        f"👤 @{call.from_user.username} (`{call.from_user.id}`)\n"
        f"📦 {plan['desc']} — {plan['price']:.2f}€\n"
        f"👤 `{username}`"
    )

# ==========================================
# 📦 MES COMMANDES
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "my_orders")
def my_orders(call):
    rows = db_get_orders(call.from_user.id)
    if not rows:
        return bot.answer_callback_query(call.id, "📭 Aucune commande.", show_alert=True)
    text = "📦 *Vos accès Plesk :*\n\n"
    for row in rows:
        text += (
            f"🖥️ *{row['plan_desc']}* — {row['purchase_date']}\n"
            f"🔗 `{PLESK_URL}`\n"
            f"👤 `{row['plesk_user']}` · 🔑 `{row['plesk_pass']}`\n\n"
        )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

# ==========================================
# 🧾 MES TRANSACTIONS
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "my_transactions")
def my_transactions(call):
    rows = db_get_transactions(call.from_user.id)
    if not rows:
        return bot.answer_callback_query(call.id, "📭 Aucune transaction.", show_alert=True)
    text = "🧾 *Transactions (15 dernières) :*\n\n"
    for row in rows:
        icon = "✅" if row["status"] == "confirmed" else ("❌" if row["status"] in ("expired", "error") else "⏳")
        text += (
            f"{icon} *+{row['amount_eur']:.2f} €* via `{row['currency']}`\n"
            f"   {row['created_at']} · `{row['status']}`\n\n"
        )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

# ==========================================
# 👑 PANEL ADMIN
# ==========================================
def admin_markup():
    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("📥 Ajouter stock",     callback_data="adm_add_stock"),
        InlineKeyboardButton("📦 Voir stock",        callback_data="adm_view_stock"),
        InlineKeyboardButton("👥 Commandes",         callback_data="adm_orders"),
        InlineKeyboardButton("📊 Statistiques",      callback_data="adm_stats"),
        InlineKeyboardButton("💰 Soldes clients",    callback_data="adm_wallets"),
        InlineKeyboardButton("💳 Créditer client",   callback_data="adm_credit"),
        InlineKeyboardButton("🔍 Chercher client",   callback_data="adm_search"),
        InlineKeyboardButton("✏️ Modifier prix",     callback_data="adm_prices"),
    )
    return m

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if not is_admin(message.from_user.id): return
    s = db_stats()
    bot.send_message(
        message.chat.id,
        f"👑 *Panel Admin*\n\n"
        f"🛒 Ventes : *{s['orders_total']}* — *{s['orders_revenue']:.2f} €*\n"
        f"💳 Recharges : *{s['topups_total']}* — *{s['topups_revenue']:.2f} €*\n"
        f"📦 Stock dispo : *{s['stock_dispo']}* comptes",
        reply_markup=admin_markup(), parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "adm_back")
def adm_back(call):
    if not is_admin(call.from_user.id): return
    s = db_stats()
    bot.edit_message_text(
        f"👑 *Panel Admin*\n\n"
        f"🛒 Ventes : *{s['orders_total']}* — *{s['orders_revenue']:.2f} €*\n"
        f"💳 Recharges : *{s['topups_total']}* — *{s['topups_revenue']:.2f} €*\n"
        f"📦 Stock dispo : *{s['stock_dispo']}* comptes",
        call.message.chat.id, call.message.message_id,
        reply_markup=admin_markup(), parse_mode="Markdown"
    )

# ---- Ajouter stock ----
@bot.callback_query_handler(func=lambda c: c.data == "adm_add_stock")
def adm_add_stock(call):
    if not is_admin(call.from_user.id): return
    plans = get_plans()
    m = InlineKeyboardMarkup(row_width=1)
    for pid, plan in plans.items():
        dispo = db_count_stock(pid)
        m.add(InlineKeyboardButton(
            f"{plan['emoji']} {plan['desc']} (stock: {dispo})",
            callback_data=f"adm_addstock_{pid}"
        ))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(
            "📥 *Ajouter du stock*\n\nChoisissez l'offre :",
            call.message.chat.id, call.message.message_id,
            reply_markup=m, parse_mode="Markdown"
        )
    except Exception:
        bot.send_message(call.message.chat.id, "📥 Choisissez l'offre :", reply_markup=m)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_addstock_"))
def adm_addstock_plan(call):
    if not is_admin(call.from_user.id): return
    plan_id = call.data.replace("adm_addstock_", "")
    plans   = get_plans()
    plan    = plans.get(plan_id, {})
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        f"📥 *Ajout de stock — {plan.get('desc', plan_id)}*\n\n"
        f"Envoie les comptes, *un par ligne*, format :\n"
        f"`username:password`\n\n"
        f"Exemple :\n"
        f"`admin1:MonPass123`\n"
        f"`admin2:AutrePass456`\n\n"
        f"_Envoie autant de lignes que tu veux en un seul message._",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, lambda m: adm_addstock_receive(m, plan_id))

def adm_addstock_receive(message, plan_id):
    if not is_admin(message.from_user.id): return
    lines   = [l.strip() for l in message.text.strip().splitlines() if l.strip()]
    added   = 0
    errors  = []
    for line in lines:
        if ":" not in line:
            errors.append(f"❌ `{line}` — format invalide")
            continue
        parts = line.split(":", 1)
        username, password = parts[0].strip(), parts[1].strip()
        if not username or not password:
            errors.append(f"❌ `{line}` — champ vide")
            continue
        db_add_stock(plan_id, username, password)
        added += 1

    plans  = get_plans()
    plan   = plans.get(plan_id, {})
    total  = db_count_stock(plan_id)
    report = f"✅ *{added} compte(s) ajouté(s)* pour *{plan.get('desc', plan_id)}*\n"
    report += f"📦 Stock total dispo : *{total}*\n"
    if errors:
        report += "\n*Erreurs :*\n" + "\n".join(errors)

    bot.reply_to(message, report, parse_mode="Markdown")
    send_log(f"📥 *STOCK AJOUTÉ*\n{added} compte(s) — {plan.get('desc', plan_id)}\nTotal dispo : {total}")

# ---- Voir stock ----
@bot.callback_query_handler(func=lambda c: c.data == "adm_view_stock")
def adm_view_stock(call):
    if not is_admin(call.from_user.id): return
    plans  = get_plans()
    stock  = db_count_all_stock()
    bot.answer_callback_query(call.id)

    text = "📦 *Stock disponible :*\n\n"
    for pid, plan in plans.items():
        dispo = stock.get(pid, 0)
        bar   = "🟢" * min(dispo, 10) + ("+" if dispo > 10 else "")
        text += f"{plan['emoji']} *{plan['desc']}* : *{dispo}* dispo {bar}\n"

    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("📥 Ajouter", callback_data="adm_add_stock"),
        InlineKeyboardButton("⬅️ Retour",  callback_data="adm_back"),
    )
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=m, parse_mode="Markdown")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

# ---- Commandes ----
@bot.callback_query_handler(func=lambda c: c.data == "adm_orders")
def adm_orders(call):
    if not is_admin(call.from_user.id): return
    rows = db_get_all_orders()
    bot.answer_callback_query(call.id)
    if not rows:
        return bot.send_message(call.message.chat.id, "📭 Aucune commande.")

    # Envoie par blocs de 10 pour éviter les messages trop longs
    chunk_size = 8
    for i in range(0, min(len(rows), chunk_size), 1):
        row  = rows[i]
        text = (
            f"🆔 `{row['id']}` · @{row['tg_username']}\n"
            f"📦 {row['plan_desc']} — {row['credits_spent']:.2f}€\n"
            f"👤 `{row['plesk_user']}` · 🔑 `{row['plesk_pass']}`\n"
            f"📅 {row['purchase_date']}\n"
        )
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown")

    if len(rows) > chunk_size:
        bot.send_message(call.message.chat.id, f"_... et {len(rows) - chunk_size} commandes de plus._", parse_mode="Markdown")

    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour admin", callback_data="adm_back"))
    bot.send_message(call.message.chat.id, "─", reply_markup=m)

# ---- Stats ----
@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def adm_stats(call):
    if not is_admin(call.from_user.id): return
    s  = db_stats()
    sd = "\n".join([f"  • {r[0]} : {r[1]} dispo" for r in s["stock_detail"]]) or "  Aucun"
    text = (
        f"📊 *Statistiques*\n\n"
        f"🛒 Ventes : *{s['orders_total']}* — *{s['orders_revenue']:.2f} €*\n"
        f"💳 Recharges : *{s['topups_total']}* — *{s['topups_revenue']:.2f} €*\n\n"
        f"📦 *Stock par offre :*\n{sd}"
    )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=m, parse_mode="Markdown")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

# ---- Soldes clients ----
@bot.callback_query_handler(func=lambda c: c.data == "adm_wallets")
def adm_wallets(call):
    if not is_admin(call.from_user.id): return
    rows = db_get_all_wallets()
    bot.answer_callback_query(call.id)
    if not rows:
        return bot.send_message(call.message.chat.id, "📭 Aucun client.")
    text = "💰 *Soldes clients :*\n\n"
    for row in rows:
        text += f"@{row['tg_username']} (`{row['tg_id']}`) — `{row['balance']:.2f} €`\n"
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=m, parse_mode="Markdown")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

# ---- Créditer ----
@bot.callback_query_handler(func=lambda c: c.data == "adm_credit")
def adm_credit(call):
    if not is_admin(call.from_user.id): return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "💳 *Créditer un client*\n\nFormat : `@username montant`\nEx : `@monuser 15`",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, adm_credit_do)

def adm_credit_do(message):
    if not is_admin(message.from_user.id): return
    try:
        parts    = message.text.strip().split()
        username = parts[0].lstrip("@")
        amount   = float(parts[1])
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT tg_id FROM wallets WHERE tg_username = ?", (username,))
        row = cur.fetchone()
        con.close()
        if not row:
            return bot.reply_to(message, "❌ Client introuvable (doit avoir fait /start).")
        tg_id = row[0]
        db_add_balance(tg_id, username, amount)
        new_bal = db_get_balance(tg_id)
        bot.reply_to(message,
            f"✅ *+{amount:.2f}€* crédités à @{username}\nNouveau solde : `{new_bal:.2f}€`",
            parse_mode="Markdown"
        )
        try:
            bot.send_message(tg_id,
                f"💳 *{amount:.2f} crédits* ajoutés par l'admin.\n💰 Solde : `{new_bal:.2f}€`",
                parse_mode="Markdown"
            )
        except Exception:
            pass
        send_log(f"💳 *CRÉDIT ADMIN*\n@{username} +{amount:.2f}€ → {new_bal:.2f}€")
    except (IndexError, ValueError):
        bot.reply_to(message, "❌ Format : `@username montant`", parse_mode="Markdown")

# ---- Recherche client ----
@bot.callback_query_handler(func=lambda c: c.data == "adm_search")
def adm_search(call):
    if not is_admin(call.from_user.id): return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🔍 @username ou ID Telegram :")
    bot.register_next_step_handler(msg, adm_search_do)

def adm_search_do(message):
    if not is_admin(message.from_user.id): return
    query = message.text.strip().lstrip("@")
    con   = db_con()
    cur   = con.cursor()
    cur.execute(
        "SELECT * FROM wallets WHERE tg_username = ? OR CAST(tg_id AS TEXT) = ?",
        (query, query)
    )
    wallet = cur.fetchone()
    cur.execute(
        "SELECT * FROM orders WHERE tg_username = ? OR CAST(tg_id AS TEXT) = ? ORDER BY id DESC",
        (query, query)
    )
    orders = cur.fetchall()
    con.close()

    if not wallet and not orders:
        return bot.reply_to(message, "❌ Aucun client trouvé.")

    text = f"🔍 *Résultats : {query}*\n\n"
    if wallet:
        text += f"💰 Solde : `{wallet['balance']:.2f}€`\n\n"
    for row in orders:
        text += (
            f"📦 {row['plan_desc']} — {row['purchase_date']}\n"
            f"👤 `{row['plesk_user']}` · 🔑 `{row['plesk_pass']}`\n\n"
        )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# ---- Modifier prix ----
@bot.callback_query_handler(func=lambda c: c.data == "adm_prices")
def adm_prices(call):
    if not is_admin(call.from_user.id): return
    plans = get_plans()
    m = InlineKeyboardMarkup(row_width=1)
    for pid, plan in plans.items():
        m.add(InlineKeyboardButton(
            f"{plan['emoji']} {plan['desc']} — {plan['price']:.2f}€",
            callback_data=f"adm_editprice_{pid}"
        ))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    try:
        bot.edit_message_text(
            "✏️ *Modifier les prix* — choisissez une offre :",
            call.message.chat.id, call.message.message_id,
            reply_markup=m, parse_mode="Markdown"
        )
    except Exception:
        bot.send_message(call.message.chat.id, "Choisissez :", reply_markup=m)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_editprice_"))
def adm_editprice(call):
    if not is_admin(call.from_user.id): return
    plan_id = call.data.replace("adm_editprice_", "")
    plans   = get_plans()
    plan    = plans.get(plan_id)
    if not plan:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        f"✏️ Nouveau prix pour *{plan['desc']}* (actuel : `{plan['price']:.2f}€`) :",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, lambda m: adm_saveprice(m, plan_id))

def adm_saveprice(message, plan_id):
    if not is_admin(message.from_user.id): return
    try:
        new_price = float(message.text.strip().replace(",", "."))
        if new_price <= 0:
            return bot.reply_to(message, "❌ Prix doit être > 0.")
        plans = get_plans()
        old   = plans[plan_id]["price"]
        plans[plan_id]["price"] = new_price
        save_plans(plans)
        bot.reply_to(message,
            f"✅ *{plans[plan_id]['desc']}* : `{old:.2f}€` → `{new_price:.2f}€`",
            parse_mode="Markdown"
        )
        send_log(f"✏️ *PRIX MODIFIÉ*\n{plans[plan_id]['desc']} : {old:.2f}€ → {new_price:.2f}€")
    except ValueError:
        bot.reply_to(message, "❌ Nombre invalide.")

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def noop(call): bot.answer_callback_query(call.id)

# ==========================================
# 🚀 DÉMARRAGE
# ==========================================
if __name__ == "__main__":
    init_db()
    logger.info("🚀 PLESK SHOP démarré")
    logger.info("✅ Polling Plisio actif — pas de webhook nécessaire")
    bot.infinity_polling(timeout=20, long_polling_timeout=15)
