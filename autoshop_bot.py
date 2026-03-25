"""
PLESK AUTOSHOP — Système de crédits via Plisio
================================================
1€ = 1 crédit | Recharge via BTC, ETH, SOL, LTC, USDT ERC-20
Les crédits sont débités à l'achat d'un hébergement Plesk.
Le callback Plisio valide automatiquement les paiements.

Lancer le webhook Flask en parallèle du bot :
    python autoshop_bot.py

Expose le webhook (ngrok ou VPS) :
    ngrok http 5000
    → Configurer l'URL dans Plisio : https://ton-domaine.com/plisio_callback
"""

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import subprocess
import datetime
import string
import random
import logging
import time
import sqlite3
import json
import hashlib
import hmac
import threading
from flask import Flask, request as flask_request, jsonify

# ==========================================
# ⚙️ CONFIGURATION — REMPLIS ICI
# ==========================================
BOT_TOKEN      = "8628435813:AAG-RpDUGSpNaTWbPFgwaQuJe7ffRHY7E24"
PLISIO_API_KEY = "b-WCYaL8vgyJobhvc-0eEt3nnwkHmPkmJhUdXB8JJeYR7DNegbyJpo0Z9ngKATNM"     # plisio.net → API Keys
ADMIN_IDS      = [8704755112]
LOG_GROUP_ID   = -5142753842
DB_PATH        = "shop.db"
PLISIO_API     = "https://plisio.net/api/v1"
WEBHOOK_PORT   = 5000
COOLDOWN_SEC   = 10

# Cryptos Plisio acceptées (currency : label)
CRYPTOS = {
    "BTC":     "₿ Bitcoin (BTC)",
    "ETH":     "Ξ Ethereum (ETH)",
    "SOL":     "◎ Solana (SOL)",
    "LTC":     "Ł Litecoin (LTC)",
    "USDT":    "💵 USDT (ERC-20)",
}

# IP publique auto-détectée
def get_public_ip():
    for url in ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]:
        try:
            return requests.get(url, timeout=5).text.strip()
        except Exception:
            continue
    return "127.0.0.1"

SERVER_IP = get_public_ip()

# ==========================================
# 📋 LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
logger.info(f"IP serveur : {SERVER_IP}")

bot   = telebot.TeleBot(BOT_TOKEN)
app   = Flask(__name__)   # Webhook Plisio

# ==========================================
# 📦 PLANS (prix en crédits = €)
# ==========================================
DEFAULT_PLANS = {
    "plan_7":  {"name": "Pack_7J",  "days": 7,  "price": 10.50, "desc": "1 NDD / 7 Jours",     "emoji": "⚡"},
    "plan_15": {"name": "Pack_15J", "days": 15, "price": 16.00, "desc": "2 NDD / 15 Jours",    "emoji": "🚀"},
    "plan_30": {"name": "Pack_30J", "days": 30, "price": 26.50, "desc": "Illimité / 30 Jours", "emoji": "💎"},
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
# 🗄️ BASE DE DONNÉES
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

    # Transactions de recharge
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id        INTEGER NOT NULL,
            tg_username  TEXT    NOT NULL,
            amount_eur   REAL    NOT NULL,
            amount_crypto REAL   NOT NULL,
            currency     TEXT    NOT NULL,
            plisio_id    TEXT    UNIQUE NOT NULL,
            status       TEXT    DEFAULT 'pending',
            created_at   TEXT    NOT NULL,
            confirmed_at TEXT
        )
    """)

    # Commandes Plesk
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id         INTEGER NOT NULL,
            tg_username   TEXT    NOT NULL,
            plan_id       TEXT    NOT NULL,
            plan_desc     TEXT    NOT NULL,
            credits_spent REAL    NOT NULL,
            plesk_domain  TEXT    NOT NULL,
            plesk_user    TEXT    NOT NULL,
            plesk_pass    TEXT    NOT NULL,
            purchase_date TEXT    NOT NULL,
            expire_date   TEXT    NOT NULL,
            active        INTEGER DEFAULT 1
        )
    """)

    con.commit()
    con.close()
    logger.info("DB initialisée.")

# — Wallet —
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
    """, (tg_id, tg_username, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    cur.execute("UPDATE wallets SET tg_username = ? WHERE tg_id = ?", (tg_username, tg_id))
    con.commit()
    con.close()

def db_add_balance(tg_id, tg_username, amount):
    db_ensure_wallet(tg_id, tg_username)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE tg_id = ?
    """, (amount, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), tg_id))
    con.commit()
    con.close()

def db_deduct_balance(tg_id, amount):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE tg_id = ?
    """, (amount, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), tg_id))
    con.commit()
    con.close()

def db_get_all_wallets():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM wallets ORDER BY balance DESC")
    rows = cur.fetchall()
    con.close()
    return rows

# — Transactions —
def db_add_transaction(tg_id, tg_username, amount_eur, amount_crypto, currency, plisio_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO transactions
            (tg_id, tg_username, amount_eur, amount_crypto, currency, plisio_id, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (tg_id, tg_username, amount_eur, amount_crypto, currency, plisio_id,
          datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    con.commit()
    con.close()

def db_get_transaction(plisio_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
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
    """, (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), plisio_id))
    con.commit()
    con.close()

def db_get_transactions(tg_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT * FROM transactions WHERE tg_id = ? ORDER BY id DESC LIMIT 15
    """, (tg_id,))
    rows = cur.fetchall()
    con.close()
    return rows

# — Commandes —
def db_add_order(tg_id, tg_username, plan_id, plan_desc, credits, domain, username, password, expire):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO orders
            (tg_id, tg_username, plan_id, plan_desc, credits_spent, plesk_domain,
             plesk_user, plesk_pass, purchase_date, expire_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tg_id, tg_username, plan_id, plan_desc, credits, domain,
        username, password,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), expire
    ))
    con.commit()
    con.close()

def db_get_orders(tg_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM orders WHERE tg_id = ? ORDER BY id DESC", (tg_id,))
    rows = cur.fetchall()
    con.close()
    return rows

def db_get_all_orders():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM orders ORDER BY id DESC")
    rows = cur.fetchall()
    con.close()
    return rows

def db_set_order_active(order_id, state):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE orders SET active = ? WHERE id = ?", (state, order_id))
    con.commit()
    con.close()

def db_get_order(order_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    con.close()
    return row

def db_stats():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*), SUM(credits_spent) FROM orders")
    o = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM orders WHERE active = 1")
    active = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*), SUM(amount_eur) FROM transactions WHERE status = 'confirmed'")
    t = cur.fetchone()
    cur.execute("SELECT plan_desc, COUNT(*) FROM orders GROUP BY plan_desc")
    breakdown = cur.fetchall()
    con.close()
    return {
        "orders_total":    o[0] or 0,
        "orders_revenue":  o[1] or 0.0,
        "orders_active":   active,
        "topups_total":    t[0] or 0,
        "topups_revenue":  t[1] or 0.0,
        "breakdown":       breakdown,
    }

# ==========================================
# 🛠️ UTILITAIRES
# ==========================================
_cooldown: dict[int, float] = {}

def gen_password(length=12):
    return ''.join(random.choices(string.ascii_letters + string.digits + "!@#$", k=length))

def gen_uid(length=6):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def is_admin(uid):   return uid in ADMIN_IDS
def has_username(u): return bool(u.username)

def is_rate_limited(uid):
    now = time.time()
    if now - _cooldown.get(uid, 0) < COOLDOWN_SEC:
        return True
    _cooldown[uid] = now
    return False

def send_log(text):
    try:
        bot.send_message(LOG_GROUP_ID, text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Log groupe : {e}")

def order_card(row, show_creds=False):
    status = "✅ Actif" if row["active"] else "❌ Inactif"
    card = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *ID :* `{row['id']}`\n"
        f"👤 *Telegram :* @{row['tg_username']} (`{row['tg_id']}`)\n"
        f"📦 *Offre :* {row['plan_desc']}\n"
        f"💳 *Crédits :* `{row['credits_spent']:.2f} €`\n"
        f"📅 *Achat :* {row['purchase_date']}\n"
        f"⏰ *Expire :* {row['expire_date']}\n"
        f"🌐 *Domaine :* `{row['plesk_domain']}`\n"
        f"🔖 *Statut :* {status}\n"
    )
    if show_creds:
        card += (
            f"👤 *Login :* `{row['plesk_user']}`\n"
            f"🔑 *Pass :* `{row['plesk_pass']}`\n"
            f"🖥️ *Panel :* `https://{SERVER_IP}:8443`\n"
        )
    return card

# ==========================================
# 💳 PLISIO API
# ==========================================
def plisio_create_invoice(amount_eur: float, currency: str, tg_id: int, order_id: str):
    """
    Crée une facture Plisio.
    Retourne (invoice_url, plisio_id, amount_crypto) ou (None, None, None).
    """
    try:
        params = {
            "api_key":          PLISIO_API_KEY,
            "currency":         currency,           # BTC, ETH, SOL, LTC, USDT
            "source_currency":  "EUR",
            "source_amount":    str(amount_eur),
            "order_number":     order_id,
            "order_name":       f"Recharge {amount_eur:.2f} credits",
            "callback_url":     f"http://{SERVER_IP}:{WEBHOOK_PORT}/plisio_callback",
            "success_url":      f"https://t.me/{bot.get_me().username}",
            "cancel_url":       f"https://t.me/{bot.get_me().username}",
        }
        res = requests.get(f"{PLISIO_API}/invoices/new",
                           params=params, timeout=15).json()

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

def plisio_verify_callback(data: dict) -> bool:
    """
    Vérifie la signature du callback Plisio pour éviter les fraudes.
    Plisio envoie un champ 'verify_hash'.
    """
    verify_hash = data.pop("verify_hash", None)
    if not verify_hash:
        return False
    # Trier les paramètres par clé, concaténer, puis HMAC-SHA1 avec la clé API
    sorted_data  = "&".join([f"{k}={v}" for k, v in sorted(data.items())])
    expected     = hmac.new(
        PLISIO_API_KEY.encode(), sorted_data.encode(), hashlib.sha1
    ).hexdigest()
    return hmac.compare_digest(expected, verify_hash)

# ==========================================
# 🌐 WEBHOOK FLASK — Reçoit les callbacks Plisio
# ==========================================
@app.route("/plisio_callback", methods=["POST", "GET"])
def plisio_callback():
    data = flask_request.form.to_dict() if flask_request.method == "POST" else flask_request.args.to_dict()
    logger.info(f"Plisio callback reçu : {data}")

    # Vérif signature
    if not plisio_verify_callback(dict(data)):
        logger.warning("Callback Plisio : signature invalide !")
        return jsonify({"status": "error", "message": "invalid signature"}), 403

    status    = data.get("status")
    plisio_id = data.get("txn_id")

    # Statuts Plisio : new / pending / completed / mismatch / error / cancelled / expired
    if status not in ("completed",):
        logger.info(f"Callback Plisio ignoré (status={status})")
        return jsonify({"status": "ok"}), 200

    # Récupère la transaction en base
    txn = db_get_transaction(plisio_id)
    if not txn:
        logger.warning(f"Transaction Plisio inconnue : {plisio_id}")
        return jsonify({"status": "error", "message": "unknown transaction"}), 404

    if txn["status"] == "confirmed":
        logger.info(f"Transaction déjà confirmée : {plisio_id}")
        return jsonify({"status": "ok"}), 200

    # ✅ Crédit automatique
    db_confirm_transaction(plisio_id)
    db_add_balance(txn["tg_id"], txn["tg_username"], txn["amount_eur"])

    new_balance = db_get_balance(txn["tg_id"])

    logger.info(f"Crédit OK : +{txn['amount_eur']}€ pour {txn['tg_username']} (total: {new_balance:.2f}€)")

    # Notification Telegram au client
    try:
        bot.send_message(
            txn["tg_id"],
            f"✅ *Paiement confirmé !*\n\n"
            f"💳 *+{txn['amount_eur']:.2f} crédits* ajoutés à votre solde\n"
            f"💰 *Solde actuel :* `{new_balance:.2f} €`\n\n"
            f"Vous pouvez maintenant acheter un hébergement Plesk 🚀",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Notif Telegram échouée : {e}")

    send_log(
        f"💰 *RECHARGE CONFIRMÉE*\n\n"
        f"👤 @{txn['tg_username']} (`{txn['tg_id']}`)\n"
        f"💳 +{txn['amount_eur']:.2f} € via {txn['currency']}\n"
        f"💰 Nouveau solde : {new_balance:.2f} €\n"
        f"🆔 `{plisio_id}`"
    )

    return jsonify({"status": "ok"}), 200

# ==========================================
# 🖥️ PROVISIONNEMENT PLESK
# ==========================================
def provision_plesk(plan):
    uid      = gen_uid()
    domain   = f"client-{uid}.local"
    username = f"user_{uid}"
    password = gen_password()
    expire   = (
        datetime.datetime.now() + datetime.timedelta(days=plan["days"])
    ).strftime("%Y-%m-%d")

    cmd = [
        "plesk", "bin", "subscription", "--create", domain,
        "-owner", "admin", "-service-plan", plan["name"],
        "-ip", SERVER_IP, "-login", username,
        "-passwd", password, "-expire", expire
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
        logger.info(f"Plesk créé : {domain}")
        return {"domain": domain, "username": username, "password": password, "expire": expire}
    except subprocess.CalledProcessError as e:
        logger.error(f"Plesk error : {e.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("Plesk timeout (60s)")
    return None

# ==========================================
# 🏠 MENU PRINCIPAL
# ==========================================
def main_menu_markup():
    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("🛒 Acheter hébergement", callback_data="menu_shop"),
        InlineKeyboardButton("💳 Recharger solde",     callback_data="menu_topup"),
        InlineKeyboardButton("📦 Mes commandes",       callback_data="my_orders"),
        InlineKeyboardButton("🧾 Mes transactions",    callback_data="my_transactions"),
        InlineKeyboardButton("💰 Mon solde",           callback_data="my_balance"),
        InlineKeyboardButton("ℹ️ À propos",            callback_data="menu_about"),
    )
    return m

@bot.message_handler(commands=['start'])
def send_main_menu(message):
    if not has_username(message.from_user):
        return bot.send_message(
            message.chat.id,
            "⚠️ *Un @username Telegram est obligatoire.*\n\n"
            "➡️ *Paramètres → Modifier le profil → Nom d'utilisateur*\n"
            "Crée ton @username puis reviens ici avec /start.",
            parse_mode="Markdown"
        )
    db_ensure_wallet(message.from_user.id, message.from_user.username)
    balance = db_get_balance(message.from_user.id)
    bot.send_message(
        message.chat.id,
        f"👋 *Bienvenue sur PLESK AUTOSHOP* 🛡️\n\n"
        f"💰 *Votre solde :* `{balance:.2f} €`\n\n"
        f"⚡ Hébergement web professionnel\n"
        f"💳 Rechargez en crypto · Payez en crédits\n"
        f"🚀 Livraison instantanée\n\n"
        f"Que souhaitez-vous faire ?",
        reply_markup=main_menu_markup(),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "back_main")
def back_main(call):
    bot.answer_callback_query(call.id)
    send_main_menu(call.message)

@bot.callback_query_handler(func=lambda c: c.data == "menu_about")
def menu_about(call):
    bot.answer_callback_query(call.id)
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.send_message(
        call.message.chat.id,
        "ℹ️ *PLESK AUTOSHOP*\n\n"
        "🖥️ Hébergement Plesk clé en main\n"
        "💳 *1 crédit = 1 €*\n\n"
        "Recharges acceptées :\n"
        "₿ BTC · Ξ ETH · ◎ SOL · Ł LTC · 💵 USDT\n\n"
        "🔒 Paiements via Plisio\n"
        f"🖥️ Serveur : `{SERVER_IP}`",
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
        InlineKeyboardButton("💳 Recharger",    callback_data="menu_topup"),
        InlineKeyboardButton("⬅️ Retour",       callback_data="back_main"),
    )
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"💰 *Votre solde*\n\n"
        f"💳 Crédits disponibles : `{balance:.2f} €`\n\n"
        f"_1 crédit = 1 € · Rechargez en crypto_",
        reply_markup=m, parse_mode="Markdown"
    )

# ==========================================
# 💳 RECHARGE SOLDE
# ==========================================
TOPUP_AMOUNTS = [5, 10, 20, 50, 100]   # montants prédéfinis en €

@bot.callback_query_handler(func=lambda c: c.data == "menu_topup")
def menu_topup(call):
    bot.answer_callback_query(call.id)
    m = InlineKeyboardMarkup(row_width=3)
    for amt in TOPUP_AMOUNTS:
        m.add(InlineKeyboardButton(f"💳 {amt} €", callback_data=f"topup_amt_{amt}"))
    m.add(InlineKeyboardButton("✏️ Montant personnalisé", callback_data="topup_custom"))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.send_message(
        call.message.chat.id,
        "💳 *RECHARGER LE SOLDE*\n\n"
        "1 crédit = 1 €\n"
        "Choisissez le montant à recharger :",
        reply_markup=m, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "topup_custom")
def topup_custom(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        "✏️ Entrez le montant en € à recharger (minimum 1€) :\n\n_Exemple : 25_",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, topup_custom_amount)

def topup_custom_amount(message):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount < 1:
            return bot.reply_to(message, "❌ Minimum 1€.")
        choose_crypto_topup(message, amount)
    except ValueError:
        bot.reply_to(message, "❌ Entrez un nombre valide (ex: 25 ou 25.50).")

@bot.callback_query_handler(func=lambda c: c.data.startswith("topup_amt_"))
def topup_amount(call):
    bot.answer_callback_query(call.id)
    amount = float(call.data.split("_")[2])
    choose_crypto_topup(call.message, amount)

def choose_crypto_topup(message, amount: float):
    m = InlineKeyboardMarkup(row_width=1)
    for code, label in CRYPTOS.items():
        m.add(InlineKeyboardButton(label, callback_data=f"topup_pay_{amount}_{code}"))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="menu_topup"))
    bot.send_message(
        message.chat.id,
        f"💳 *Recharge de {amount:.2f} €*\n\n"
        f"Choisissez votre crypto :",
        reply_markup=m, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("topup_pay_"))
def topup_pay(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    if is_rate_limited(call.from_user.id):
        return bot.answer_callback_query(call.id, "⏳ Patientez.", show_alert=True)

    parts    = call.data.split("_")  # topup_pay_<amount>_<currency>
    amount   = float(parts[2])
    currency = parts[3]
    label    = CRYPTOS.get(currency, currency)
    order_id = f"topup-{call.from_user.id}-{int(time.time())}"

    bot.answer_callback_query(call.id, "⏳ Génération de la facture...")

    invoice_url, plisio_id, amount_crypto = plisio_create_invoice(
        amount, currency, call.from_user.id, order_id
    )

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

    m = InlineKeyboardMarkup(row_width=1)
    m.add(InlineKeyboardButton(f"💸 Payer {amount_crypto} {currency}", url=invoice_url))
    m.add(InlineKeyboardButton("⬅️ Retour au menu", callback_data="back_main"))

    bot.send_message(
        call.message.chat.id,
        f"🧾 *FACTURE CRÉÉE*\n\n"
        f"💳 *Recharge :* `{amount:.2f} €` ({amount:.2f} crédits)\n"
        f"🔗 *Crypto :* {label}\n"
        f"💰 *Montant à envoyer :* `{amount_crypto}` {currency}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👆 Cliquez le bouton pour payer\n"
        f"✅ Votre solde sera crédité *automatiquement*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 *Réf :* `{plisio_id}`",
        reply_markup=m, parse_mode="Markdown"
    )

# ==========================================
# 🛒 CATALOGUE & ACHAT AVEC CRÉDITS
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "menu_shop")
def show_shop(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    db_ensure_wallet(call.from_user.id, call.from_user.username)
    balance = db_get_balance(call.from_user.id)
    plans   = get_plans()

    m = InlineKeyboardMarkup(row_width=1)
    for pid, plan in plans.items():
        suffix = " ✅" if balance >= plan["price"] else " ❌ Solde insuff."
        m.add(InlineKeyboardButton(
            f"{plan['emoji']} {plan['days']}J — {plan['price']:.2f}€ — {plan['desc']}{suffix}",
            callback_data=f"buy_{pid}"
        ))
    m.add(InlineKeyboardButton("💳 Recharger le solde", callback_data="menu_topup"))
    m.add(InlineKeyboardButton("⬅️ Retour",             callback_data="back_main"))

    bot.send_message(
        call.message.chat.id,
        f"🛒 *CATALOGUE*\n\n"
        f"💰 Votre solde : `{balance:.2f} €`\n\n"
        f"✅ = solde suffisant · ❌ = recharge nécessaire",
        reply_markup=m, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def buy_plan(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)

    plan_id = call.data[4:]
    plans   = get_plans()
    if plan_id not in plans:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)

    plan    = plans[plan_id]
    balance = db_get_balance(call.from_user.id)

    if balance < plan["price"]:
        missing = plan["price"] - balance
        m = InlineKeyboardMarkup(row_width=1)
        m.add(InlineKeyboardButton(
            f"💳 Recharger {missing:.2f}€", callback_data="menu_topup"))
        m.add(InlineKeyboardButton("⬅️ Retour", callback_data="menu_shop"))
        return bot.answer_callback_query(call.id), bot.send_message(
            call.message.chat.id,
            f"❌ *Solde insuffisant*\n\n"
            f"💰 Votre solde : `{balance:.2f} €`\n"
            f"💳 Prix de l'offre : `{plan['price']:.2f} €`\n"
            f"📉 Manque : `{missing:.2f} €`",
            reply_markup=m, parse_mode="Markdown"
        )

    # Confirmation avant achat
    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("✅ Confirmer l'achat", callback_data=f"confirm_{plan_id}"),
        InlineKeyboardButton("❌ Annuler",           callback_data="menu_shop"),
    )
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"🛒 *CONFIRMATION D'ACHAT*\n\n"
        f"📦 *Offre :* {plan['desc']}\n"
        f"⏰ *Durée :* {plan['days']} jours\n"
        f"💳 *Coût :* `{plan['price']:.2f} crédits`\n"
        f"💰 *Solde après :* `{balance - plan['price']:.2f} €`\n\n"
        f"Confirmer l'achat ?",
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

    # Vérif solde à nouveau (sécurité double-check)
    if balance < plan["price"]:
        return bot.answer_callback_query(
            call.id, "❌ Solde insuffisant.", show_alert=True)

    if is_rate_limited(call.from_user.id):
        return bot.answer_callback_query(call.id, "⏳ Patientez.", show_alert=True)

    bot.answer_callback_query(call.id, "⏳ Création de votre espace...")
    bot.edit_message_text(
        "⏳ *Provisionnement en cours...*",
        call.message.chat.id, call.message.message_id,
        parse_mode="Markdown"
    )

    # Débit AVANT provisionnement (atomique)
    db_deduct_balance(call.from_user.id, plan["price"])

    infos = provision_plesk(plan)

    if infos:
        db_add_order(
            tg_id=call.from_user.id,
            tg_username=call.from_user.username,
            plan_id=plan_id,
            plan_desc=plan["desc"],
            credits=plan["price"],
            domain=infos["domain"],
            username=infos["username"],
            password=infos["password"],
            expire=infos["expire"]
        )
        new_balance = db_get_balance(call.from_user.id)
        m = InlineKeyboardMarkup(row_width=1)
        m.add(
            InlineKeyboardButton("📦 Mes commandes",    callback_data="my_orders"),
            InlineKeyboardButton("💰 Mon solde",        callback_data="my_balance"),
            InlineKeyboardButton("🏠 Menu principal",   callback_data="back_main"),
        )
        bot.send_message(
            call.message.chat.id,
            f"🎉 *SERVEUR LIVRÉ !*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥️ *URL Panel Plesk :*\n"
            f"`https://{SERVER_IP}:8443`\n\n"
            f"👤 *Identifiant :* `{infos['username']}`\n"
            f"🔑 *Mot de passe :* `{infos['password']}`\n"
            f"📅 *Expiration :* `{infos['expire']}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 *Crédits restants :* `{new_balance:.2f} €`\n\n"
            f"_Retrouvez vos infos via 📦 Mes commandes._",
            reply_markup=m, parse_mode="Markdown"
        )
        send_log(
            f"🛒 *NOUVELLE VENTE*\n\n"
            f"👤 @{call.from_user.username} (`{call.from_user.id}`)\n"
            f"📦 {plan['desc']}\n"
            f"💳 -{plan['price']:.2f} crédits\n"
            f"🌐 `{infos['domain']}`\n"
            f"🖥️ `https://{SERVER_IP}:8443`\n"
            f"📅 Expire : {infos['expire']}"
        )
    else:
        # Remboursement si Plesk échoue
        db_add_balance(call.from_user.id, call.from_user.username, plan["price"])
        bot.send_message(
            call.message.chat.id,
            "⚠️ Erreur serveur. Vos crédits ont été remboursés automatiquement."
        )
        send_log(
            f"⚠️ *ERREUR PLESK + REMBOURSEMENT*\n"
            f"@{call.from_user.username} (`{call.from_user.id}`)\n"
            f"Plan : {plan['desc']} — Remboursé : {plan['price']:.2f}€"
        )

# ==========================================
# 📦 COMMANDES & TRANSACTIONS CLIENT
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "my_orders")
def my_orders(call):
    rows = db_get_orders(call.from_user.id)
    if not rows:
        return bot.answer_callback_query(call.id, "📭 Aucune commande.", show_alert=True)
    text = "📦 *Vos commandes :*\n\n"
    for row in rows:
        status = "✅ Actif" if row["active"] else "❌ Expiré"
        text += (
            f"{status} *{row['plan_desc']}*\n"
            f"⏰ Expire : `{row['expire_date']}`\n"
            f"🖥️ `https://{SERVER_IP}:8443`\n"
            f"👤 `{row['plesk_user']}` · 🔑 `{row['plesk_pass']}`\n\n"
        )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "my_transactions")
def my_transactions(call):
    rows = db_get_transactions(call.from_user.id)
    if not rows:
        return bot.answer_callback_query(call.id, "📭 Aucune transaction.", show_alert=True)
    text = "🧾 *Vos transactions (15 dernières) :*\n\n"
    for row in rows:
        icon = "✅" if row["status"] == "confirmed" else "⏳"
        text += (
            f"{icon} *+{row['amount_eur']:.2f} €* via `{row['currency']}`\n"
            f"   📅 {row['created_at']} · `{row['status']}`\n"
            f"   🆔 `{row['plisio_id']}`\n\n"
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
        InlineKeyboardButton("👥 Commandes",          callback_data="adm_orders_0"),
        InlineKeyboardButton("📊 Statistiques",       callback_data="adm_stats"),
        InlineKeyboardButton("💰 Soldes clients",     callback_data="adm_wallets"),
        InlineKeyboardButton("🔍 Chercher client",    callback_data="adm_search"),
        InlineKeyboardButton("✏️ Modifier les prix",  callback_data="adm_prices"),
        InlineKeyboardButton("💳 Créditer un client", callback_data="adm_credit"),
    )
    return m

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if not is_admin(message.from_user.id): return
    s = db_stats()
    bot.send_message(
        message.chat.id,
        f"👑 *Panel Admin*\n\n"
        f"🖥️ Serveur : `{SERVER_IP}`\n"
        f"🛒 Commandes : *{s['orders_total']}* (actives: {s['orders_active']})\n"
        f"💰 CA ventes : *{s['orders_revenue']:.2f} €*\n"
        f"💳 Recharges : *{s['topups_total']}* · *{s['topups_revenue']:.2f} €*",
        reply_markup=admin_markup(), parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def adm_stats(call):
    if not is_admin(call.from_user.id): return
    s  = db_stats()
    bd = "\n".join([f"  • {r[0]} : {r[1]}" for r in s["breakdown"]]) or "  Aucune"
    text = (
        f"📊 *Statistiques*\n\n"
        f"🛒 Commandes total : *{s['orders_total']}*\n"
        f"✅ Actives : *{s['orders_active']}*\n"
        f"💰 CA ventes : *{s['orders_revenue']:.2f} €*\n\n"
        f"💳 Recharges confirmées : *{s['topups_total']}*\n"
        f"💵 Total rechargé : *{s['topups_revenue']:.2f} €*\n\n"
        f"📦 *Par offre :*\n{bd}"
    )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                          reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_orders_"))
def adm_orders(call):
    if not is_admin(call.from_user.id): return
    page     = int(call.data.split("_")[2])
    per_page = 4
    rows     = db_get_all_orders()
    total    = len(rows)
    if total == 0:
        return bot.answer_callback_query(call.id, "Aucune commande.", show_alert=True)
    start = page * per_page
    chunk = rows[start:start + per_page]
    text  = f"🛒 *Commandes ({start+1}–{min(start+per_page,total)} / {total})*\n\n"
    for row in chunk:
        text += order_card(row) + "\n"
    m = InlineKeyboardMarkup(row_width=3)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm_orders_{page-1}"))
    nav.append(InlineKeyboardButton(f"· {page+1} ·", callback_data="noop"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"adm_orders_{page+1}"))
    if nav: m.add(*nav)
    m.add(InlineKeyboardButton("🔍 Creds par ID", callback_data="adm_creds_ask"))
    m.add(InlineKeyboardButton("⬅️ Retour",       callback_data="adm_back"))
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=m, parse_mode="Markdown")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_wallets")
def adm_wallets(call):
    if not is_admin(call.from_user.id): return
    rows = db_get_all_wallets()
    if not rows:
        return bot.answer_callback_query(call.id, "Aucun client.", show_alert=True)
    text = "💰 *Soldes clients :*\n\n"
    for row in rows:
        text += f"@{row['tg_username']} (`{row['tg_id']}`) — `{row['balance']:.2f} €`\n"
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=m, parse_mode="Markdown")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_credit")
def adm_credit(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(
        call.message.chat.id,
        "💳 *Créditer un client*\n\nEntrez : `@username montant`\nEx : `@monuser 10`",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, adm_credit_do)

def adm_credit_do(message):
    if not is_admin(message.from_user.id): return
    try:
        parts    = message.text.strip().split()
        username = parts[0].lstrip("@")
        amount   = float(parts[1])
        # Trouve le tg_id
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT tg_id FROM wallets WHERE tg_username = ?", (username,))
        row = cur.fetchone()
        con.close()
        if not row:
            return bot.reply_to(message, "❌ Client introuvable (doit avoir utilisé /start).")
        tg_id = row[0]
        db_add_balance(tg_id, username, amount)
        new_bal = db_get_balance(tg_id)
        bot.reply_to(message, f"✅ *+{amount:.2f} €* crédités à @{username}\nNouveau solde : `{new_bal:.2f} €`", parse_mode="Markdown")
        try:
            bot.send_message(tg_id, f"💳 *{amount:.2f} crédits* ont été ajoutés à votre solde par un administrateur.\n💰 Nouveau solde : `{new_bal:.2f} €`", parse_mode="Markdown")
        except Exception:
            pass
        send_log(f"💳 *CRÉDIT ADMIN*\n@{username} +{amount:.2f}€ → solde : {new_bal:.2f}€")
    except (IndexError, ValueError):
        bot.reply_to(message, "❌ Format : `@username montant` — ex: `@monuser 10`", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_search")
def adm_search(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id,
                           "🔍 *@username* ou *ID Telegram* du client :", parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_search_result)

def adm_search_result(message):
    if not is_admin(message.from_user.id): return
    query  = message.text.strip().lstrip("@")
    orders = db_get_by_tg_query(query)
    con    = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur    = con.cursor()
    cur.execute("SELECT * FROM wallets WHERE tg_username = ? OR CAST(tg_id AS TEXT) = ?", (query, query))
    wallet = cur.fetchone()
    con.close()
    if not orders and not wallet:
        return bot.reply_to(message, "❌ Aucun client trouvé.")
    text = f"🔍 *Résultats pour* `{query}` :\n\n"
    if wallet:
        text += f"💰 *Solde :* `{wallet['balance']:.2f} €`\n\n"
    for row in orders:
        text += order_card(row, show_creds=True) + "\n"
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

def db_get_by_tg_query(query):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM orders WHERE tg_username = ? OR CAST(tg_id AS TEXT) = ? ORDER BY id DESC",
                (query, query))
    rows = cur.fetchall()
    con.close()
    return rows

@bot.callback_query_handler(func=lambda c: c.data == "adm_creds_ask")
def adm_creds_ask(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id, "🔐 ID de la commande :", parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_creds_show)

def adm_creds_show(message):
    if not is_admin(message.from_user.id): return
    try:
        row = db_get_order(int(message.text.strip()))
        if not row:
            return bot.reply_to(message, "❌ Commande introuvable.")
        m = InlineKeyboardMarkup(row_width=2)
        m.add(
            InlineKeyboardButton("🚫 Désactiver", callback_data=f"adm_off_{row['id']}"),
            InlineKeyboardButton("✅ Réactiver",  callback_data=f"adm_on_{row['id']}"),
        )
        bot.send_message(message.chat.id, order_card(row, show_creds=True),
                         reply_markup=m, parse_mode="Markdown")
    except ValueError:
        bot.reply_to(message, "❌ Entrez un nombre entier.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_off_") or c.data.startswith("adm_on_"))
def adm_toggle(call):
    if not is_admin(call.from_user.id): return
    parts  = call.data.split("_")
    action = parts[1]
    rid    = int(parts[2])
    state  = 0 if action == "off" else 1
    db_set_order_active(rid, state)
    label  = "désactivé ❌" if state == 0 else "réactivé ✅"
    bot.answer_callback_query(call.id, f"Compte {label}.")
    row = db_get_order(rid)
    if row:
        bot.edit_message_text(order_card(row, show_creds=True),
                              call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown")

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
    text = "✏️ *Modifier les prix*\n\n" + "\n".join(
        [f"• {p['desc']} : `{p['price']:.2f} €`" for p in plans.values()]
    )
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=m, parse_mode="Markdown")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_editprice_"))
def adm_editprice(call):
    if not is_admin(call.from_user.id): return
    plan_id = call.data.replace("adm_editprice_", "")
    plans   = get_plans()
    plan    = plans.get(plan_id)
    if not plan:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)
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
            return bot.reply_to(message, "❌ Prix doit être positif.")
        plans = get_plans()
        old   = plans[plan_id]["price"]
        plans[plan_id]["price"] = new_price
        save_plans(plans)
        bot.reply_to(message,
                     f"✅ Prix mis à jour !\n*{plans[plan_id]['desc']}* : `{old:.2f}€` → `{new_price:.2f}€`",
                     parse_mode="Markdown")
        send_log(f"✏️ *PRIX MODIFIÉ*\n{plans[plan_id]['desc']}\n{old:.2f}€ → {new_price:.2f}€")
    except ValueError:
        bot.reply_to(message, "❌ Entrez un nombre valide.")

@bot.callback_query_handler(func=lambda c: c.data == "adm_back")
def adm_back(call):
    if not is_admin(call.from_user.id): return
    s = db_stats()
    bot.edit_message_text(
        f"👑 *Panel Admin*\n\n"
        f"🖥️ Serveur : `{SERVER_IP}`\n"
        f"🛒 Commandes : *{s['orders_total']}* (actives: {s['orders_active']})\n"
        f"💰 CA ventes : *{s['orders_revenue']:.2f} €*\n"
        f"💳 Recharges : *{s['topups_total']}* · *{s['topups_revenue']:.2f} €*",
        call.message.chat.id, call.message.message_id,
        reply_markup=admin_markup(), parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def noop(call): bot.answer_callback_query(call.id)

# ==========================================
# 🚀 DÉMARRAGE (Bot + Webhook en parallèle)
# ==========================================
def run_flask():
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    init_db()
    logger.info(f"🚀 Démarrage — IP : {SERVER_IP}")

    # Lance Flask en thread séparé
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"🌐 Webhook Flask actif sur le port {WEBHOOK_PORT}")
    logger.info(f"📡 URL callback : http://{SERVER_IP}:{WEBHOOK_PORT}/plisio_callback")

    bot.infinity_polling(timeout=20, long_polling_timeout=15)
