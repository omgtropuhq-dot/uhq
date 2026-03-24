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
BOT_TOKEN      = "TON_TOKEN_TELEGRAM_BOT"
CRYPTO_TOKEN   = "TON_TOKEN_CRYPTO_BOT"
SERVER_IP      = "123.123.123.123"
ADMIN_IDS      = [123456789]          # Ton ID Telegram
LOG_GROUP_ID   = -100987654321        # ID groupe de logs
DB_PATH        = "customers.db"
CRYPTO_API_URL = "https://pay.crypt.bot/api"
COOLDOWN_SEC   = 15

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
PLANS = {
    "plan_7":  {"name": "Pack_7J",  "days": 7,  "price": 10.50, "desc": "1 NDD / 7 Jours",     "emoji": "🔥"},
    "plan_15": {"name": "Pack_15J", "days": 15, "price": 16.00, "desc": "2 NDD / 15 Jours",    "emoji": "✌️"},
    "plan_30": {"name": "Pack_30J", "days": 30, "price": 26.50, "desc": "Illimité / 30 Jours", "emoji": "👑"},
}

# ==========================================
# 🗄️ BASE DE DONNÉES SQLite
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
            invoice_id    TEXT    NOT NULL,
            active        INTEGER DEFAULT 1
        )
    """)
    con.commit()
    con.close()
    logger.info("Base de données initialisée.")

def db_add_customer(tg_id, tg_username, plan_id, plan_desc,
                    domain, username, password, price, expire_date, invoice_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO customers
            (tg_id, tg_username, plan_id, plan_desc, plesk_domain, plesk_user,
             plesk_pass, price_paid, purchase_date, expire_date, invoice_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tg_id, tg_username, plan_id, plan_desc, domain,
        username, password, price,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        expire_date, invoice_id
    ))
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

def db_set_active(record_id, state: int):
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
        "total": total_row[0] or 0,
        "revenue": total_row[1] or 0.0,
        "active": active,
        "breakdown": breakdown
    }

def db_get_by_tg_id(tg_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM customers WHERE tg_id = ? ORDER BY id DESC", (tg_id,))
    rows = cur.fetchall()
    con.close()
    return rows

# ==========================================
# 🛠️ UTILITAIRES
# ==========================================
_cooldown: dict[int, float] = {}

def generate_password(length=12):
    chars = string.ascii_letters + string.digits + "!@#$"
    return ''.join(random.choices(chars, k=length))

def generate_uid(length=6):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_rate_limited(user_id):
    now = time.time()
    if now - _cooldown.get(user_id, 0) < COOLDOWN_SEC:
        return True
    _cooldown[user_id] = now
    return False

def has_username(user):
    return bool(user.username)

def send_log(text):
    try:
        bot.send_message(LOG_GROUP_ID, text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Log groupe error: {e}")

def customer_card(row, show_creds=False):
    status = "✅ Actif" if row["active"] else "❌ Inactif"
    card = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *ID :* `{row['id']}`\n"
        f"👤 *Telegram :* @{row['tg_username']} (`{row['tg_id']}`)\n"
        f"📦 *Offre :* {row['plan_desc']}\n"
        f"💵 *Payé :* `{row['price_paid']:.2f} USDT`\n"
        f"📅 *Achat :* {row['purchase_date']}\n"
        f"⏰ *Expire :* {row['expire_date']}\n"
        f"🌐 *Domaine :* `{row['plesk_domain']}`\n"
        f"🔖 *Statut :* {status}\n"
    )
    if show_creds:
        card += (
            f"👤 *Login Plesk :* `{row['plesk_user']}`\n"
            f"🔑 *Password :* `{row['plesk_pass']}`\n"
        )
    return card

# ==========================================
# 💳 CRYPTO BOT API
# ==========================================
def create_invoice(amount, description):
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
        payload  = {"asset": "USDT", "amount": str(amount),
                    "description": description, "expires_in": 3600}
        res = requests.post(f"{CRYPTO_API_URL}/createInvoice",
                            headers=headers, json=payload, timeout=10).json()
        if res.get("ok"):
            r = res["result"]
            return r["pay_url"], str(r["invoice_id"])
        logger.warning(f"createInvoice failed: {res}")
    except Exception as e:
        logger.error(f"createInvoice error: {e}")
    return None, None

def check_invoice(invoice_id):
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
        res = requests.get(f"{CRYPTO_API_URL}/getInvoices",
                           headers=headers,
                           params={"invoice_ids": invoice_id},
                           timeout=10).json()
        if res.get("ok") and res["result"]["items"]:
            return res["result"]["items"][0]["status"]
    except Exception as e:
        logger.error(f"checkInvoice error: {e}")
    return "error"

# ==========================================
# 🖥️ PROVISIONNEMENT PLESK
# ==========================================
def provision_plesk(plan):
    uid      = generate_uid()
    domain   = f"client-{uid}.local"
    username = f"user_{uid}"
    password = generate_password()
    expire   = (datetime.datetime.now() + datetime.timedelta(days=plan["days"])).strftime("%Y-%m-%d")

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
        logger.error(f"Plesk error: {e.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("Plesk timeout (60s)")
    return None

# ==========================================
# 👑 PANEL ADMIN
# ==========================================
def admin_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👥 Liste clients",        callback_data="adm_list_0"),
        InlineKeyboardButton("📊 Statistiques",         callback_data="adm_stats"),
        InlineKeyboardButton("🔍 Chercher par @",       callback_data="adm_search_tg"),
        InlineKeyboardButton("🔍 Chercher par domaine", callback_data="adm_search_domain"),
        InlineKeyboardButton("🔐 Voir creds (ID DB)",   callback_data="adm_creds_ask"),
    )
    return markup

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if not is_admin(message.from_user.id):
        return
    s = db_stats()
    text = (
        f"👑 *Panel Admin*\n\n"
        f"👥 Total commandes : *{s['total']}*\n"
        f"✅ Comptes actifs : *{s['active']}*\n"
        f"💰 Revenus totaux : *{s['revenue']:.2f} USDT*"
    )
    bot.send_message(message.chat.id, text, reply_markup=admin_markup(), parse_mode="Markdown")

# — Statistiques —
@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def adm_stats(call):
    if not is_admin(call.from_user.id): return
    s = db_stats()
    breakdown = "\n".join([f"  • {r[0]} : {r[1]} vente(s)" for r in s["breakdown"]]) or "  Aucune vente"
    text = (
        f"📊 *Statistiques détaillées*\n\n"
        f"👥 Total commandes : *{s['total']}*\n"
        f"✅ Comptes actifs : *{s['active']}*\n"
        f"❌ Inactifs : *{s['total'] - s['active']}*\n"
        f"💰 Revenus totaux : *{s['revenue']:.2f} USDT*\n\n"
        f"📦 *Répartition par offre :*\n{breakdown}"
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))
    bot.edit_message_text(text, chat_id=call.message.chat.id,
                          message_id=call.message.message_id,
                          reply_markup=markup, parse_mode="Markdown")

# — Liste paginée —
@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_list_"))
def adm_list(call):
    if not is_admin(call.from_user.id): return
    page     = int(call.data.split("_")[2])
    per_page = 4
    rows     = db_get_all_customers()
    total    = len(rows)

    if total == 0:
        return bot.answer_callback_query(call.id, "Aucun client encore.", show_alert=True)

    start = page * per_page
    chunk = rows[start:start + per_page]

    text = f"👥 *Clients ({start+1}–{min(start+per_page, total)} / {total})*\n\n"
    for row in chunk:
        text += customer_card(row) + "\n"

    markup = InlineKeyboardMarkup(row_width=3)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm_list_{page-1}"))
    nav.append(InlineKeyboardButton(f"· {page+1} ·", callback_data="noop"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"adm_list_{page+1}"))
    if nav:
        markup.add(*nav)
    markup.add(InlineKeyboardButton("⬅️ Retour", callback_data="adm_back"))

    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              reply_markup=markup, parse_mode="Markdown")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="Markdown")

# — Voir creds par ID DB —
@bot.callback_query_handler(func=lambda c: c.data == "adm_creds_ask")
def adm_creds_ask(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id,
                           "🔐 Entrez l'*ID DB* du client :",
                           parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_creds_show)

def adm_creds_show(message):
    if not is_admin(message.from_user.id): return
    try:
        record_id = int(message.text.strip())
        row = db_get_by_id(record_id)
        if not row:
            return bot.reply_to(message, "❌ Aucun client avec cet ID.")
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🚫 Désactiver", callback_data=f"adm_off_{record_id}"),
            InlineKeyboardButton("✅ Réactiver",  callback_data=f"adm_on_{record_id}"),
        )
        bot.send_message(message.chat.id, customer_card(row, show_creds=True),
                         reply_markup=markup, parse_mode="Markdown")
    except ValueError:
        bot.reply_to(message, "❌ Entrez un nombre entier.")

# — Activer / Désactiver —
@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_off_") or c.data.startswith("adm_on_"))
def adm_toggle(call):
    if not is_admin(call.from_user.id): return
    parts  = call.data.split("_")
    action = parts[1]           # "off" ou "on"
    rid    = int(parts[2])
    state  = 0 if action == "off" else 1
    db_set_active(rid, state)
    label  = "désactivé ❌" if state == 0 else "réactivé ✅"
    bot.answer_callback_query(call.id, f"Compte {label}.")
    row = db_get_by_id(rid)
    if row:
        bot.edit_message_text(customer_card(row, show_creds=True),
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              parse_mode="Markdown")

# — Recherche par @ —
@bot.callback_query_handler(func=lambda c: c.data == "adm_search_tg")
def adm_search_tg(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id,
                           "🔍 Entrez le *@username* ou l'*ID Telegram* :",
                           parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_search_tg_result)

def adm_search_tg_result(message):
    if not is_admin(message.from_user.id): return
    query = message.text.strip().lstrip("@")
    rows  = db_get_by_tg(query)
    if not rows:
        return bot.reply_to(message, "❌ Aucun client trouvé.")
    text = f"🔍 *Résultats pour* `{query}` *({len(rows)} commande(s)) :*\n\n"
    for row in rows:
        text += customer_card(row, show_creds=True) + "\n"
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# — Recherche par domaine —
@bot.callback_query_handler(func=lambda c: c.data == "adm_search_domain")
def adm_search_domain(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id,
                           "🔍 Entrez le domaine Plesk (ex: `client-abc123.local`) :",
                           parse_mode="Markdown")
    bot.register_next_step_handler(msg, adm_search_domain_result)

def adm_search_domain_result(message):
    if not is_admin(message.from_user.id): return
    row = db_get_by_domain(message.text.strip())
    if not row:
        return bot.reply_to(message, "❌ Domaine introuvable.")
    bot.send_message(message.chat.id, customer_card(row, show_creds=True), parse_mode="Markdown")

# — Retour menu admin —
@bot.callback_query_handler(func=lambda c: c.data == "adm_back")
def adm_back(call):
    if not is_admin(call.from_user.id): return
    s = db_stats()
    text = (
        f"👑 *Panel Admin*\n\n"
        f"👥 Total commandes : *{s['total']}*\n"
        f"✅ Comptes actifs : *{s['active']}*\n"
        f"💰 Revenus totaux : *{s['revenue']:.2f} USDT*"
    )
    bot.edit_message_text(text, chat_id=call.message.chat.id,
                          message_id=call.message.message_id,
                          reply_markup=admin_markup(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def noop(call):
    bot.answer_callback_query(call.id)

# ==========================================
# 🛒 SHOP CLIENT
# ==========================================
@bot.message_handler(commands=['start'])
def send_shop(message):
    if not has_username(message.from_user):
        return bot.send_message(
            message.chat.id,
            "⚠️ *Un @username Telegram est obligatoire pour acheter.*\n\n"
            "➡️ Va dans *Paramètres → Modifier le profil → Nom d'utilisateur*\n"
            "Définis ton @username puis reviens ici.",
            parse_mode="Markdown"
        )

    markup = InlineKeyboardMarkup(row_width=1)
    for plan_id, plan in PLANS.items():
        markup.add(InlineKeyboardButton(
            f"{plan['emoji']}  {plan['days']} JOURS  —  {plan['price']:.2f} USDT  —  {plan['desc']}",
            callback_data=f"buy_{plan_id}"
        ))
    markup.add(InlineKeyboardButton("📦 Mes commandes", callback_data="my_orders"))

    bot.send_message(
        message.chat.id,
        "📡 *PLESK AUTOSHOP* 🛡️\n\n"
        "Paiement 100% crypto · Livraison instantanée\n\n"
        "👇 *Choisissez votre offre :*",
        reply_markup=markup,
        parse_mode="Markdown"
    )

# — Mes commandes (bouton inline) —
@bot.callback_query_handler(func=lambda c: c.data == "my_orders")
def my_orders_inline(call):
    rows = db_get_by_tg_id(call.from_user.id)
    if not rows:
        return bot.answer_callback_query(call.id, "📭 Aucune commande.", show_alert=True)
    text = "📦 *Vos commandes :*\n\n"
    for row in rows:
        status = "✅ Actif" if row["active"] else "❌ Inactif"
        text += (
            f"*{row['plan_desc']}* — {status}\n"
            f"⏰ Expire : `{row['expire_date']}`\n"
            f"🌐 Panel : `https://{SERVER_IP}:8443`\n"
            f"👤 `{row['plesk_user']}` / 🔑 `{row['plesk_pass']}`\n\n"
        )
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")

# ==========================================
# 💸 ACHAT & VÉRIFICATION
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data.startswith('buy_'))
def process_buy(call):
    if not has_username(call.from_user):
        return bot.answer_callback_query(
            call.id, "⚠️ @username requis pour acheter.", show_alert=True)

    plan_id = call.data[4:]
    if plan_id not in PLANS:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)
    if is_rate_limited(call.from_user.id):
        return bot.answer_callback_query(call.id, "⏳ Patientez quelques secondes.", show_alert=True)

    plan = PLANS[plan_id]
    bot.answer_callback_query(call.id, "⏳ Génération de la facture...")

    pay_url, invoice_id = create_invoice(plan["price"], f"Hébergement Plesk — {plan['desc']}")
    if not pay_url:
        return bot.send_message(call.message.chat.id,
                                "❌ Service de paiement indisponible. Réessayez dans quelques minutes.")

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("💸 Payer maintenant (USDT/TON/BTC)", url=pay_url),
        InlineKeyboardButton("🔄 Vérifier mon paiement", callback_data=f"check_{invoice_id}_{plan_id}")
    )
    bot.send_message(
        call.message.chat.id,
        f"🧾 *FACTURE CRÉÉE*\n\n"
        f"📦 *Offre :* {plan['desc']}\n"
        f"💵 *Montant :* `{plan['price']:.2f} USDT`\n"
        f"⏰ *Validité :* 1 heure\n\n"
        f"1️⃣ Cliquez *Payer maintenant*\n"
        f"2️⃣ Revenez et cliquez *Vérifier*",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith('check_'))
def verify_payment(call):
    parts = call.data.split('_', 2)
    if len(parts) != 3:
        return bot.answer_callback_query(call.id, "❌ Données invalides.", show_alert=True)

    _, invoice_id, plan_id = parts
    if plan_id not in PLANS:
        return bot.answer_callback_query(call.id, "❌ Offre inconnue.", show_alert=True)

    plan   = PLANS[plan_id]
    status = check_invoice(invoice_id)

    if status == "paid":
        bot.answer_callback_query(call.id, "✅ Paiement validé !")
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
                tg_id       = call.from_user.id,
                tg_username = tg_username,
                plan_id     = plan_id,
                plan_desc   = plan["desc"],
                domain      = infos["domain"],
                username    = infos["username"],
                password    = infos["password"],
                price       = plan["price"],
                expire_date = infos["expire"],
                invoice_id  = invoice_id
            )
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("📦 Voir mes commandes", callback_data="my_orders"))
            bot.send_message(
                call.message.chat.id,
                f"🎉 *SERVEUR LIVRÉ !*\n\n"
                f"🌐 *Panel :* `https://{SERVER_IP}:8443`\n"
                f"👤 *Identifiant :* `{infos['username']}`\n"
                f"🔑 *Mot de passe :* `{infos['password']}`\n"
                f"📅 *Expiration :* `{infos['expire']}`\n\n"
                f"_Conservez ces infos précieusement._",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            send_log(
                f"💰 *NOUVELLE VENTE*\n\n"
                f"👤 @{tg_username} (`{call.from_user.id}`)\n"
                f"📦 {plan['desc']}\n"
                f"💵 +{plan['price']:.2f} USDT\n"
                f"🌐 `{infos['domain']}`\n"
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
                f"Facture : `{invoice_id}` — Offre : {plan['desc']}"
            )

    elif status == "active":
        bot.answer_callback_query(
            call.id, "⏳ Paiement non encore détecté. Attendez 1 min puis réessayez.", show_alert=True)
    elif status == "expired":
        bot.answer_callback_query(
            call.id, "❌ Facture expirée. Revenez sur /start pour en créer une nouvelle.", show_alert=True)
    else:
        bot.answer_callback_query(
            call.id, "❌ Erreur de vérification. Réessayez.", show_alert=True)

# ==========================================
# 🚀 DÉMARRAGE
# ==========================================
if __name__ == "__main__":
    init_db()
    logger.info("🚀 Autoshop démarré.")
    bot.infinity_polling(timeout=20, long_polling_timeout=15)
