import os
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variabili ambiente
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
EASYFIT_EMAIL = os.getenv('EASYFIT_EMAIL')
EASYFIT_PASSWORD = os.getenv('EASYFIT_PASSWORD')

# Configurazione EasyFit API
EASYFIT_BASE_URL = "https://app-easyfitpalestre.it"
ORGANIZATION_UNIT_ID = "1216915380"

# Connessione database
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# =============================================================================
# EASYFIT API FUNCTIONS - VERSIONE REALE
# =============================================================================

def easyfit_login():
    """Effettua login su EasyFit e restituisce session e token"""
    try:
        logger.info("🔐 Tentativo login EasyFit...")
        
        url = f"{EASYFIT_BASE_URL}/login"
        payload = {
            "username": EASYFIT_EMAIL,
            "password": EASYFIT_PASSWORD
        }
        
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            session_id = data.get('sessionId')
            access_token = data.get('access_token')
            
            logger.info(f"✅ Login effettuato! SessionID: {session_id[:20]}...")
            return session_id, access_token
        else:
            logger.error(f"❌ Login fallito: {response.status_code} - {response.text}")
            return None, None
            
    except Exception as e:
        logger.error(f"❌ Errore login: {e}")
        return None, None


def get_calendar_courses(start_date, end_date, session_id=None):
    """
    Recupera i corsi disponibili dal calendario di EasyFit
    Se viene passato session_id, usa quello, altrimenti fa login
    """
    try:
        logger.info(f"📅 Recupero calendario: {start_date} -> {end_date}")
        
        url = f"{EASYFIT_BASE_URL}/nox/public/v2/bookableitems/courses/with-canceled"
        params = {
            "startDate": start_date,
            "endDate": end_date,
            "employeeIds": "",
            "organizationUnitIds": ORGANIZATION_UNIT_ID
        }
        
        # Headers necessari
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "it-IT,it;q=0.9",
            "Origin": "https://app-easyfitpalestre.it",
            "Referer": f"https://app-easyfitpalestre.it/studio/ZWFzeWZpdDoxMjE2OTE1Mzgw/course"
        }
        
        # Se abbiamo session_id, aggiungi cookie
        if session_id:
            headers["Cookie"] = f"sessionId={session_id}"
        
        response = requests.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            courses = response.json()
            logger.info(f"✅ Calendario recuperato: {len(courses)} corsi trovati")
            return courses
        elif response.status_code == 401:
            # Se fallisce senza session, prova con login
            logger.warning("⚠️ Errore 401, provo con login...")
            session_id, _ = easyfit_login()
            if session_id:
                return get_calendar_courses(start_date, end_date, session_id)
            else:
                logger.error("❌ Login fallito, impossibile recuperare calendario")
                return []
        else:
            logger.error(f"❌ Errore recupero calendario: {response.status_code} - {response.text}")
            return []
            
    except Exception as e:
        logger.error(f"❌ Errore get_calendar_courses: {e}")
        return []


def book_course_easyfit(session_id, course_appointment_id):
    """Prenota un corso su EasyFit usando l'API reale"""
    try:
        logger.info(f"📝 Tentativo prenotazione corso ID: {course_appointment_id}")
        
        url = f"{EASYFIT_BASE_URL}/nox/v1/calendar/bookcourse"
        
        headers = {
            "Content-Type": "application/json",
            "Cookie": f"sessionId={session_id}"
        }
        
        payload = {
            "courseAppointmentId": course_appointment_id,
            "expectedCustomerStatus": "BOOKED"
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"✅ Prenotazione effettuata con successo!")
            return True, response.json()
        else:
            logger.error(f"❌ Prenotazione fallita: {response.status_code} - {response.text}")
            return False, None
            
    except Exception as e:
        logger.error(f"❌ Errore book_course_easyfit: {e}")
        return False, None


def find_course_appointment_id(class_name, class_date, class_time):
    """
    Trova il courseAppointmentId cercando nel calendario
    NOTA: Il courseAppointmentId sembra essere l'ID del benefit/corso
    """
    try:
        date_obj = datetime.strptime(class_date, '%Y-%m-%d')
        start_date = date_obj.strftime('%Y-%m-%d')
        end_date = date_obj.strftime('%Y-%m-%d')
        
        courses = get_calendar_courses(start_date, end_date)
        
        for course in courses:
            if course['name'].lower() == class_name.lower():
                for slot in course.get('slots', []):
                    slot_datetime_str = slot['startDateTime']
                    slot_datetime = slot_datetime_str.split('[')[0]
                    slot_time = slot_datetime.split('T')[1][:5]
                    
                    if slot_time == class_time:
                        # Usa l'ID del corso come courseAppointmentId
                        course_appointment_id = course['id']
                        
                        logger.info(f"✅ Corso trovato: {course['name']} - ID: {course_appointment_id}")
                        return course_appointment_id, slot
        
        logger.warning(f"⚠️ Corso non trovato: {class_name} - {class_date} {class_time}")
        return None, None
        
    except Exception as e:
        logger.error(f"❌ Errore find_course_appointment_id: {e}")
        return None, None


# =============================================================================
# COMANDI TELEGRAM
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Ciao {user.first_name}!\n\n"
        f"Sono il bot per prenotare le lezioni EasyFit.\n\n"
        f"🤖 Cosa posso fare:\n"
        f"• Prenotare lezioni 72 ore prima automaticamente\n"
        f"• Mostrarti le lezioni REALI disponibili da EasyFit\n"
        f"• Attivo dalle 8 alle 21 ogni giorno\n"
        f"• Notifiche quando prenoto\n\n"
        f"📋 Comandi disponibili:\n"
        f"/prenota - Programma una nuova prenotazione\n"
        f"/lista - Vedi prenotazioni programmate\n"
        f"/cancella - Cancella prenotazione\n"
        f"/help - Guida completa"
    )


async def prenota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📅 Sto recuperando le lezioni disponibili da EasyFit...\n"
        "⏳ Attendi qualche secondo..."
    )
    
    try:
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
        
        unique_courses = {}
        for course in courses:
            course_name = course['name']
            if course_name not in unique_courses:
                unique_courses[course_name] = course
        
        keyboard = []
        for course_name in sorted(unique_courses.keys()):
            keyboard.append([InlineKeyboardButton(
                f"📚 {course_name}",
                callback_data=f'class_{course_name}'
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📚 Lezioni disponibili nei prossimi 7 giorni:\n\n"
            f"💡 Seleziona la lezione che vuoi prenotare.",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Errore in /prenota: {e}")
        await update.message.reply_text(
            "❌ Si è verificato un errore.\n"
            "Riprova tra qualche minuto."
        )


async def class_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    class_name = query.data.split('_', 1)[1]
    context.user_data['class_name'] = class_name
    
    today = datetime.now()
    start_date = today.strftime('%Y-%m-%d')
    end_date = (today + timedelta(days=7)).strftime('%Y-%m-%d')
    
    courses = get_calendar_courses(start_date, end_date)
    
    available_dates = {}
    for course in courses:
        if course['name'] == class_name:
            for slot in course.get('slots', []):
                slot_datetime = slot['startDateTime'].split('[')[0]
                date_str = slot_datetime.split('T')[0]
                
                if date_str not in available_dates:
                    available_dates[date_str] = []
                available_dates[date_str].append(slot)
    
    if not available_dates:
        await query.edit_message_text(
            f"❌ Nessuna lezione di {class_name} disponibile nei prossimi 7 giorni."
        )
        return
    
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
        f"📚 {class_name}\n\n"
        f"📅 Quale giorno?",
        reply_markup=reply_markup
    )


async def date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    date_str = query.data.split('_')[1]
    context.user_data['date'] = date_str
    
    class_name = context.user_data['class_name']
    
    courses = get_calendar_courses(date_str, date_str)
    
    available_times = []
    for course in courses:
        if course['name'] == class_name:
            for slot in course.get('slots', []):
                slot_datetime = slot['startDateTime'].split('[')[0]
                time_str = slot_datetime.split('T')[1][:5]
                
                instructor = slot['employees'][0]['displayedName'] if slot.get('employees') else 'N/A'
                location = slot['locations'][0]['name'] if slot.get('locations') else 'N/A'
                bookable = slot.get('bookable', False)
                
                available_times.append({
                    'time': time_str,
                    'instructor': instructor,
                    'location': location,
                    'bookable': bookable,
                    'slot': slot
                })
    
    if not available_times:
        await query.edit_message_text(
            f"❌ Nessun orario disponibile per {class_name} in questa data."
        )
        return
    
    keyboard = []
    for item in available_times:
        status = "✅" if item['bookable'] else "⏰"
        button_text = f"{status} {item['time']} - {item['instructor']}"
        
        keyboard.append([InlineKeyboardButton(
            button_text,
            callback_data=f"time_{item['time']}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
    
    await query.edit_message_text(
        f"📚 {class_name}\n"
        f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n\n"
        f"🕐 Che orario?\n\n"
        f"✅ = Prenotabile ora\n"
        f"⏰ = Prenotabile tra 72h",
        reply_markup=reply_markup
    )


async def time_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    time_str = query.data.split('_')[1]
    context.user_data['time'] = time_str
    
    class_name = context.user_data['class_name']
    class_date = context.user_data['date']
    
    class_datetime = datetime.strptime(f"{class_date} {time_str}", '%Y-%m-%d %H:%M')
    booking_datetime = class_datetime - timedelta(hours=72)
    
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
            (
                str(query.from_user.id),
                class_name,
                class_date,
                time_str,
                booking_datetime,
                'pending'
            )
        )
        
        booking_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        date_obj = datetime.strptime(class_date, '%Y-%m-%d')
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
            f"ID Prenotazione: #{booking_id}"
        )
        
        logger.info(f"✅ Prenotazione #{booking_id} salvata: {class_name} - {class_date} {time_str}")
        
    except Exception as e:
        logger.error(f"Errore salvataggio database: {e}")
        await query.edit_message_text(
            "❌ Errore nel salvare la prenotazione. Riprova."
        )


async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            SELECT id, class_name, class_date, class_time, booking_date, status
            FROM bookings
            WHERE user_id = %s AND status = 'pending'
            ORDER BY class_date, class_time
            """,
            (user_id,)
        )
        
        bookings = cur.fetchall()
        cur.close()
        conn.close()
        
        if not bookings:
            await update.message.reply_text(
                "📋 Non hai prenotazioni programmate.\n\n"
                "Usa /prenota per programmarne una!"
            )
            return
        
        message = "📋 PRENOTAZIONI PROGRAMMATE:\n\n"
        
        for booking in bookings:
            booking_id, class_name, class_date, class_time, booking_date, status = booking
            date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
            day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
            
            message += f"#{booking_id} - {class_name}\n"
            message += f"   📅 {day_name} {date_obj.strftime('%d/%m/%Y')} ore {class_time}\n"
            message += f"   ⏰ Prenoterò il {booking_date.strftime('%d/%m/%Y alle %H:%M')}\n\n"
        
        message += "💡 Usa /cancella per cancellare una prenotazione"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Errore recupero prenotazioni: {e}")
        await update.message.reply_text("❌ Errore nel recuperare le prenotazioni.")


async def cancella(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            SELECT id, class_name, class_date, class_time
            FROM bookings
            WHERE user_id = %s AND status = 'pending'
            ORDER BY class_date, class_time
            """,
            (user_id,)
        )
        
        bookings = cur.fetchall()
        cur.close()
        conn.close()
        
        if not bookings:
            await update.message.reply_text(
                "📋 Non hai prenotazioni da cancellare.\n\n"
                "Usa /prenota per programmarne una!"
            )
            return
        
        keyboard = []
        for booking in bookings:
            booking_id, class_name, class_date, class_time = booking
            date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
            day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
            
            button_text = f"#{booking_id} - {class_name} - {day_name} {date_obj.strftime('%d/%m')} {class_time}"
            keyboard.append([InlineKeyboardButton(
                button_text,
                callback_data=f'cancel_{booking_id}'
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🗑️ Quale prenotazione vuoi cancellare?\n\n"
            "Seleziona dalla lista:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Errore in cancella: {e}")
        await update.message.reply_text("❌ Errore nel recuperare le prenotazioni.")


async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    booking_id = int(query.data.split('_')[1])
    user_id = str(query.from_user.id)
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            SELECT class_name, class_date, class_time
            FROM bookings
            WHERE id = %s AND user_id = %s AND status = 'pending'
            """,
            (booking_id, user_id)
        )
        
        booking = cur.fetchone()
        
        if not booking:
            await query.edit_message_text(
                "❌ Prenotazione non trovata o già cancellata."
            )
            cur.close()
            conn.close()
            return
        
        class_name, class_date, class_time = booking
        
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
        
        date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
        day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
        
        await query.edit_message_text(
            f"✅ Prenotazione cancellata!\n\n"
            f"📚 Lezione: {class_name}\n"
            f"📅 Data: {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
            f"🕐 Orario: {class_time}\n\n"
            f"ID: #{booking_id}\n\n"
            f"💡 Usa /prenota per programmarne un'altra."
        )
        
        logger.info(f"✅ Prenotazione #{booking_id} cancellata da utente {user_id}")
        
    except Exception as e:
        logger.error(f"Errore cancellazione prenotazione: {e}")
        await query.edit_message_text(
            "❌ Errore nella cancellazione. Riprova."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 GUIDA EASYFIT BOT\n\n"
        "🤖 Cosa fa questo bot:\n"
        "Prenota automaticamente le tue lezioni EasyFit "
        "esattamente 72 ore prima dell'inizio.\n"
        "Mostra le lezioni REALI disponibili dal calendario EasyFit!\n\n"
        "📋 COMANDI:\n\n"
        "/prenota - Programma una nuova prenotazione\n"
        "   Vedi lezioni reali, scegli giorno e orario.\n"
        "   Il bot prenoterà automaticamente 72h prima.\n\n"
        "/lista - Vedi tutte le prenotazioni programmate\n"
        "   Mostra cosa hai in programma.\n\n"
        "/cancella - Cancella una prenotazione\n"
        "   Ti mostrerò la lista e scegli quale cancellare.\n\n"
        "⏰ ORARI:\n"
        "Il bot è attivo dalle 8:00 alle 21:00 ogni giorno.\n"
        "Controlla ogni 2 minuti se ci sono prenotazioni da fare.\n\n"
        "📲 NOTIFICHE:\n"
        "Riceverai un messaggio quando il bot prenota per te!\n\n"
        "🎯 NOVITÀ: Il bot ora mostra le lezioni VERE da EasyFit! 🎉"
    )


def send_telegram_notification(application, user_id, class_name, class_date, class_time, success):
    try:
        import asyncio
        
        date_obj = datetime.strptime(class_date, '%Y-%m-%d')
        day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
        
        if success:
            message = (
                f"✅ PRENOTAZIONE EFFETTUATA!\n\n"
                f"Ho prenotato la tua lezione:\n\n"
                f"📚 {class_name}\n"
                f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                f"🕐 Ore {class_time}\n\n"
                f"Ci vediamo in palestra! 💪"
            )
        else:
            message = (
                f"❌ PRENOTAZIONE FALLITA\n\n"
                f"Non sono riuscito a prenotare:\n\n"
                f"📚 {class_name}\n"
                f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                f"🕐 Ore {class_time}\n\n"
                f"⚠️ Prova a prenotare manualmente o contatta il supporto."
            )
        
        asyncio.run(application.bot.send_message(chat_id=user_id, text=message))
        logger.info(f"📲 Notifica inviata a {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Errore invio notifica: {e}")


def check_and_book(application):
    current_hour = datetime.now().hour
    if not (8 <= current_hour < 21):
        logger.info("⏰ Fuori orario attivo (8-21). Salto controllo.")
        return
    
    logger.info("🔍 Controllo prenotazioni da effettuare...")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        now = datetime.now()
        two_hours_ago = now - timedelta(hours=2)
        
        cur.execute(
            """
            SELECT id, user_id, class_name, class_date, class_time
            FROM bookings
            WHERE status = 'pending'
            AND booking_date BETWEEN %s AND %s
            """,
            (two_hours_ago, now)
        )
        
        bookings_to_make = cur.fetchall()
        
        if not bookings_to_make:
            logger.info("ℹ️ Nessuna prenotazione da effettuare.")
            cur.close()
            conn.close()
            return
        
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time = booking
            
            logger.info(f"📝 Prenotazione #{booking_id}: {class_name} per {class_date} {class_time}")
            
            session_id, access_token = easyfit_login()
            
            if not session_id:
                logger.error(f"❌ Login fallito per prenotazione #{booking_id}")
                send_telegram_notification(application, user_id, class_name, class_date, class_time, False)
                continue
            
            course_appointment_id, slot = find_course_appointment_id(class_name, class_date, class_time)
            
            if not course_appointment_id:
                logger.error(f"❌ Corso non trovato per prenotazione #{booking_id}")
                send_telegram_notification(application, user_id, class_name, class_date, class_time, False)
                continue
            
            success, result = book_course_easyfit(session_id, course_appointment_id)
            
            if success:
                cur.execute(
                    """
                    UPDATE bookings
                    SET status = 'completed'
                    WHERE id = %s
                    """,
                    (booking_id,)
                )
                conn.commit()
                
                send_telegram_notification(application, user_id, class_name, class_date, class_time, True)
                
                logger.info(f"✅ Prenotazione #{booking_id} completata!")
            else:
                send_telegram_notification(application, user_id, class_name, class_date, class_time, False)
                logger.error(f"❌ Prenotazione #{booking_id} fallita!")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Errore nel controllo prenotazioni: {e}")


# =============================================================================
# HEALTH CHECK SERVER (per Render.com)
# =============================================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot EasyFit is running OK')
    
    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get('PORT', 10000))
    
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"🌐 Health check server avviato su porta {port}")
    except Exception as e:
        logger.error(f"❌ Errore avvio health server: {e}")


def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("prenota", prenota))
    application.add_handler(CommandHandler("lista", lista))
    application.add_handler(CommandHandler("cancella", cancella))
    application.add_handler(CommandHandler("help", help_command))
    
    application.add_handler(CallbackQueryHandler(class_selected, pattern="^class_"))
    application.add_handler(CallbackQueryHandler(date_selected, pattern="^date_"))
    application.add_handler(CallbackQueryHandler(time_selected, pattern="^time_"))
    application.add_handler(CallbackQueryHandler(cancel_booking, pattern="^cancel_"))
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: check_and_book(application),
        'cron',
        hour='8-21',
        minute='*/2'
    )
    scheduler.start()
    
    start_health_server()
    
    logger.info("="*60)
    logger.info("🚀 BOT AVVIATO CON LEZIONI REALI DA EASYFIT!")
    logger.info("⏰ Attivo dalle 8:00 alle 21:00 (controllo ogni 2 minuti)")
    logger.info("🎯 Calendario REALE da app-easyfitpalestre.it")
    logger.info("="*60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
