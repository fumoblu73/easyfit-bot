#!/usr/bin/env python3
"""
EasyFit Bot - VERSIONE DEFINITIVA COMPLETA
Con tutti i comandi: prenota, lista, cancella, help
Integrazione API EasyFit reale per calendario e prenotazioni
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import base64

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger(__name__)

# Variabili ambiente
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
EASYFIT_EMAIL = os.getenv('EASYFIT_EMAIL')
EASYFIT_PASSWORD = os.getenv('EASYFIT_PASSWORD')

# Costanti EasyFit
EASYFIT_BASE_URL = "https://app-easyfitpalestre.it"
STUDIO_ID = "ZWFzeWZpdDoxMjE2OTE1Mzgw"
FACILITY_ID = "easyfit:1216915380"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


# ============================================================================
# FUNZIONI API EASYFIT
# ============================================================================

def get_calendar_courses(start_date, end_date):
    """Recupera calendario corsi PUBBLICO (no login richiesto)"""
    try:
        url = f"{EASYFIT_BASE_URL}/nox/v2/bookableitems/courses/with-canceled"
        
        params = {
            "facilityId": FACILITY_ID,
            "startDate": start_date,
            "endDate": end_date
        }
        
        headers = {
            "Accept": "application/json",
            "Origin": EASYFIT_BASE_URL,
            "Referer": f"{EASYFIT_BASE_URL}/studio/{STUDIO_ID}/course"
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            courses = response.json()
            logger.info(f"✅ Calendario: {len(courses)} corsi trovati")
            return courses
        else:
            logger.error(f"❌ Errore calendario: {response.status_code}")
            return []
            
    except Exception as e:
        logger.error(f"❌ Errore get_calendar: {e}")
        return []


def easyfit_login():
    """Effettua login su EasyFit"""
    try:
        logger.info("🔐 Login EasyFit...")
        
        session = requests.Session()
        url = f"{EASYFIT_BASE_URL}/login"
        
        credentials = f"{EASYFIT_EMAIL}:{EASYFIT_PASSWORD}"
        basic_auth = base64.b64encode(credentials.encode()).decode()
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Authorization": f"Basic {basic_auth}",
            "Origin": EASYFIT_BASE_URL,
            "Referer": f"{EASYFIT_BASE_URL}/studio/{STUDIO_ID}/course",
            "x-tenant": "easyfit",
            "x-ms-web-context": f"/studio/{STUDIO_ID}",
            "x-nox-client-type": "WEB",
            "x-public-facility-group": "BRANDEDAPP-263FBF081EAB42E6A62602B2DDDE4506"
        }
        
        payload = {
            "username": EASYFIT_EMAIL,
            "password": EASYFIT_PASSWORD
        }
        
        response = session.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"✅ Login OK")
            return session
        else:
            logger.error(f"❌ Login fallito: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Errore login: {e}")
        return None


def find_course_appointment_id(session, class_name, class_date, class_time):
    """Trova courseAppointmentId per prenotazione"""
    try:
        logger.info(f"🔎 Cerco: {class_name} {class_date} {class_time}")
        
        date_obj = datetime.strptime(class_date, '%Y-%m-%d')
        start_date = (date_obj - timedelta(hours=72)).strftime('%Y-%m-%d')
        end_date = (date_obj + timedelta(days=7)).strftime('%Y-%m-%d')
        
        url = f"{EASYFIT_BASE_URL}/nox/v2/bookableitems/courses/with-canceled"
        
        params = {
            "facilityId": FACILITY_ID,
            "startDate": start_date,
            "endDate": end_date
        }
        
        response = session.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            courses = response.json()
            
            target_datetime = f"{class_date}T{class_time}:00"
            
            for course in courses:
                if course.get('name') == class_name:
                    for slot in course.get('slots', []):
                        slot_time = slot.get('startDateTime', '')
                        if slot_time.startswith(target_datetime):
                            course_id = slot.get('id')
                            logger.info(f"✅ Trovato ID: {course_id}")
                            return course_id, slot
            
            logger.warning(f"⚠️  Lezione non trovata")
            return None, None
        else:
            logger.error(f"❌ Errore ricerca: {response.status_code}")
            return None, None
            
    except Exception as e:
        logger.error(f"❌ Errore find_course: {e}")
        return None, None


def book_course_easyfit(session, course_appointment_id):
    """Prenota corso su EasyFit"""
    try:
        logger.info(f"📝 Prenotazione ID: {course_appointment_id}")
        
        url = f"{EASYFIT_BASE_URL}/nox/v1/calendar/bookcourse"
        
        payload = {
            "courseAppointmentId": course_appointment_id,
            "expectedCustomerStatus": "BOOKED"
        }
        
        response = session.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            status = data.get('participantStatus')
            
            if status == "BOOKED":
                logger.info(f"✅ PRENOTATO!")
                return True, data
            else:
                logger.warning(f"⚠️  Status: {status}")
                return False, data
        else:
            logger.error(f"❌ Prenotazione fallita: {response.status_code}")
            return False, None
            
    except Exception as e:
        logger.error(f"❌ Errore prenotazione: {e}")
        return False, None


# ============================================================================
# FUNZIONE CHECK_AND_BOOK
# ============================================================================

def check_and_book(application):
    """Controlla e prenota automaticamente"""
    
    logger.info("="*60)
    logger.info("🔍 CONTROLLO PRENOTAZIONI")
    logger.info(f"⏰ Ora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    current_hour = datetime.now().hour
    
    if not (8 <= current_hour < 21):
        logger.info(f"⏰ Fuori orario (8-21)")
        logger.info("="*60)
        return
    
    logger.info("✅ Dentro orario attivo")
    
    conn = None
    cur = None
    
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cur = conn.cursor()
        logger.info("✅ Database connesso")
    except Exception as e:
        logger.error(f"❌ Errore DB: {e}")
        return
    
    try:
        now = datetime.now()
        
        cur.execute(
            """
            SELECT id, user_id, class_name, class_date, class_time
            FROM bookings
            WHERE status = 'pending'
            AND booking_date <= %s
            ORDER BY booking_date
            """,
            (now,)
        )
        
        bookings_to_make = cur.fetchall()
        
        logger.info(f"📊 Prenotazioni da fare: {len(bookings_to_make)}")
        
        if not bookings_to_make:
            logger.info("ℹ️  Nessuna prenotazione")
            cur.close()
            conn.close()
            logger.info("="*60)
            return
        
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time = booking
            
            logger.info("")
            logger.info(f"{'─'*60}")
            logger.info(f"📝 PRENOTAZIONE #{booking_id}")
            logger.info(f"   📚 {class_name}")
            logger.info(f"   📅 {class_date} ore {class_time}")
            
            # Login
            session = easyfit_login()
            
            if not session:
                logger.error(f"❌ Login fallito #{booking_id}")
                send_telegram_notification(
                    application, user_id, class_name,
                    class_date, class_time, False
                )
                continue
            
            # Trova lezione
            course_id, slot = find_course_appointment_id(
                session, class_name, class_date, class_time
            )
            
            if not course_id:
                logger.error(f"❌ Lezione non trovata #{booking_id}")
                send_telegram_notification(
                    application, user_id, class_name,
                    class_date, class_time, False
                )
                continue
            
            # Prenota
            success, result = book_course_easyfit(session, course_id)
            
            if success:
                logger.info(f"✅ Prenotazione #{booking_id} RIUSCITA!")
                
                cur.execute(
                    """
                    UPDATE bookings
                    SET status = 'completed'
                    WHERE id = %s
                    """,
                    (booking_id,)
                )
                
                conn.commit()
                logger.info(f"💾 Database aggiornato")
                
                send_telegram_notification(
                    application, user_id, class_name,
                    class_date, class_time, True
                )
                
                logger.info(f"🎉 Completata!")
            else:
                logger.error(f"❌ Prenotazione fallita #{booking_id}")
                send_telegram_notification(
                    application, user_id, class_name,
                    class_date, class_time, False
                )
            
            logger.info(f"{'─'*60}")
        
        cur.close()
        conn.close()
        logger.info("✅ Controllo terminato")
        
    except Exception as e:
        logger.error(f"❌ ERRORE: {e}")
        logger.exception("Stack trace:")
        if conn:
            try:
                conn.rollback()
            except:
                pass
    
    logger.info("="*60)


def send_telegram_notification(application, user_id, class_name, class_date, class_time, success):
    """Invia notifica Telegram"""
    try:
        if success:
            message = (
                f"✅ PRENOTAZIONE EFFETTUATA!\n\n"
                f"📚 {class_name}\n"
                f"📅 {class_date}\n"
                f"🕐 {class_time}\n\n"
                f"Ci vediamo in palestra! 💪"
            )
        else:
            message = (
                f"❌ Prenotazione fallita\n\n"
                f"📚 {class_name}\n"
                f"📅 {class_date}\n"
                f"🕐 {class_time}\n\n"
                f"Prova manualmente su app EasyFit"
            )
        
        import asyncio
        asyncio.run(application.bot.send_message(chat_id=user_id, text=message))
        
    except Exception as e:
        logger.error(f"Errore notifica: {e}")


# ============================================================================
# HANDLER TELEGRAM
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    user = update.effective_user
    logger.info(f"📱 /start da {user.first_name}")
    
    await update.message.reply_text(
        f"👋 Ciao {user.first_name}!\n\n"
        f"Sono il bot per prenotare le lezioni EasyFit.\n\n"
        f"🤖 Cosa posso fare:\n"
        f"• Prenotare lezioni 72 ore prima automaticamente\n"
        f"• Calendario REALE da EasyFit\n"
        f"• Attivo dalle 8 alle 21 ogni giorno\n\n"
        f"📋 Comandi:\n"
        f"/prenota - Programma prenotazione\n"
        f"/lista - Vedi prenotazioni programmate\n"
        f"/cancella - Cancella prenotazione\n"
        f"/help - Guida completa"
    )


async def prenota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /prenota - Mostra lezioni dal calendario reale"""
    user = update.effective_user
    logger.info(f"📱 /prenota da {user.first_name}")
    
    await update.message.reply_text("🔍 Recupero calendario EasyFit...")
    
    try:
        # Recupera calendario prossimi 7 giorni
        today = datetime.now()
        start_date = today.strftime('%Y-%m-%d')
        end_date = (today + timedelta(days=7)).strftime('%Y-%m-%d')
        
        courses = get_calendar_courses(start_date, end_date)
        
        if not courses:
            await update.message.reply_text(
                "❌ Impossibile recuperare il calendario.\n"
                "Riprova tra qualche minuto."
            )
            return
        
        # Estrai nomi lezioni univoci
        class_names = set()
        for course in courses:
            class_names.add(course['name'])
        
        if not class_names:
            await update.message.reply_text(
                "ℹ️  Nessuna lezione disponibile nei prossimi 7 giorni."
            )
            return
        
        # Salva calendario in context
        context.user_data['calendar'] = courses
        
        # Crea bottoni
        keyboard = []
        for class_name in sorted(class_names):
            keyboard.append([InlineKeyboardButton(
                f"📚 {class_name}",
                callback_data=f"class_{class_name}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📚 Che lezione vuoi prenotare?",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Errore /prenota: {e}")
        await update.message.reply_text(
            "❌ Errore. Riprova."
        )


async def class_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback - Lezione selezionata"""
    query = update.callback_query
    await query.answer()
    
    class_name = query.data.split('_', 1)[1]
    context.user_data['class_name'] = class_name
    
    calendar = context.user_data.get('calendar', [])
    
    # Trova date disponibili
    available_dates = {}
    for course in calendar:
        if course['name'] == class_name:
            for slot in course.get('slots', []):
                slot_datetime = slot['startDateTime'].split('[')[0]
                date_str = slot_datetime.split('T')[0]
                
                if date_str not in available_dates:
                    available_dates[date_str] = []
                available_dates[date_str].append(slot)
    
    if not available_dates:
        await query.edit_message_text(
            f"❌ Nessuna lezione di {class_name} disponibile."
        )
        return
    
    # Mostra giorni
    keyboard = []
    for date_str in sorted(available_dates.keys()):
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
        date_display = date_obj.strftime('%d/%m')
        
        keyboard.append([InlineKeyboardButton(
            f"{day_name} {date_display}",
            callback_data=f"date_{date_str}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📚 {class_name}\n\n📅 Quale giorno?",
        reply_markup=reply_markup
    )


async def date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback - Data selezionata"""
    query = update.callback_query
    await query.answer()
    
    date_str = query.data.split('_')[1]
    context.user_data['date'] = date_str
    
    calendar = context.user_data.get('calendar', [])
    class_name = context.user_data.get('class_name')
    
    # Trova orari disponibili
    available_times = set()
    
    for course in calendar:
        if course['name'] == class_name:
            for slot in course.get('slots', []):
                slot_datetime = slot['startDateTime'].split('[')[0]
                slot_date = slot_datetime.split('T')[0]
                
                if slot_date == date_str:
                    time_str = slot_datetime.split('T')[1][:5]  # HH:MM
                    available_times.add(time_str)
    
    if not available_times:
        await query.edit_message_text(
            f"❌ Nessun orario disponibile per questa data."
        )
        return
    
    # Mostra orari
    keyboard = []
    for time_str in sorted(available_times):
        keyboard.append([InlineKeyboardButton(
            f"🕐 {time_str}",
            callback_data=f"time_{time_str}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
    
    await query.edit_message_text(
        f"📚 {class_name}\n"
        f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n\n"
        f"🕐 Che orario?",
        reply_markup=reply_markup
    )


async def time_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback - Orario selezionato e conferma"""
    query = update.callback_query
    await query.answer()
    
    time_str = query.data.split('_')[1]
    
    class_name = context.user_data.get('class_name')
    date_str = context.user_data.get('date')
    user_id = str(query.from_user.id)
    
    # Calcola booking_date (72h prima)
    class_datetime = datetime.strptime(f"{date_str} {time_str}", '%Y-%m-%d %H:%M')
    booking_datetime = class_datetime - timedelta(hours=72)
    
    # Salva nel database
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO bookings 
            (user_id, class_name, class_date, class_time, booking_date, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, class_name, date_str, time_str, booking_datetime, 'pending')
        )
        
        booking_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
        
        await query.edit_message_text(
            f"✅ PRENOTAZIONE PROGRAMMATA!\n\n"
            f"📚 Lezione: {class_name}\n"
            f"📅 Data: {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
            f"🕐 Orario: {time_str}\n\n"
            f"⏰ Prenoterò automaticamente:\n"
            f"   {booking_datetime.strftime('%d/%m/%Y alle %H:%M')}\n"
            f"   (72 ore prima)\n\n"
            f"📲 Ti avviserò quando prenoto!\n\n"
            f"ID: #{booking_id}"
        )
        
        logger.info(f"✅ Prenotazione #{booking_id} salvata da utente {user_id}")
        
    except Exception as e:
        logger.error(f"Errore salvataggio: {e}")
        await query.edit_message_text("❌ Errore nel salvare. Riprova.")


async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /lista - Mostra prenotazioni programmate"""
    user = update.effective_user
    user_id = str(user.id)
    logger.info(f"📱 /lista da {user.first_name}")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            SELECT id, class_name, class_date, class_time, booking_date, status
            FROM bookings
            WHERE user_id = %s
            ORDER BY class_date, class_time
            """,
            (user_id,)
        )
        
        bookings = cur.fetchall()
        cur.close()
        conn.close()
        
        if not bookings:
            await update.message.reply_text(
                "📋 Non hai prenotazioni.\n\n"
                "Usa /prenota per programmarne una!"
            )
            return
        
        message = "📋 LE TUE PRENOTAZIONI:\n\n"
        
        for booking in bookings:
            booking_id, class_name, class_date, class_time, booking_date, status = booking
            
            date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
            day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
            
            if status == 'pending':
                emoji = "⏳"
                status_text = "Programmata"
            elif status == 'completed':
                emoji = "✅"
                status_text = "Prenotata"
            else:
                emoji = "❌"
                status_text = "Annullata"
            
            message += f"{emoji} #{booking_id} - {class_name}\n"
            message += f"   📅 {day_name} {date_obj.strftime('%d/%m')} ore {class_time}\n"
            message += f"   Status: {status_text}\n"
            
            if status == 'pending':
                message += f"   ⏰ Prenoterò: {booking_date.strftime('%d/%m alle %H:%M')}\n"
            
            message += "\n"
        
        message += "💡 Usa /cancella <ID> per annullare"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Errore /lista: {e}")
        await update.message.reply_text("❌ Errore.")


async def cancella(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /cancella - Cancella prenotazione"""
    user = update.effective_user
    user_id = str(user.id)
    logger.info(f"📱 /cancella da {user.first_name}")
    
    # Verifica argomento
    if not context.args:
        await update.message.reply_text(
            "⚠️  Specifica l'ID da cancellare.\n\n"
            "Esempio: /cancella 5\n\n"
            "Usa /lista per vedere gli ID."
        )
        return
    
    try:
        booking_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID non valido. Usa un numero.")
        return
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Verifica che esista e sia dell'utente
        cur.execute(
            """
            SELECT status FROM bookings
            WHERE id = %s AND user_id = %s
            """,
            (booking_id, user_id)
        )
        
        result = cur.fetchone()
        
        if not result:
            await update.message.reply_text(
                f"❌ Prenotazione #{booking_id} non trovata."
            )
            cur.close()
            conn.close()
            return
        
        status = result[0]
        
        if status != 'pending':
            await update.message.reply_text(
                f"⚠️  Prenotazione #{booking_id} già {status}.\n"
                f"Puoi cancellare solo prenotazioni 'pending'."
            )
            cur.close()
            conn.close()
            return
        
        # Cancella
        cur.execute(
            """
            UPDATE bookings
            SET status = 'cancelled'
            WHERE id = %s
            """,
            (booking_id,)
        )
        
        conn.commit()
        cur.close()
        conn.close()
        
        await update.message.reply_text(
            f"✅ Prenotazione #{booking_id} cancellata!"
        )
        
        logger.info(f"✅ Prenotazione #{booking_id} cancellata da {user_id}")
        
    except Exception as e:
        logger.error(f"Errore /cancella: {e}")
        await update.message.reply_text("❌ Errore.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /help"""
    await update.message.reply_text(
        "📖 GUIDA EASYFIT BOT\n\n"
        "🤖 Cosa fa:\n"
        "Prenota automaticamente le lezioni EasyFit "
        "esattamente 72 ore prima dell'inizio.\n\n"
        "📋 COMANDI:\n\n"
        "/prenota\n"
        "   Programma una nuova prenotazione.\n"
        "   Scegli lezione, giorno e orario dal calendario reale.\n"
        "   Il bot prenoterà automaticamente 72h prima.\n\n"
        "/lista\n"
        "   Vedi tutte le prenotazioni programmate.\n\n"
        "/cancella <ID>\n"
        "   Cancella una prenotazione programmata.\n"
        "   Esempio: /cancella 5\n\n"
        "⏰ ORARI:\n"
        "Attivo dalle 8:00 alle 21:00 ogni giorno.\n"
        "Controlla ogni 2 minuti.\n\n"
        "📲 NOTIFICHE:\n"
        "Riceverai un messaggio quando prenoto per te!\n\n"
        "✅ Le prenotazioni sono REALI su app EasyFit."
    )


# ============================================================================
# HEALTH CHECK
# ============================================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    
    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get('PORT', 10000))
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"🌐 Health server: porta {port}")
    except Exception as e:
        logger.error(f"❌ Health server: {e}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("")
    logger.info("="*70)
    logger.info("🚀 EASYFIT BOT - VERSIONE DEFINITIVA")
    logger.info("="*70)
    logger.info("✅ API EasyFit reale")
    logger.info("✅ Calendario reale")
    logger.info("✅ Tutti i comandi attivi")
    logger.info("="*70)
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handler comandi
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("prenota", prenota))
    application.add_handler(CommandHandler("lista", lista))
    application.add_handler(CommandHandler("cancella", cancella))
    application.add_handler(CommandHandler("help", help_command))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(class_selected, pattern="^class_"))
    application.add_handler(CallbackQueryHandler(date_selected, pattern="^date_"))
    application.add_handler(CallbackQueryHandler(time_selected, pattern="^time_"))
    
    # Scheduler ogni 2 minuti (8-21)
    logger.info("⏰ Scheduler: ogni 2 minuti (8-21)")
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: check_and_book(application),
        'cron',
        hour='8-21',
        minute='*/2'
    )
    scheduler.start()
    
    logger.info("✅ Scheduler avviato")
    
    start_health_server()
    
    logger.info("")
    logger.info("="*70)
    logger.info("✅ BOT PRONTO")
    logger.info("="*70)
    logger.info("")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
