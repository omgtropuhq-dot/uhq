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

# ==========================================
# ⚙️ CONFIGURATION — REMPLIS ICI
# ==========================================
BOT_TOKEN       = "TON_TOKEN_ICI"
NOWPAYMENTS_KEY = "TA_CLE_NOWPAYMENTS_ICI"
ADMIN_IDS       = [TON_ID_ICI]
LOG_GROUP_ID    = TON_LOG_GROUP_ICI
DB_PATH         = "customers.db"
NOWPAY_API      = "https://api.nowpayments.io/v1"
COOLDOWN_SEC    = 15

# Cryptos acceptées (currency_code NOWPayments : label affiché)
CRYPTOS = {
    "usdttrc20": "USDT (TRC-20)",
    "eth":       "Ethereum (ETH)",
    "sol":       "Solana (SOL)",
    "btc":       "Bitcoin (BTC)",
    "ltc":       "Litecoin (LTC)",
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

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# 📦 PLANS (prix modifiables par admin)
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id         INTEGER NOT NULL,
            tg_username   TEXT    NOT NULL,
            plan_id       TEXT    NOT NULL,
            plan_desc     TEXT    NOT NULL,
            plesk_domain  TEXT    NOT NULL,
            plesk_user    TEXT    NOT NULL,
            plesk_pass    TEXT    NOT NULL,
            price_paid    REAL    NOT NULL,
            currency      TEXT    DEFAULT 'USDT',
            purchase_date TEXT    NOT NULL,
            expire_date   TEXT    NOT NULL,
            payment_id    TEXT    NOT NULL,
            active        INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payment_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id       INTEGER NOT NULL,
            tg_username TEXT    NOT NULL,
            plan_desc   TEXT    NOT NULL,
            amount      REAL    NOT NULL,
            currency    TEXT    NOT NULL,
            payment_id  TEXT    NOT NULL,
            status      TEXT    NOT NULL,
            date        TEXT    NOT NULL
        )
    """)
    con.commit()
    con.close()
    logger.info("DB initialisée.")

def db_add_customer(tg_id, tg_username, plan_id, plan_desc,
                    domain, username, password, price, currency, expire_date, payment_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO customers
            (tg_id, tg_username, plan_id, plan_desc, plesk_domain, plesk_user,
             plesk_pass, price_paid, currency, purchase_date, expire_date, payment_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tg_id, tg_username, plan_id, plan_desc, domain,
        username, password, price, currency,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        expire_date, payment_id
    ))
    con.commit()
    con.close()

def db_add_payment_history(tg_id, tg_username, plan_desc, amount, currency, payment_id, status):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO payment_history
            (tg_id, tg_username, plan_desc, amount, currency, payment_id, status, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tg_id, tg_username, plan_desc, amount, currency, payment_id, status,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ))
    con.commit()
    con.close()

def db_update_history_status(payment_id, status):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE payment_history SET status = ? WHERE payment_id = ?", (status, payment_id))
    con.commit()
    con.close()

def db_get_all_customers():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM customers ORDER BY id DESC")
    rows = cur.fetchall()
    con.close()
    return rows

def db_get_by_tg(query):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        "SELECT * FROM customers WHERE tg_username = ? OR CAST(tg_id AS TEXT) = ? ORDER BY id DESC",
        (query, query)
    )
    rows = cur.fetchall()
    con.close()
    return rows

def db_get_by_domain(domain):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM customers WHERE plesk_domain = ?", (domain,))
    row = cur.fetchone()
    con.close()
    return row

def db_get_by_id(record_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM customers WHERE id = ?", (record_id,))
    row = cur.fetchone()
    con.close()
    return row

def db_get_by_tg_id(tg_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM customers WHERE tg_id = ? ORDER BY id DESC", (tg_id,))
    rows = cur.fetchall()
    con.close()
    return rows

def db_get_payment_history(tg_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM payment_history WHERE tg_id = ? ORDER BY id DESC LIMIT 15", (tg_id,))
    rows = cur.fetchall()
    con.close()
    return rows

def db_set_active(record_id, state):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE customers SET active = ? WHERE id = ?", (state, record_id))
    con.commit()
    con.close()

def db_stats():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*), SUM(price_paid) FROM customers")
    total_row = cur.fetchone()
    cur.execute("SELECT plan_desc, COUNT(*) FROM customers GROUP BY plan_desc")
    breakdown = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM customers WHERE active = 1")
    active = cur.fetchone()[0]
    con.close()
    return {
        "total":     total_row[0] or 0,
        "revenue":   total_row[1] or 0.0,
        "active":    active,
        "breakdown": breakdown,
    }

# ==========================================
# 🛠️ UTILITAIRES
# ==========================================
_cooldown: dict[int, float] = {}
_pending_crypto: dict[int, str] = {}   # user_id -> plan_id en attente choix crypto

def gen_password(length=12):
    chars = string.ascii_letters + string.digits + "!@#$"
    return ''.join(random.choices(chars, k=length))

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

def customer_card(row, show_creds=False):
    status = "✅ Actif" if row["active"] else "❌ Inactif"
    card = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *ID :* `{row['id']}`\n"
        f"👤 *Telegram :* @{row['tg_username']} (`{row['tg_id']}`)\n"
        f"📦 *Offre :* {row['plan_desc']}\n"
        f"💵 *Payé :* `{row['price_paid']:.2f} USD`\n"
        f"📅 *Achat :* {row['purchase_date']}\n"
        f"⏰ *Expire :* {row['expire_date']}\n"
        f"🌐 *Domaine :* `{row['plesk_domain']}`\n"
        f"🔖 *Statut :* {status}\n"
    )
    if show_creds:
        card += (
            f"👤 *Login :* `{row['plesk_user']}`\n"
            f"🔑 *Password :* `{row['plesk_pass']}`\n"
            f"🖥️ *Panel :* `https://{SERVER_IP}:8443`\n"
        )
    return card

# ==========================================
# 💳 NOWPAYMENTS — CORRECTION AMOUNT_MINIMAL
# ==========================================
def get_min_amount(currency_code: str) -> float:
    """Récupère le montant minimum accepté par NOWPayments pour une crypto."""
    try:
        headers = {"x-api-key": NOWPAYMENTS_KEY}
        res = requests.get(
            f"{NOWPAY_API}/min-amount",
            headers=headers,
            params={"currency_from": currency_code, "currency_to": currency_code},
            timeout=10
        ).json()
        return float(res.get("min_amount", 0))
    except Exception:
        return 0.0

def create_payment(amount: float, order_id: str, description: str, currency_code: str):
    """
    Crée un paiement NOWPayments avec vérification du minimum.
    Retourne (adresse, montant_crypto, devise, payment_id) ou (None,None,None,None).
    """
    try:
        headers = {
            "x-api-key":    NOWPAYMENTS_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "price_amount":        amount,
            "price_currency":      "usd",
            "pay_currency":        currency_code,
            "order_id":            order_id,
            "order_description":   description,
            "is_fixed_rate":       False,   # False = NOWPayments gère le taux
            "is_fee_paid_by_user": True,    # Le client paie les frais réseau
        }
        res = requests.post(
            f"{NOWPAY_API}/payment",
            headers=headers, json=payload, timeout=10
        ).json()

        if "payment_id" in res:
            return (
                res.get("pay_address", "—"),
                float(res.get("pay_amount", amount)),
                res.get("pay_currency", currency_code).upper(),
                str(res["payment_id"])
            )

        # Gestion erreur AMOUNT_MINIMAL_ERROR
        if res.get("code") == "AMOUNT_MINIMAL_ERROR":
            logger.warning(f"Montant trop faible pour {currency_code}, récupération du minimum...")
            min_amt = get_min_amount(currency_code)
            logger.info(f"Minimum pour {currency_code} : {min_amt}")
            return None, None, None, "AMOUNT_TOO_LOW"

        logger.warning(f"NOWPayments echec : {res}")

    except Exception as e:
        logger.error(f"NOWPayments create error : {e}")
    return None, None, None, None

def check_payment(payment_id: str) -> str:
    try:
        headers = {"x-api-key": NOWPAYMENTS_KEY}
        res = requests.get(
            f"{NOWPAY_API}/payment/{payment_id}",
            headers=headers, timeout=10
        ).json()
        return res.get("payment_status", "error")
    except Exception as e:
        logger.error(f"checkPayment error : {e}")
    return "error"

def get_available_currencies() -> list[str]:
    """Retourne la liste des cryptos disponibles sur NOWPayments."""
    try:
        headers = {"x-api-key": NOWPAYMENTS_KEY}
        res = requests.get(f"{NOWPAY_API}/currencies", headers=headers, timeout=10).json()
        return [c.lower() for c in res.get("currencies", [])]
    except Exception:
        return list(CRYPTOS.keys())

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
# 🏠 MENU PRINCIPAL CLIENT
# ==========================================
def main_menu_markup():
    m = InlineKeyboardMarkup(row_width=2)
    m.add(
        InlineKeyboardButton("🛒 Acheter un hébergement", callback_data="menu_shop"),
        InlineKeyboardButton("📦 Mes commandes",           callback_data="my_orders"),
        InlineKeyboardButton("🧾 Historique paiements",    callback_data="pay_history"),
        InlineKeyboardButton("ℹ️ À propos",                callback_data="menu_about"),
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
    bot.send_message(
        message.chat.id,
        f"👋 *Bienvenue sur PLESK AUTOSHOP* 🛡️\n\n"
        f"🖥️ Hébergement web professionnel\n"
        f"⚡ Livraison instantanée après paiement\n"
        f"💳 +300 cryptos acceptées\n\n"
        f"Que souhaitez-vous faire ?",
        reply_markup=main_menu_markup(),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "menu_about")
def menu_about(call):
    bot.answer_callback_query(call.id)
    text = (
        "ℹ️ *À propos de PLESK AUTOSHOP*\n\n"
        "🖥️ Nous fournissons des hébergements Plesk\n"
        "   clé en main, livrés automatiquement.\n\n"
        "💳 *Paiements acceptés :*\n"
        "   USDT · ETH · SOL · BTC · LTC\n\n"
        "🔒 *Sécurité :* Paiements via NOWPayments\n"
        "📞 *Support :* Contactez un admin\n\n"
        f"🖥️ *Serveur :* `{SERVER_IP}`"
    )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "back_main")
def back_main(call):
    bot.answer_callback_query(call.id)
    send_main_menu(call.message)

# ==========================================
# 🛒 CATALOGUE
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "menu_shop")
def show_shop(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    plans = get_plans()
    m = InlineKeyboardMarkup(row_width=1)
    for pid, plan in plans.items():
        m.add(InlineKeyboardButton(
            f"{plan['emoji']}  {plan['days']} JOURS  —  {plan['price']:.2f}$  —  {plan['desc']}",
            callback_data=f"buy_{pid}"
        ))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.send_message(
        call.message.chat.id,
        "🛒 *CATALOGUE DES OFFRES*\n\n"
        "Choisissez votre hébergement :\n\n"
        "✅ Panel Plesk complet\n"
        "✅ Accès immédiat\n"
        "✅ Support inclus",
        reply_markup=m, parse_mode="Markdown"
    )

# ==========================================
# 💰 CHOIX DE LA CRYPTO
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data.startswith('buy_'))
def choose_crypto(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    plan_id = call.data[4:]
    plans   = get_plans()
    if plan_id not in plans:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)
    if is_rate_limited(call.from_user.id):
        return bot.answer_callback_query(call.id, "⏳ Patientez quelques secondes.", show_alert=True)

    plan = plans[plan_id]
    _pending_crypto[call.from_user.id] = plan_id

    m = InlineKeyboardMarkup(row_width=1)
    for code, label in CRYPTOS.items():
        m.add(InlineKeyboardButton(
            f"💳 {label}",
            callback_data=f"crypto_{plan_id}_{code}"
        ))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="menu_shop"))

    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"💳 *CHOISIR LA CRYPTO*\n\n"
        f"📦 Offre : *{plan['desc']}*\n"
        f"💵 Prix : `{plan['price']:.2f} USD`\n\n"
        f"Avec quelle crypto souhaitez-vous payer ?",
        reply_markup=m, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith('crypto_'))
def process_payment(call):
    parts = call.data.split('_', 2)
    if len(parts) != 3:
        return bot.answer_callback_query(call.id, "❌ Données invalides.", show_alert=True)

    _, plan_id, currency_code = parts
    plans = get_plans()
    if plan_id not in plans:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)

    plan     = plans[plan_id]
    order_id = f"{call.from_user.id}-{plan_id}-{int(time.time())}"
    label    = CRYPTOS.get(currency_code, currency_code.upper())

    bot.answer_callback_query(call.id, "⏳ Création du paiement...")

    pay_address, pay_amount, pay_currency, payment_id = create_payment(
        plan["price"], order_id, f"Plesk {plan['desc']}", currency_code
    )

    if payment_id == "AMOUNT_TOO_LOW":
        return bot.send_message(
            call.message.chat.id,
            f"⚠️ *Montant trop faible pour {label}*\n\n"
            f"Le prix de l'offre (`{plan['price']:.2f} USD`) est inférieur au minimum requis "
            f"par le réseau {label}.\n\n"
            f"👉 Choisissez une autre crypto comme *USDT TRC-20* ou *LTC* qui ont des minimums très bas.",
            parse_mode="Markdown"
        )

    if not pay_address:
        return bot.send_message(
            call.message.chat.id,
            "❌ Service de paiement indisponible. Réessayez dans quelques minutes."
        )

    db_add_payment_history(
        tg_id=call.from_user.id,
        tg_username=call.from_user.username,
        plan_desc=plan["desc"],
        amount=pay_amount,
        currency=pay_currency,
        payment_id=payment_id,
        status="waiting"
    )

    m = InlineKeyboardMarkup(row_width=1)
    m.add(InlineKeyboardButton(
        "🔄 Vérifier mon paiement",
        callback_data=f"check_{payment_id}_{plan_id}"
    ))
    m.add(InlineKeyboardButton("⬅️ Retour au menu", callback_data="back_main"))

    bot.send_message(
        call.message.chat.id,
        f"🧾 *FACTURE CRÉÉE*\n\n"
        f"📦 *Offre :* {plan['desc']}\n"
        f"💵 *Prix :* `{plan['price']:.2f} USD`\n"
        f"💳 *Crypto :* {label}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 *Envoyez exactement :*\n"
        f"`{pay_amount}` *{pay_currency}*\n\n"
        f"📬 *Adresse de paiement :*\n"
        f"`{pay_address}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ *Validité :* 60 min\n"
        f"🆔 *Réf :* `{payment_id}`\n\n"
        f"1️⃣ Envoyez le montant exact\n"
        f"2️⃣ Appuyez sur *Vérifier mon paiement*",
        reply_markup=m, parse_mode="Markdown"
    )

# ==========================================
# ✅ VÉRIFICATION PAIEMENT
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data.startswith('check_'))
def verify_payment(call):
    parts = call.data.split('_', 2)
    if len(parts) != 3:
        return bot.answer_callback_query(call.id, "❌ Données invalides.", show_alert=True)

    _, payment_id, plan_id = parts
    plans = get_plans()
    if plan_id not in plans:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)

    plan   = plans[plan_id]
    status = check_payment(payment_id)
    db_update_history_status(payment_id, status)

    if status == "finished":
        bot.answer_callback_query(call.id, "✅ Paiement confirmé !")
        bot.edit_message_text(
            "✅ *Paiement confirmé !*\n⏳ Création de votre espace en cours...",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        infos       = provision_plesk(plan)
        tg_username = call.from_user.username

        if infos:
            db_add_customer(
                tg_id=call.from_user.id, tg_username=tg_username,
                plan_id=plan_id, plan_desc=plan["desc"],
                domain=infos["domain"], username=infos["username"],
                password=infos["password"], price=plan["price"],
                currency="USD", expire_date=infos["expire"],
                payment_id=payment_id
            )
            m = InlineKeyboardMarkup(row_width=1)
            m.add(
                InlineKeyboardButton("📦 Mes commandes",        callback_data="my_orders"),
                InlineKeyboardButton("🧾 Historique paiements", callback_data="pay_history"),
                InlineKeyboardButton("🏠 Menu principal",       callback_data="back_main"),
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
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"_Retrouvez vos infos via 📦 Mes commandes._",
                reply_markup=m, parse_mode="Markdown"
            )
            send_log(
                f"💰 *NOUVELLE VENTE*\n\n"
                f"👤 @{tg_username} (`{call.from_user.id}`)\n"
                f"📦 {plan['desc']}\n"
                f"💵 +{plan['price']:.2f} USD\n"
                f"🌐 `{infos['domain']}`\n"
                f"🖥️ `https://{SERVER_IP}:8443`\n"
                f"📅 Expire : {infos['expire']}"
            )
        else:
            bot.send_message(
                call.message.chat.id,
                "⚠️ Paiement reçu mais erreur serveur. Notre équipe a été alertée."
            )
            send_log(
                f"⚠️ *ERREUR PROVISIONNEMENT*\n"
                f"@{tg_username} (`{call.from_user.id}`) a payé mais Plesk a échoué !\n"
                f"Paiement : `{payment_id}` — {plan['desc']}"
            )

    elif status == "confirming":
        bot.answer_callback_query(
            call.id, "🔄 Paiement détecté, confirmation blockchain en cours (1–3 min).", show_alert=True)
    elif status == "waiting":
        bot.answer_callback_query(
            call.id, "⏳ Paiement non reçu. Vérifiez que vous avez envoyé le bon montant.", show_alert=True)
    elif status == "partially_paid":
        bot.answer_callback_query(
            call.id, "⚠️ Montant partiel reçu. Envoyez le reste à la même adresse.", show_alert=True)
    elif status == "expired":
        bot.answer_callback_query(
            call.id, "❌ Paiement expiré. Revenez au menu pour recommencer.", show_alert=True)
    elif status == "failed":
        bot.answer_callback_query(
            call.id, "❌ Paiement échoué. Contactez le support.", show_alert=True)
    else:
        bot.answer_callback_query(
            call.id, f"❓ Statut : {status}. Réessayez.", show_alert=True)

# ==========================================
# 📦 MES COMMANDES & HISTORIQUE
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "my_orders")
def my_orders(call):
    rows = db_get_by_tg_id(call.from_user.id)
    if not rows:
        return bot.answer_callback_query(call.id, "📭 Aucune commande.", show_alert=True)
    text = "📦 *Vos commandes :*\n\n"
    for row in rows:
        status = "✅ Actif" if row["active"] else "❌ Expiré/Inactif"
        text += (
            f"{status} *{row['plan_desc']}*\n"
            f"⏰ Expire : `{row['expire_date']}`\n"
            f"🖥️ Panel : `https://{SERVER_IP}:8443`\n"
            f"👤 Login : `{row['plesk_user']}`\n"
            f"🔑 Pass : `{row['plesk_pass']}`\n\n"
        )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_main"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "pay_history")
def pay_history(call):
    rows = db_get_payment_history(call.from_user.id)
    if not rows:
        return bot.answer_callback_query(call.id, "📭 Aucun paiement.", show_alert=True)
    text = "🧾 *Historique paiements (15 derniers) :*\n\n"
    for row in rows:
        icon = "✅" if row["status"] == "finished" else ("⏳" if row["status"] in ("waiting","confirming","partially_paid") else "❌")
        text += (
            f"{icon} *{row['plan_desc']}*\n"
            f"   `{row['amount']:.6f} {row['currency']}` · {row['date']}\n"
            f"   Statut : `{row['status']}`\n"
            f"   🆔 `{row['payment_id']}`\n\n"
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
        InlineKeyboardButton("👥 Liste clients",        callback_data="adm_list_0"),
        InlineKeyboardButton("📊 Statistiques",         callback_data="adm_stats"),
        InlineKeyboardButton("🔍 Chercher par @",       callback_data="adm_search_tg"),
        InlineKeyboardButton("🔍 Chercher par domaine", callback_data="adm_search_domain"),
        InlineKeyboardButton("🔐 Creds par ID",         callback_data="adm_creds_ask"),
        InlineKeyboardButton("💰 Modifier les prix",    callback_data="adm_prices"),
    )
    return m

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if not is_admin(message.from_user.id):
        return
    s = db_stats()
    text = (
        f"👑 *Panel Admin*\n\n"
        f"🖥️ *Serveur :* `{SERVER_IP}`\n"
        f"👥 Total : *{s['total']}*\n"
        f"✅ Actifs : *{s['active']}*\n"
        f"💰 Revenus : *{s['revenue']:.2f} USD*"
    )
    bot.send_message(message.chat.id, text, reply_markup=admin_markup(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def adm_stats(call):
    if not is_admin(call.from_user.id): return
    s  = db_stats()
    bd = "\n".join([f"  • {r[0]} : {r[1]} vente(s)" for r in s["breakdown"]]) or "  Aucune"
    text = (
        f"📊 *Statistiques*\n\n"
        f"👥 Total : *{s['total']}*\n"
        f"✅ Actifs : *{s['active']}*  ❌ Inactifs : *{s['total']-s['active']}*\n"
        f"💰 Revenus : *{s['revenue']:.2f} USD*\n\n"
        f"📦 *Par offre :*\n{bd}"
    )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                          reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_list_"))
def adm_list(call):
    if not is_admin(call.from_user.id): return
    page     = int(call.data.split("_")[2])
    per_page = 4
    rows     = db_get_all_customers()
    total    = len(rows)
    if total == 0:
        return bot.answer_callback_query(call.id, "Aucun client.", show_alert=True)
    start = page * per_page
    chunk = rows[start:start + per_page]
    text  = f"👥 *Clients ({start+1}–{min(start+per_page,total)} / {total})*\n\n"
    for row in chunk:
        text += customer_card(row) + "\n"
    m = InlineKeyboardMarkup(row_width=3)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm_list_{page-1}"))
    nav.append(InlineKeyboardButton(f"· {page+1} ·", callback_data="noop"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"adm_list_{page+1}"))
    if nav: m.add(*nav)
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=m, parse_mode="Markdown")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_creds_ask")
def adm_creds_ask(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id,
                           "🔐 Entrez l'*ID DB* du client :", parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_creds_show)

def adm_creds_show(message):
    if not is_admin(message.from_user.id): return
    try:
        row = db_get_by_id(int(message.text.strip()))
        if not row:
            return bot.reply_to(message, "❌ Aucun client avec cet ID.")
        m = InlineKeyboardMarkup(row_width=2)
        m.add(
            InlineKeyboardButton("🚫 Désactiver", callback_data=f"adm_off_{row['id']}"),
            InlineKeyboardButton("✅ Réactiver",  callback_data=f"adm_on_{row['id']}"),
        )
        bot.send_message(message.chat.id, customer_card(row, show_creds=True),
                         reply_markup=m, parse_mode="Markdown")
    except ValueError:
        bot.reply_to(message, "❌ Entrez un nombre entier.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_off_") or
                                            c.data.startswith("adm_on_"))
def adm_toggle(call):
    if not is_admin(call.from_user.id): return
    parts  = call.data.split("_")
    action = parts[1]
    rid    = int(parts[2])
    state  = 0 if action == "off" else 1
    db_set_active(rid, state)
    label  = "désactivé ❌" if state == 0 else "réactivé ✅"
    bot.answer_callback_query(call.id, f"Compte {label}.")
    row = db_get_by_id(rid)
    if row:
        bot.edit_message_text(customer_card(row, show_creds=True),
                              call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_search_tg")
def adm_search_tg(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id,
                           "🔍 *@username* ou *ID Telegram* :", parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_search_tg_result)

def adm_search_tg_result(message):
    if not is_admin(message.from_user.id): return
    query = message.text.strip().lstrip("@")
    rows  = db_get_by_tg(query)
    if not rows:
        return bot.reply_to(message, "❌ Aucun client trouvé.")
    text = f"🔍 *Résultats `{query}` ({len(rows)}) :*\n\n"
    for row in rows:
        text += customer_card(row, show_creds=True) + "\n"
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_search_domain")
def adm_search_domain(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id,
                           "🔍 Domaine (ex: `client-abc123.local`) :", parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_search_domain_result)

def adm_search_domain_result(message):
    if not is_admin(message.from_user.id): return
    row = db_get_by_domain(message.text.strip())
    if not row:
        return bot.reply_to(message, "❌ Domaine introuvable.")
    bot.send_message(message.chat.id, customer_card(row, show_creds=True), parse_mode="Markdown")

# ==========================================
# 💰 MODIFIER LES PRIX (ADMIN)
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "adm_prices")
def adm_prices(call):
    if not is_admin(call.from_user.id): return
    plans = get_plans()
    text  = "💰 *Modifier les prix*\n\nPrix actuels :\n\n"
    for pid, plan in plans.items():
        text += f"  • *{plan['desc']}* : `{plan['price']:.2f} USD`\n"
    text += "\nChoisissez l'offre à modifier :"
    m = InlineKeyboardMarkup(row_width=1)
    for pid, plan in plans.items():
        m.add(InlineKeyboardButton(
            f"{plan['emoji']} {plan['desc']} — {plan['price']:.2f}$",
            callback_data=f"adm_editprice_{pid}"
        ))
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                          reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_editprice_"))
def adm_editprice(call):
    if not is_admin(call.from_user.id): return
    plan_id = call.data.replace("adm_editprice_", "")
    plans   = get_plans()
    if plan_id not in plans:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)
    plan = plans[plan_id]
    msg  = bot.send_message(
        call.message.chat.id,
        f"💰 Entrez le nouveau prix pour *{plan['desc']}* (actuel : `{plan['price']:.2f} USD`) :\n\n"
        f"_Exemple : 12.50_",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, lambda m: adm_saveprice(m, plan_id))

def adm_saveprice(message, plan_id):
    if not is_admin(message.from_user.id): return
    try:
        new_price = float(message.text.strip().replace(",", "."))
        if new_price <= 0:
            return bot.reply_to(message, "❌ Le prix doit être positif.")
        plans = get_plans()
        old   = plans[plan_id]["price"]
        plans[plan_id]["price"] = new_price
        save_plans(plans)
        bot.reply_to(
            message,
            f"✅ Prix mis à jour !\n\n"
            f"📦 *{plans[plan_id]['desc']}*\n"
            f"Ancien prix : `{old:.2f} USD`\n"
            f"Nouveau prix : `{new_price:.2f} USD`",
            parse_mode="Markdown"
        )
        send_log(
            f"💰 *PRIX MODIFIÉ*\n"
            f"Admin : {message.from_user.id}\n"
            f"Offre : {plans[plan_id]['desc']}\n"
            f"{old:.2f} → {new_price:.2f} USD"
        )
    except ValueError:
        bot.reply_to(message, "❌ Entrez un nombre valide (ex: 12.50).")

@bot.callback_query_handler(func=lambda c: c.data == "adm_back")
def adm_back(call):
    if not is_admin(call.from_user.id): return
    s = db_stats()
    text = (
        f"👑 *Panel Admin*\n\n"
        f"🖥️ *Serveur :* `{SERVER_IP}`\n"
        f"👥 Total : *{s['total']}*\n"
        f"✅ Actifs : *{s['active']}*\n"
        f"💰 Revenus : *{s['revenue']:.2f} USD*"
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                          reply_markup=admin_markup(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def noop(call): bot.answer_callback_query(call.id)

# ==========================================
# 🚀 DÉMARRAGE
# ==========================================
if __name__ == "__main__":
    init_db()
    logger.info(f"🚀 Autoshop démarré — IP : {SERVER_IP}")
    bot.infinity_polling(timeout=20, long_polling_timeout=15)
