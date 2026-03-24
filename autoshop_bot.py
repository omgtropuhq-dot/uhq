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

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
BOT_TOKEN       = "8628435813:AAG-RpDUGSpNaTWbPFgwaQuJe7ffRHY7E24"
NOWPAYMENTS_KEY = "2T93HR9-5CXME5G-H8C530Z-6Q1CR8F"  # nowpayments.io → Settings → API Keys
ADMIN_IDS       = [8704755112]
LOG_GROUP_ID    = -5142753842
DB_PATH         = "customers.db"
NOWPAY_API      = "https://api.nowpayments.io/v1"
COOLDOWN_SEC    = 15

# IP publique détectée automatiquement
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
logger.info(f"IP serveur détectée : {SERVER_IP}")

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# 📦 PLANS
# ==========================================
PLANS = {
    "plan_7":  {"name": "Pack_7J",  "days": 7,  "price": 10.50, "desc": "1 NDD / 7 Jours",     "emoji": "⚡"},
    "plan_15": {"name": "Pack_15J", "days": 15, "price": 16.00, "desc": "2 NDD / 15 Jours",    "emoji": "🚀"},
    "plan_30": {"name": "Pack_30J", "days": 30, "price": 26.50, "desc": "Illimité / 30 Jours", "emoji": "💎"},
}

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
    logger.info("Base de données initialisée.")

def db_add_customer(tg_id, tg_username, plan_id, plan_desc,
                    domain, username, password, price, expire_date, payment_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO customers
            (tg_id, tg_username, plan_id, plan_desc, plesk_domain, plesk_user,
             plesk_pass, price_paid, purchase_date, expire_date, payment_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tg_id, tg_username, plan_id, plan_desc, domain,
        username, password, price,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        expire_date, payment_id
    ))
    con.commit()
    con.close()

def db_add_payment_history(tg_id, tg_username, plan_desc,
                           amount, currency, payment_id, status):
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
    cur.execute("UPDATE payment_history SET status = ? WHERE payment_id = ?",
                (status, payment_id))
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
    cur.execute(
        "SELECT * FROM payment_history WHERE tg_id = ? ORDER BY id DESC LIMIT 15",
        (tg_id,)
    )
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
# 💳 NOWPAYMENTS API
# ==========================================
def create_payment(amount, order_id, description):
    """
    Crée un paiement NOWPayments.
    Retourne (adresse, montant_crypto, devise, payment_id) ou (None,None,None,None).
    La devise par défaut est USDT TRC-20. Tu peux changer 'pay_currency' :
      usdttrc20 / btc / eth / ltc / xmr / trx ...
    """
    try:
        headers = {
            "x-api-key":    NOWPAYMENTS_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "price_amount":        amount,
            "price_currency":      "usd",
            "pay_currency":        "usdttrc20",
            "order_id":            order_id,
            "order_description":   description,
            "is_fixed_rate":       True,
            "is_fee_paid_by_user": False,
        }
        res = requests.post(
            f"{NOWPAY_API}/payment",
            headers=headers, json=payload, timeout=10
        ).json()

        if "payment_id" in res:
            return (
                res.get("pay_address", "—"),
                float(res.get("pay_amount", amount)),
                res.get("pay_currency", "USDT").upper(),
                str(res["payment_id"])
            )
        logger.warning(f"NOWPayments echec : {res}")
    except Exception as e:
        logger.error(f"NOWPayments create error : {e}")
    return None, None, None, None

def check_payment(payment_id):
    """
    Statuts : waiting · confirming · confirmed · sending
              partially_paid · finished · failed · refunded · expired
    """
    try:
        headers = {"x-api-key": NOWPAYMENTS_KEY}
        res = requests.get(
            f"{NOWPAY_API}/payment/{payment_id}",
            headers=headers, timeout=10
        ).json()
        return res.get("payment_status", "error")
    except Exception as e:
        logger.error(f"NOWPayments check error : {e}")
    return "error"

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
        return {"domain": domain, "username": username,
                "password": password, "expire": expire}
    except subprocess.CalledProcessError as e:
        logger.error(f"Plesk CalledProcessError : {e.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("Plesk timeout (60s)")
    return None

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
        InlineKeyboardButton("🔐 Voir creds (ID DB)",   callback_data="adm_creds_ask"),
    )
    return m

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if not is_admin(message.from_user.id): return
    s = db_stats()
    text = (
        f"👑 *Panel Admin*\n\n"
        f"🖥️ *Serveur :* `{SERVER_IP}`\n"
        f"👥 Total commandes : *{s['total']}*\n"
        f"✅ Comptes actifs : *{s['active']}*\n"
        f"💰 Revenus totaux : *{s['revenue']:.2f} USD*"
    )
    bot.send_message(message.chat.id, text,
                     reply_markup=admin_markup(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def adm_stats(call):
    if not is_admin(call.from_user.id): return
    s = db_stats()
    bd = "\n".join([f"  • {r[0]} : {r[1]} vente(s)" for r in s["breakdown"]]) or "  Aucune"
    text = (
        f"📊 *Statistiques*\n\n"
        f"👥 Total : *{s['total']}*\n"
        f"✅ Actifs : *{s['active']}*  |  ❌ Inactifs : *{s['total']-s['active']}*\n"
        f"💰 Revenus : *{s['revenue']:.2f} USD*\n\n"
        f"📦 *Par offre :*\n{bd}"
    )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.edit_message_text(text, call.message.chat.id,
                          call.message.message_id, reply_markup=m, parse_mode="Markdown")

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
        bot.edit_message_text(text, call.message.chat.id,
                              call.message.message_id, reply_markup=m, parse_mode="Markdown")
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
                           "🔍 Domaine Plesk (ex: `client-abc123.local`) :", parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_search_domain_result)

def adm_search_domain_result(message):
    if not is_admin(message.from_user.id): return
    row = db_get_by_domain(message.text.strip())
    if not row:
        return bot.reply_to(message, "❌ Domaine introuvable.")
    bot.send_message(message.chat.id, customer_card(row, show_creds=True), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_back")
def adm_back(call):
    if not is_admin(call.from_user.id): return
    s = db_stats()
    text = (
        f"👑 *Panel Admin*\n\n"
        f"🖥️ *Serveur :* `{SERVER_IP}`\n"
        f"👥 Total commandes : *{s['total']}*\n"
        f"✅ Comptes actifs : *{s['active']}*\n"
        f"💰 Revenus totaux : *{s['revenue']:.2f} USD*"
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                          reply_markup=admin_markup(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def noop(call): bot.answer_callback_query(call.id)

# ==========================================
# 🛒 SHOP CLIENT
# ==========================================
@bot.message_handler(commands=['start'])
def send_shop(message):
    if not has_username(message.from_user):
        return bot.send_message(
            message.chat.id,
            "⚠️ *Un @username Telegram est obligatoire.*\n\n"
            "➡️ *Paramètres → Modifier le profil → Nom d'utilisateur*\n"
            "Crée ton @username puis reviens ici avec /start.",
            parse_mode="Markdown"
        )
    m = InlineKeyboardMarkup(row_width=1)
    for pid, plan in PLANS.items():
        m.add(InlineKeyboardButton(
            f"{plan['emoji']}  {plan['days']} JOURS  —  {plan['price']:.2f}$  —  {plan['desc']}",
            callback_data=f"buy_{pid}"
        ))
    m.add(InlineKeyboardButton("📦 Mes commandes",        callback_data="my_orders"))
    m.add(InlineKeyboardButton("🧾 Historique paiements", callback_data="pay_history"))
    bot.send_message(
        message.chat.id,
        "📡 *PLESK AUTOSHOP* 🛡️\n\n"
        "Paiement crypto automatique · Livraison instantanée\n"
        "Accepte USDT · BTC · ETH · LTC · +300 cryptos\n\n"
        "👇 *Choisissez votre offre :*",
        reply_markup=m, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "my_orders")
def my_orders(call):
    rows = db_get_by_tg_id(call.from_user.id)
    if not rows:
        return bot.answer_callback_query(call.id, "📭 Aucune commande.", show_alert=True)
    text = "📦 *Vos commandes actives :*\n\n"
    for row in rows:
        status = "✅ Actif" if row["active"] else "❌ Expiré/Inactif"
        text += (
            f"{status} — *{row['plan_desc']}*\n"
            f"⏰ Expire : `{row['expire_date']}`\n"
            f"🖥️ Panel : `https://{SERVER_IP}:8443`\n"
            f"👤 Login : `{row['plesk_user']}`\n"
            f"🔑 Pass : `{row['plesk_pass']}`\n\n"
        )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_shop"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "pay_history")
def pay_history(call):
    rows = db_get_payment_history(call.from_user.id)
    if not rows:
        return bot.answer_callback_query(call.id, "📭 Aucun paiement.", show_alert=True)
    text = "🧾 *Historique paiements (15 derniers) :*\n\n"
    for row in rows:
        if row["status"] == "finished":
            icon = "✅"
        elif row["status"] in ("waiting", "confirming", "partially_paid"):
            icon = "⏳"
        else:
            icon = "❌"
        text += (
            f"{icon} *{row['plan_desc']}* — `{row['amount']:.4f} {row['currency']}`\n"
            f"   📅 {row['date']} · `{row['status']}`\n"
            f"   🆔 `{row['payment_id']}`\n\n"
        )
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("⬅️ Retour", callback_data="back_shop"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "back_shop")
def back_shop(call):
    bot.answer_callback_query(call.id)
    send_shop(call.message)

# ==========================================
# 💸 ACHAT
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data.startswith('buy_'))
def process_buy(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(call.id, "⚠️ @username requis.", show_alert=True)
    plan_id = call.data[4:]
    if plan_id not in PLANS:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)
    if is_rate_limited(call.from_user.id):
        return bot.answer_callback_query(call.id, "⏳ Patientez quelques secondes.", show_alert=True)

    plan     = PLANS[plan_id]
    order_id = f"{call.from_user.id}-{plan_id}-{int(time.time())}"
    bot.answer_callback_query(call.id, "⏳ Création du paiement...")

    pay_address, pay_amount, pay_currency, payment_id = create_payment(
        plan["price"], order_id, f"Plesk {plan['desc']}"
    )

    if not pay_address:
        return bot.send_message(
            call.message.chat.id,
            "❌ Service de paiement indisponible. Réessayez dans quelques minutes."
        )

    # Enregistre la tentative
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

    bot.send_message(
        call.message.chat.id,
        f"🧾 *FACTURE CRÉÉE*\n\n"
        f"📦 *Offre :* {plan['desc']}\n"
        f"💵 *Prix :* `{plan['price']:.2f} USD`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 *Envoyez exactement :*\n"
        f"`{pay_amount}` *{pay_currency}*\n\n"
        f"📬 *Adresse de paiement :*\n"
        f"`{pay_address}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ *Validité :* 60 minutes\n"
        f"🆔 *Réf :* `{payment_id}`\n\n"
        f"1️⃣ Envoyez le montant exact\n"
        f"2️⃣ Cliquez *Vérifier mon paiement*",
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
    if plan_id not in PLANS:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)

    plan   = PLANS[plan_id]
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
                plan_id=plan_id,         plan_desc=plan["desc"],
                domain=infos["domain"],  username=infos["username"],
                password=infos["password"], price=plan["price"],
                expire_date=infos["expire"], payment_id=payment_id
            )
            m = InlineKeyboardMarkup(row_width=1)
            m.add(
                InlineKeyboardButton("📦 Mes commandes",        callback_data="my_orders"),
                InlineKeyboardButton("🧾 Historique paiements", callback_data="pay_history"),
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
                f"_Retrouvez vos infos à tout moment via 📦 Mes commandes._",
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
                "⚠️ Paiement reçu mais erreur serveur. Notre équipe a été alertée et vous contactera rapidement."
            )
            send_log(
                f"⚠️ *ERREUR PROVISIONNEMENT*\n"
                f"@{tg_username} (`{call.from_user.id}`) a payé mais Plesk a échoué !\n"
                f"Paiement : `{payment_id}` — {plan['desc']}"
            )

    elif status == "confirming":
        bot.answer_callback_query(
            call.id, "🔄 Paiement détecté, confirmation blockchain en cours (1–3 min).", show_alert=True)
    elif status in ("waiting", "partially_paid"):
        msgs = {
            "waiting":        "⏳ Paiement non reçu. Réessayez dans 1 min.",
            "partially_paid": "⚠️ Montant partiel reçu. Envoyez le reste à la même adresse.",
        }
        bot.answer_callback_query(call.id, msgs[status], show_alert=True)
    elif status == "expired":
        bot.answer_callback_query(
            call.id, "❌ Paiement expiré. Faites /start pour recommencer.", show_alert=True)
    elif status == "failed":
        bot.answer_callback_query(
            call.id, "❌ Paiement échoué. Contactez le support.", show_alert=True)
    else:
        bot.answer_callback_query(
            call.id, f"❓ Statut inconnu : {status}. Réessayez.", show_alert=True)

# ==========================================
# 🚀 DÉMARRAGE
# ==========================================
if __name__ == "__main__":
    init_db()
    logger.info(f"🚀 Autoshop démarré — IP : {SERVER_IP}")
    bot.infinity_polling(timeout=20, long_polling_timeout=15)
