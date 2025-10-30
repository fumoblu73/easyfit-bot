import os
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import threading
import asyncio
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
    return psycopg2.connect(DATABASE_URL, sslmode='require')


# =============================================================================
# EASYFIT API FUNCTIONS
# =============================================================================

def easyfit_login():
    """Effettua login su EasyFit e restituisce session object"""
    try:
        logger.info("🔐 Login EasyFit...")
        
        session = requests.Session()
        
        url = f"{EASYFIT_BASE_URL}/login"
        
        # Basic Auth
        import base64
        credentials = f"{EASYFIT_EMAIL}:{EASYFIT_PASSWORD}"
        basic_auth = base64.b64encode(credentials.encode()).decode()
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "it-IT,it;q=0.9",
            "Authorization": f"Basic {basic_auth}",
            "Origin": "https://app-easyfitpalestre.it",
            "Referer": "https://app-easyfitpalestre.it/studio/ZWFzeWZpdDoxMjE2OTE1Mzgw/course",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "x-tenant": "easyfit",
            "x-ms-web-context": "/studio/ZWFzeWZpdDoxMjE2OTE1Mzgw",
            "x-nox-client-type": "WEB",
            "x-nox-web-context": "v=1",
            "x-public-facility-group": "BRANDEDAPP-263FBF081EAB42E6A62602B2DDDE4506"
        }
        
        payload = {
            "username": EASYFIT_EMAIL,
            "password": EASYFIT_PASSWORD
        }
        
        response = session.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            session_id = data.get('sessionId')
            
            logger.info(f"✅ Login OK! SessionID: {session_id[:20] if session_id else 'N/A'}...")
            
            # Salva sessionId come attributo della sessione
            session.session_id = session_id
            
            return session
        else:
            logger.error(f"❌ Login fallito: {response.status_code}")
            logger.error(f"   Response: {response.text[:200]}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Errore login: {e}")
        return None


def get_calendar_courses(session, start_date, end_date):
    """
    Recupera i corsi disponibili dal calendario
    USA LA SESSIONE PASSATA, non fa nuovo login
    """
    try:
        logger.info(f"📅 Range: {start_date} → {end_date}")
        
        url = f"{EASYFIT_BASE_URL}/nox/public/v2/bookableitems/courses/with-canceled"
        
        params = {
            "startDate": start_date,
            "endDate": end_date,
            "employeeIds": "",
            "organizationUnitIds": ORGANIZATION_UNIT_ID
        }
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "it-IT,it;q=0.9",
            "Origin": "https://app-easyfitpalestre.it",
            "Referer": f"https://app-easyfitpalestre.it/studio/ZWFzeWZpdDoxMjE2OTE1Mzgw/course",
            "x-tenant": "easyfit",
            "x-ms-web-context": "/studio/ZWFzeWZpdDoxMjE2OTE1Mzgw",
            "x-nox-client-type": "WEB",
            "x-nox-web-context": "v=1",
            "x-public-facility-group": "BRANDEDAPP-263FBF081EAB42E6A62602B2DDDE4506"
        }
        
        logger.info(f"🔍 Richiesta calendario...")
        
        # USA LA SESSIONE (con cookie) invece di fare nuova richiesta
        response = session.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            courses = response.json()
            logger.info(f"✅ Recuperate {len(courses)} lezioni")
            return courses
        else:
            logger.error(f"❌ Errore calendario: {response.status_code}")
            logger.error(f"   Response: {response.text[:200]}")
            return []
            
    except Exception as e:
        logger.error(f"❌ Errore get_calendar_courses: {e}")
        return []


def book_course_easyfit(session, course_appointment_id, try_waitlist=True):
    """Prenota un corso su EasyFit"""
    try:
        logger.info(f"📝 Prenotazione ID: {course_appointment_id}")
        
        url = f"{EASYFIT_BASE_URL}/nox/v1/calendar/bookcourse"
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "it-IT,it;q=0.9",
            "Origin": "https://app-easyfitpalestre.it",
            "Referer": "https://app-easyfitpalestre.it/studio/ZWFzeWZpdDoxMjE2OTE1Mzgw/course",
            "x-tenant": "easyfit",
            "x-ms-web-context": "/studio/ZWFzeWZpdDoxMjE2OTE1Mzgw",
            "x-nox-client-type": "WEB",
            "x-nox-web-context": "v=1",
            "x-public-facility-group": "BRANDEDAPP-263FBF081EAB42E6A62602B2DDDE4506"
        }
        
        # Tentativo 1: Prenotazione normale
        payload = {
            "courseAppointmentId": course_appointment_id,
            "expectedCustomerStatus": "BOOKED"
        }
        
        response = session.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"✅ PRENOTATO!")
            return True, "completed", response.json()
        
        # Se fallisce e try_waitlist è True, prova lista d'attesa
        if try_waitlist:
            logger.info(f"⏳ Tentativo lista d'attesa...")
            
            # STEP 1: Valida (come fa il browser)
            validate_url = f"{EASYFIT_BASE_URL}/v1/me/masterdata/validate/bookcourse"
            validate_payload = {
                "courseAppointmentId": course_appointment_id,
                "expectedCustomerStatus": "WAITING_LIST"
            }
            
            validate_response = session.post(validate_url, json=validate_payload, headers=headers, timeout=10)
            
            if validate_response.status_code == 200:
                logger.info(f"✅ Validazione lista d'attesa OK")
                
                # STEP 2: Effettua prenotazione lista d'attesa (endpoint originale)
                waitlist_payload = {
                    "courseAppointmentId": course_appointment_id,
                    "expectedCustomerStatus": "WAITING_LIST"
                }
                
                response = session.post(url, json=waitlist_payload, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    logger.info(f"📋 LISTA D'ATTESA!")
                    return True, "waitlisted", response.json()
                else:
                    logger.warning(f"⚠️ Prenotazione lista d'attesa fallita: {response.status_code}")
                    logger.warning(f"   Response: {response.text[:200]}")
                    return False, "waitlist_unavailable", None
            else:
                # Validazione fallita
                logger.warning(f"⚠️ Validazione lista d'attesa fallita: {validate_response.status_code}")
                logger.warning(f"   Response: {validate_response.text[:200]}")
                return False, "waitlist_unavailable", None
        
        logger.error(f"❌ Prenotazione fallita: {response.status_code}")
        logger.error(f"   Response: {response.text[:200]}")
        return False, "full", None
            
    except Exception as e:
        logger.error(f"❌ Errore book_course_easyfit: {e}")
        return False, "failed", None


def find_course_appointment_id(session, class_name, class_date, class_time):
    """Trova il courseAppointmentId cercando nel calendario"""
    try:
        # class_date può essere str o datetime.date dal database
        if isinstance(class_date, str):
            date_obj = datetime.strptime(class_date, '%Y-%m-%d')
        else:
            # È già un oggetto date dal database
            date_obj = class_date
        
        start_date = date_obj.strftime('%Y-%m-%d')
        end_date = date_obj.strftime('%Y-%m-%d')
        
        logger.info(f"🔎 Cerco: {class_name} {class_date} {class_time}")
        
        courses = get_calendar_courses(session, start_date, end_date)
        
        for course in courses:
            if course['name'].lower() == class_name.lower():
                for slot in course.get('slots', []):
                    # FIX CRITICO: Rimuovi [Europe/Rome] prima del parsing
                    slot_datetime_str = slot['startDateTime']
                    slot_datetime = slot_datetime_str.split('[')[0]  # Rimuove [Europe/Rome]
                    slot_time = slot_datetime.split('T')[1][:5]
                    
                    if slot_time == class_time:
                        # L'ID è a livello di CORSO, non di slot
                        course_appointment_id = course.get('id')
                        
                        if not course_appointment_id:
                            logger.error(f"❌ Corso trovato ma senza ID")
                            logger.error(f"   Course keys: {list(course.keys())}")
                            continue
                        
                        logger.info(f"✅ Trovato ID: {course_appointment_id}")
                        return course_appointment_id, slot
        
        logger.warning(f"⚠️ Corso non trovato")
        return None, None
        
    except Exception as e:
        logger.error(f"❌ Errore find_course_appointment_id: {e}")
        import traceback
        logger.error(traceback.format_exc())
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
        f"• Attivo dalle 8 alle 21 ogni giorno\n"
        f"• Notifiche quando prenoto\n\n"
        f"📋 Comandi disponibili:\n"
        f"/prenota - Programma una nuova prenotazione\n"
        f"/lista - Vedi prenotazioni programmate\n"
        f"/cancella - Cancella prenotazione\n"
        f"/help - Guida completa"
    )


async def prenota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    IMPORTANTE: Fa login UNA volta e salva la sessione in context.user_data
    """
    logger.info(f"📱 /prenota da {update.effective_user.first_name}")
    
    await update.message.reply_text(
        "📅 Sto recuperando le lezioni disponibili da EasyFit...\n"
        "⏳ Attendi qualche secondo..."
    )
    
    try:
        # FASE 1: Login e salva sessione
        session = easyfit_login()
        
        if not session:
            await update.message.reply_text(
                "❌ Impossibile connettersi a EasyFit.\n"
                "Riprova tra qualche minuto."
            )
            return
        
        # SALVA SESSIONE nel context
        context.user_data['easyfit_session'] = session
        
        # FASE 2: Recupera calendario
        today = datetime.now()
        start_date = today.strftime('%Y-%m-%d')
        end_date = (today + timedelta(days=7)).strftime('%Y-%m-%d')
        
        courses = get_calendar_courses(session, start_date, end_date)
        
        if not courses:
            await update.message.reply_text(
                "❌ Nessuna lezione trovata nei prossimi 7 giorni.\n"
                "Riprova più tardi."
            )
            return
        
        # FASE 3: Mostra lezioni uniche
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
        logger.error(f"❌ Errore /prenota: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await update.message.reply_text(
            "❌ Si è verificato un errore.\n"
            "Riprova tra qualche minuto."
        )


async def class_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usa la sessione salvata in context.user_data"""
    query = update.callback_query
    await query.answer()
    
    class_name = query.data.split('_', 1)[1]
    context.user_data['class_name'] = class_name
    
    # RECUPERA SESSIONE
    session = context.user_data.get('easyfit_session')
    
    if not session:
        await query.edit_message_text(
            "❌ Sessione scaduta. Riavvia con /prenota"
        )
        return
    
    today = datetime.now()
    start_date = today.strftime('%Y-%m-%d')
    end_date = (today + timedelta(days=7)).strftime('%Y-%m-%d')
    
    # USA LA SESSIONE SALVATA
    courses = get_calendar_courses(session, start_date, end_date)
    
    available_dates = {}
    for course in courses:
        if course['name'] == class_name:
            for slot in course.get('slots', []):
                # FIX: Rimuovi [Europe/Rome]
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
    """Usa la sessione salvata in context.user_data"""
    query = update.callback_query
    await query.answer()
    
    date_str = query.data.split('_')[1]
    context.user_data['date'] = date_str
    
    class_name = context.user_data['class_name']
    
    # RECUPERA SESSIONE
    session = context.user_data.get('easyfit_session')
    
    if not session:
        await query.edit_message_text(
            "❌ Sessione scaduta. Riavvia con /prenota"
        )
        return
    
    # USA LA SESSIONE SALVATA
    courses = get_calendar_courses(session, date_str, date_str)
    
    available_times = []
    for course in courses:
        if course['name'] == class_name:
            for slot in course.get('slots', []):
                # FIX: Rimuovi [Europe/Rome]
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
    """Salva la prenotazione nel database"""
    query = update.callback_query
    await query.answer()
    
    time_str = query.data.split('_')[1]
    context.user_data['time'] = time_str
    
    class_name = context.user_data['class_name']
    class_date = context.user_data['date']
    
    # Calcola quando prenotare (72h prima)
    class_datetime = datetime.strptime(f"{class_date} {time_str}", '%Y-%m-%d %H:%M')
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
            WHERE user_id = %s AND status IN ('pending', 'waitlisted')
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
        
        message = "📋 LE TUE PRENOTAZIONI:\n\n"
        
        for booking in bookings:
            booking_id, class_name, class_date, class_time, booking_date, status = booking
            
            # Gestione date: può essere str o datetime.date
            if isinstance(class_date, str):
                date_obj = datetime.strptime(class_date, '%Y-%m-%d')
            else:
                # È già datetime.date dal database
                date_obj = class_date
            
            day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
            
            if status == 'waitlisted':
                status_icon = "📋"
                status_text = "Lista d'attesa"
            else:
                status_icon = "⏳"
                status_text = "Programmata"
            
            message += f"{status_icon} #{booking_id} - {class_name}\n"
            message += f"   📅 {day_name} {date_obj.strftime('%d/%m/%Y')} ore {class_time}\n"
            message += f"   Status: {status_text}\n"
            if status == 'pending':
                message += f"   ⏰ Prenoterò il {booking_date.strftime('%d/%m/%Y alle %H:%M')}\n"
            message += "\n"
        
        message += "💡 Usa /cancella <ID> per cancellare"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Errore recupero prenotazioni: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await update.message.reply_text("❌ Errore nel recuperare le prenotazioni.")


async def cancella(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancella una prenotazione"""
    user_id = str(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text(
            "❌ Uso corretto: /cancella <ID>\n\n"
            "Esempio: /cancella 5\n\n"
            "Usa /lista per vedere gli ID delle tue prenotazioni."
        )
        return
    
    try:
        booking_id = int(context.args[0])
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Verifica che la prenotazione esista e appartenga all'utente
        cur.execute(
            """
            SELECT id, class_name, class_date, class_time 
            FROM bookings
            WHERE id = %s AND user_id = %s AND status = 'pending'
            """,
            (booking_id, user_id)
        )
        
        result = cur.fetchone()
        
        if not result:
            await update.message.reply_text(
                f"❌ Prenotazione #{booking_id} non trovata o già completata."
            )
            cur.close()
            conn.close()
            return
        
        # Cancella
        cur.execute(
            """
            DELETE FROM bookings
            WHERE id = %s AND user_id = %s
            """,
            (booking_id, user_id)
        )
        
        conn.commit()
        cur.close()
        conn.close()
        
        _, class_name, class_date, class_time = result
        
        await update.message.reply_text(
            f"✅ Prenotazione #{booking_id} cancellata!\n\n"
            f"📚 {class_name}\n"
            f"📅 {class_date} ore {class_time}"
        )
        
    except ValueError:
        await update.message.reply_text(
            "❌ ID non valido. Deve essere un numero.\n\n"
            "Esempio: /cancella 5"
        )
    except Exception as e:
        logger.error(f"Errore cancellazione: {e}")
        await update.message.reply_text(
            "❌ Errore durante la cancellazione."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 GUIDA EASYFIT BOT\n\n"
        "🤖 Cosa fa questo bot:\n"
        "Prenota automaticamente le tue lezioni EasyFit "
        "esattamente 72 ore prima dell'inizio.\n\n"
        "📋 COMANDI:\n\n"
        "/prenota - Programma una nuova prenotazione\n"
        "   Scegli lezione, giorno e orario.\n"
        "   Il bot prenoterà automaticamente 72h prima.\n\n"
        "/lista - Vedi tutte le prenotazioni programmate\n"
        "   Mostra cosa hai in programma.\n\n"
        "/cancella <ID> - Cancella una prenotazione\n"
        "   Esempio: /cancella 5\n\n"
        "⏰ ORARI:\n"
        "Il bot è attivo dalle 8:00 alle 21:00 ogni giorno.\n"
        "Controlla ogni minuto se ci sono prenotazioni da fare.\n\n"
        "📲 NOTIFICHE:\n"
        "Riceverai un messaggio quando il bot prenota per te!\n\n"
        "❓ Problemi? Riavvia con /start"
    )


# =============================================================================
# SCHEDULER - CONTROLLA E PRENOTA
# =============================================================================

def send_notification_from_thread(bot, user_id, message):
    """
    Invia notifica Telegram in modo sincrono dallo scheduler.
    FIX: Usa nest_asyncio e NON chiude il loop.
    """
    try:
        import nest_asyncio
        nest_asyncio.apply()
        
        # Crea una coroutine per l'invio
        async def send():
            await bot.send_message(chat_id=user_id, text=message)
        
        # Ottieni o crea event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Esegui la notifica
        loop.run_until_complete(send())
        
        # NON chiudere il loop!
        # loop.close()  ← RIMOSSO
        
        logger.info(f"📲 Notifica inviata a {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Errore notifica: {e}")
        import traceback
        logger.error(traceback.format_exc())


def check_and_book(application):
    """Controlla se ci sono prenotazioni da fare"""
    
    # Controlla se siamo nell'orario attivo (8-21)
    current_hour = datetime.now().hour
    if not (8 <= current_hour < 21):
        logger.info("⏰ Fuori orario attivo (8-21)")
        return
    
    logger.info("🔍 CONTROLLO PRENOTAZIONI")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Trova prenotazioni da fare (booking_date <= ora attuale)
        now = datetime.now()
        
        cur.execute(
            """
            SELECT id, user_id, class_name, class_date, class_time
            FROM bookings
            WHERE status = 'pending'
            AND booking_date <= %s
            ORDER BY booking_date ASC
            """,
            (now,)
        )
        
        bookings_to_make = cur.fetchall()
        
        if not bookings_to_make:
            logger.info("ℹ️ Nessuna prenotazione da effettuare")
            cur.close()
            conn.close()
            return
        
        logger.info(f"📋 Trovate {len(bookings_to_make)} prenotazioni")
        
        # Per ogni prenotazione da fare
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time = booking
            
            logger.info(f"📝 PRENOTAZIONE #{booking_id}")
            logger.info(f"   📚 {class_name}")
            logger.info(f"   📅 {class_date} ore {class_time}")
            
            # LOGIN FRESCO
            session = easyfit_login()
            
            if not session:
                logger.error(f"❌ Login fallito per prenotazione #{booking_id}")
                continue
            
            # TROVA ID CORSO
            course_appointment_id, slot = find_course_appointment_id(
                session, 
                class_name, 
                class_date, 
                class_time
            )
            
            if not course_appointment_id:
                logger.error(f"❌ Corso non trovato per prenotazione #{booking_id}")
                
                # Segna come completata comunque
                cur.execute(
                    "UPDATE bookings SET status = 'completed' WHERE id = %s",
                    (booking_id,)
                )
                conn.commit()
                
                # Notifica utente - FIX: USA run_coroutine_threadsafe
                message = (
                    f"❌ PRENOTAZIONE NON POSSIBILE\n\n"
                    f"📚 {class_name}\n"
                    f"📅 {class_date}\n"
                    f"🕐 {class_time}\n\n"
                    f"La lezione non è stata trovata nel calendario.\n"
                    f"Potrebbe essere stata cancellata."
                )
                
                send_notification_from_thread(
                    application.bot,
                    user_id,
                    message
                )
                
                continue
            
            # PRENOTA
            success, status, response = book_course_easyfit(session, course_appointment_id)
            
            if success:
                # Aggiorna status nel database
                cur.execute(
                    "UPDATE bookings SET status = %s WHERE id = %s",
                    (status, booking_id)
                )
                conn.commit()
                
                # Notifica utente - FIX: USA run_coroutine_threadsafe
                if status == "completed":
                    message = (
                        f"✅ PRENOTAZIONE EFFETTUATA!\n\n"
                        f"📚 {class_name}\n"
                        f"📅 {class_date}\n"
                        f"🕐 {class_time}\n\n"
                        f"Ci vediamo in palestra! 💪"
                    )
                elif status == "waitlisted":
                    message = (
                        f"⏳ IN LISTA D'ATTESA\n\n"
                        f"📚 {class_name}\n"
                        f"📅 {class_date}\n"
                        f"🕐 {class_time}\n\n"
                        f"⚠️ La lezione era piena!\n"
                        f"Sei stato inserito in lista d'attesa.\n\n"
                        f"🔔 Ti avviseremo se si libera un posto!\n"
                        f"Controlla l'app EasyFit per aggiornamenti."
                    )
                
                send_notification_from_thread(
                    application.bot,
                    user_id,
                    message
                )
                
                logger.info(f"🎉 Completata prenotazione #{booking_id}")
            else:
                # Prenotazione fallita
                logger.error(f"❌ Prenotazione #{booking_id} fallita - Status: {status}")
                
                # Segna come completata comunque
                cur.execute(
                    "UPDATE bookings SET status = 'completed' WHERE id = %s",
                    (booking_id,)
                )
                conn.commit()
                
                # Notifica diversa in base al tipo di fallimento
                if status == "waitlist_unavailable":
                    message = (
                        f"❌ LEZIONE PIENA\n\n"
                        f"📚 {class_name}\n"
                        f"📅 {class_date}\n"
                        f"🕐 {class_time}\n\n"
                        f"⚠️ La lezione è piena e non ha lista d'attesa disponibile.\n\n"
                        f"💡 Prova:\n"
                        f"• Prenotare un altro orario\n"
                        f"• Controllare manualmente sull'app se si liberano posti"
                    )
                elif status == "full":
                    message = (
                        f"❌ LEZIONE PIENA\n\n"
                        f"📚 {class_name}\n"
                        f"📅 {class_date}\n"
                        f"🕐 {class_time}\n\n"
                        f"⚠️ La lezione è completamente piena.\n"
                        f"Anche la lista d'attesa non è disponibile.\n\n"
                        f"💡 Scegli un altro giorno/orario."
                    )
                else:
                    message = (
                        f"❌ PRENOTAZIONE FALLITA\n\n"
                        f"📚 {class_name}\n"
                        f"📅 {class_date}\n"
                        f"🕐 {class_time}\n\n"
                        f"Si è verificato un errore durante la prenotazione.\n"
                        f"Prova manualmente su app EasyFit."
                    )
                
                send_notification_from_thread(
                    application.bot,
                    user_id,
                    message
                )
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Errore check_and_book: {e}")
        import traceback
        logger.error(traceback.format_exc())


# =============================================================================
# HEALTH CHECK SERVER
# =============================================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"💓 Health server su porta {port}")
    server.serve_forever()


# =============================================================================
# KEEP-ALIVE PING
# =============================================================================

def keep_alive_ping():
    """Fa un ping a se stesso ogni 10 minuti per evitare spin-down"""
    try:
        port = int(os.environ.get('PORT', 10000))
        url = f"http://localhost:{port}/"
        response = requests.get(url, timeout=5)
        logger.info("💓 Keep-alive ping OK")
    except Exception as e:
        logger.error(f"❌ Keep-alive ping fallito: {e}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Avvia il bot"""
    
    logger.info("🚀 Avvio EasyFit Bot...")
    
    # Crea applicazione
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Registra handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("prenota", prenota))
    application.add_handler(CommandHandler("lista", lista))
    application.add_handler(CommandHandler("cancella", cancella))
    application.add_handler(CommandHandler("help", help_command))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(class_selected, pattern="^class_"))
    application.add_handler(CallbackQueryHandler(date_selected, pattern="^date_"))
    application.add_handler(CallbackQueryHandler(time_selected, pattern="^time_"))
    
    # Avvia health server in thread separato
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Avvia scheduler
    scheduler = BackgroundScheduler()
    
    # Controllo prenotazioni ogni minuto (8-21)
    scheduler.add_job(
        lambda: check_and_book(application),
        'cron',
        hour='8-21',
        minute='*',
        id='check_bookings'
    )
    
    # Keep-alive ping ogni 10 minuti (24/7)
    scheduler.add_job(
        keep_alive_ping,
        'cron',
        minute='*/10',
        id='keep_alive'
    )
    
    scheduler.start()
    
    logger.info("✅ Bot pronto!")
    logger.info("⏰ Attivo 8-21 per prenotazioni")
    logger.info("💓 Keep-alive attivo 24/7")
    
    # Avvia bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
