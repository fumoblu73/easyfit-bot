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
import pytz

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

# Timezone Italia
ROME_TZ = pytz.timezone('Europe/Rome')

# Connection pool per gestire meglio le connessioni Supabase
import psycopg2.pool
from threading import Lock

# Pool globale di connessioni
db_pool = None
pool_lock = Lock()

def init_db_pool():
    """Inizializza il connection pool"""
    global db_pool
    with pool_lock:
        if db_pool is None:
            try:
                db_pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=5,
                    dsn=DATABASE_URL,
                    sslmode='require',
                    connect_timeout=10
                )
                logger.info("💾 Connection pool inizializzato")
            except Exception as e:
                logger.error(f"❌ Errore init pool: {e}")
                db_pool = None

def get_db_connection(max_retries=3):
    """
    Ottiene connessione dal pool con retry automatico
    Gestisce errori SSL intermittenti di Supabase
    """
    import time
    
    # Inizializza pool se necessario
    if db_pool is None:
        init_db_pool()
    
    for attempt in range(max_retries):
        try:
            conn = db_pool.getconn()
            # Testa la connessione
            cur = conn.cursor()
            cur.execute('SELECT 1')
            cur.close()
            return conn
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                logger.warning(f"⚠️ Errore DB (tentativo {attempt + 1}/{max_retries}): {str(e)[:100]}")
                logger.info(f"⏳ Riprovo tra {wait_time}s...")
                
                # Rilascia connessione corrotta se esiste
                try:
                    if 'conn' in locals():
                        db_pool.putconn(conn, close=True)
                except:
                    pass
                
                time.sleep(wait_time)
            else:
                logger.error(f"❌ Connessione DB fallita dopo {max_retries} tentativi")
                raise

def release_db_connection(conn):
    """Rilascia connessione al pool"""
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except Exception as e:
        logger.warning(f"⚠️ Errore rilascio connessione: {e}")


# =============================================================================
# EASYFIT API FUNCTIONS
# =============================================================================

def parse_course_datetime(date_string):
    """
    Parse datetime da calendario EasyFit
    Gestisce vari formati possibili
    """
    from datetime import timezone
    
    if not date_string:
        return None
    
    try:
        # Caso 1: ISO con Z (UTC)
        if date_string.endswith('Z'):
            dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
            return dt
        
        # Caso 2: ISO con timezone (+01:00, +00:00, etc)
        if '+' in date_string or date_string.count('-') > 2:
            dt = datetime.fromisoformat(date_string)
            return dt
        
        # Caso 3: ISO senza timezone (assume UTC)
        if 'T' in date_string:
            dt = datetime.fromisoformat(date_string)
            # Se naive, aggiungi UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        
        # Caso 4: Solo data (YYYY-MM-DD)
        if len(date_string) == 10:
            dt = datetime.strptime(date_string, '%Y-%m-%d')
            dt = dt.replace(tzinfo=timezone.utc)
            return dt
        
        logger.warning(f"⚠️ Formato data non riconosciuto: {date_string}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Errore parse data '{date_string}': {e}")
        return None


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
            
            # DEBUG: Log formato RAW prime 3 lezioni
            if courses:
                logger.info("🔍 DEBUG - Prime 3 lezioni RAW:")
                for i, course in enumerate(courses[:3]):
                    logger.info(f"   #{i+1}: {course}")
            
            return courses
        else:
            logger.error(f"❌ Errore calendario: {response.status_code}")
            logger.error(f"   Response: {response.text[:200]}")
            return []
            
    except Exception as e:
        logger.error(f"❌ Errore get_calendar_courses: {e}")
        return []


def book_course_easyfit(session, course_appointment_id, try_waitlist=True):
    """
    Prenota un corso su EasyFit
    
    MODIFICATO: Rimuove step di validazione che causa errore 500
    Prova direttamente la lista d'attesa se la prenotazione normale fallisce
    """
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
        
        # TENTATIVO 1: Prenotazione normale
        payload = {
            "courseAppointmentId": course_appointment_id,
            "expectedCustomerStatus": "BOOKED"
        }
        
        response = session.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"✅ PRENOTATO!")
            return True, "completed", response.json()
        
        # Log risposta prima di provare lista d'attesa
        logger.info(f"⚠️ Prenotazione normale fallita: {response.status_code}")
        logger.info(f"   Response: {response.text[:300]}")
        
        # TENTATIVO 2: Lista d'attesa (solo se try_waitlist=True)
        if try_waitlist:
            logger.info(f"⏳ Provo lista d'attesa...")
            
            # FIX: Prova direttamente senza validazione
            waitlist_payload = {
                "courseAppointmentId": course_appointment_id,
                "expectedCustomerStatus": "WAITING_LIST"
            }
            
            waitlist_response = session.post(url, json=waitlist_payload, headers=headers, timeout=10)
            
            if waitlist_response.status_code == 200:
                logger.info(f"✅ IN LISTA D'ATTESA!")
                return True, "waitlisted", waitlist_response.json()
            else:
                logger.warning(f"❌ Lista d'attesa fallita: {waitlist_response.status_code}")
                logger.warning(f"   Response: {waitlist_response.text[:300]}")
                
                # Analizza il tipo di errore
                try:
                    error_data = waitlist_response.json()
                    error_code = error_data[0].get('errorCode', '') if isinstance(error_data, list) else error_data.get('errorCode', '')
                    
                    if 'full' in error_code.lower() or 'piena' in error_code.lower():
                        return False, "full", None
                    else:
                        return False, "waitlist_unavailable", None
                except:
                    return False, "waitlist_unavailable", None
        
        # Se arrivi qui, entrambi i tentativi sono falliti
        return False, "full", None
            
    except Exception as e:
        logger.error(f"❌ Errore book_course_easyfit: {e}")
        return False, "error", None


def find_course_id(session, class_name, class_date, class_time):
    """Trova il courseAppointmentId per una lezione specifica"""
    try:
        logger.info(f"🔎 Cerco: {class_name} {class_date} {class_time}")
        
        # Converti date
        target_date = datetime.strptime(class_date, '%Y-%m-%d')
        start_date = target_date.strftime('%Y-%m-%d')
        end_date = (target_date + timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Recupera calendario
        courses = get_calendar_courses(session, start_date, end_date)
        
        if not courses:
            logger.warning(f"❌ Nessuna lezione nel calendario per {class_date}")
            return None
        
        # Cerca lezione matching
        for course in courses:
            course_name = course.get('name', '')
            
            # Controlla ogni slot del corso
            for slot in course.get('slots', []):
                start_datetime_str = slot.get('startDateTime', '')
                
                if start_datetime_str:
                    # Rimuovi timezone [Europe/Rome] se presente
                    start_datetime_str = start_datetime_str.split('[')[0]
                    
                    # Parse datetime
                    slot_datetime = parse_course_datetime(start_datetime_str)
                    
                    if slot_datetime:
                        # Estrai ora di INIZIO
                        course_time_str = slot_datetime.strftime('%H:%M')
                        
                        # Match nome E orario DI INIZIO
                        if class_name.lower() in course_name.lower() and course_time_str == class_time:
                            # Prendi l'ID del corso (non dello slot)
                            course_id = course.get('id')
                            logger.info(f"✅ Trovato ID: {course_id}")
                            logger.info(f"   Nome: {course_name}")
                            logger.info(f"   Orario INIZIO: {course_time_str}")
                            
                            # Info posti
                            booked = course.get('bookedParticipants', 0)
                            max_slots = course.get('maxParticipants', 0)
                            if max_slots:
                                available = max_slots - booked
                                logger.info(f"   Posti: {available}/{max_slots}")
                            
                            return course_id
        
        logger.warning(f"❌ Lezione non trovata: {class_name} {class_date} {class_time}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Errore find_course_id: {e}")
        return None


def cancel_booking_easyfit(session, easyfit_booking_id):
    """
    Cancella una prenotazione su EasyFit
    """
    try:
        logger.info(f"🗑️ Cancellazione prenotazione EasyFit ID: {easyfit_booking_id}")
        
        url = f"{EASYFIT_BASE_URL}/v1/aggregated/calendaritems/easyfit:{easyfit_booking_id}"
        
        headers = {
            "Accept": "*/*",
            "Accept-Language": "it-IT,it;q=0.9",
            "Content-Type": "application/json",
            "Origin": "https://app-easyfitpalestre.it",
            "Referer": "https://app-easyfitpalestre.it/studio/ZWFzeWZpdDoxMjE2OTE1Mzgw/calendar",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "x-tenant": "easyfit",
            "x-nox-client-type": "WEB",
            "x-nox-web-context": "v=1",
            "x-public-facility-group": "BRANDEDAPP-263FBF081EAB42E6A62602B2DDDE4506",
            "x-ms-web-context": "/studio/ZWFzeWZpdDoxMjE2OTE1Mzgw"
        }
        
        response = session.delete(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            logger.info("✅ Prenotazione cancellata su EasyFit!")
            return True
        else:
            logger.error(f"❌ Cancellazione fallita: {response.status_code}")
            logger.error(f"   Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Errore cancel_booking_easyfit: {e}")
        return False


# =============================================================================
# TELEGRAM BOT FUNCTIONS
# =============================================================================

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Ciao {user.first_name}!\n\n"
        f"Sono il bot per prenotare le lezioni EasyFit.\n\n"
        f"🤖 Cosa posso fare:\n"
        f"• Prenotare lezioni 72 ore prima automaticamente\n"
        f"• Gestire automaticamente la lista d'attesa\n"
        f"• Attivo dalle 8 alle 21 ogni giorno\n\n"
        f"📋 Comandi disponibili:\n"
        f"/prenota - Programma una nuova prenotazione\n"
        f"/lista - Vedi prenotazioni programmate\n"
        f"/cancella - Cancella prenotazione\n"
        f"/help - Guida completa"
    )


# Comando /prenota
async def prenota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra lezioni reali da calendario EasyFit
    """
    await update.message.reply_text("🔍 Recupero lezioni disponibili...\n⏳ Attendi qualche secondo...")
    
    try:
        # Login
        session = easyfit_login()
        if not session:
            await update.message.reply_text(
                "❌ Errore login EasyFit.\n"
                "Riprova tra qualche minuto."
            )
            return
        
        # Range: oggi + 7 giorni (UTC)
        from datetime import timezone
        today = datetime.now(timezone.utc)
        start_date = today.strftime('%Y-%m-%d')
        end_date = (today + timedelta(days=7)).strftime('%Y-%m-%d')
        
        # Recupera calendario
        courses = get_calendar_courses(session, start_date, end_date)
        
        if not courses:
            await update.message.reply_text(
                "❌ Nessuna lezione disponibile nei prossimi 7 giorni.\n"
                "Riprova più tardi."
            )
            return
        
        # Filtra solo lezioni future (usando slots con timezone corretto)
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        future_courses = []
        
        logger.info(f"🔍 Filtro lezioni. Ora UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
        
        for course in courses:
            course_has_future_slots = False
            
            for slot in course.get('slots', []):
                start_datetime_str = slot.get('startDateTime', '')
                
                if start_datetime_str:
                    start_datetime_str = start_datetime_str.split('[')[0]
                    slot_datetime = parse_course_datetime(start_datetime_str)
                    
                    if slot_datetime and slot_datetime > now_utc:
                        course_has_future_slots = True
                        break
            
            if course_has_future_slots:
                future_courses.append(course)
        
        logger.info(f"✅ Lezioni future: {len(future_courses)}/{len(courses)}")
        
        if not future_courses:
            now_ita = now_utc.astimezone(ROME_TZ)
            await update.message.reply_text(
                f"❌ Nessuna lezione futura disponibile.\n\n"
                f"⏰ Ora attuale: {now_ita.strftime('%d/%m/%Y %H:%M')} (ora italiana)\n\n"
                f"📅 Ho controllato {len(courses)} lezioni nei prossimi 7 giorni,\n"
                f"ma sono tutte già passate o in corso.\n\n"
                f"💡 Riprova tra qualche ora!"
            )
            return
        
        # Salva in context per dopo
        context.user_data['courses'] = future_courses
        
        # Raggruppa per nome corso
        courses_by_name = {}
        for course in future_courses:
            name = course.get('name', 'Sconosciuto')
            if name not in courses_by_name:
                courses_by_name[name] = []
            courses_by_name[name].append(course)
        
        # Crea mapping indice -> nome corso
        course_types = sorted(courses_by_name.keys())
        course_types_mapping = {i: name for i, name in enumerate(course_types)}
        
        # Salva mapping in context
        context.user_data['course_types'] = course_types_mapping
        
        # Crea bottoni con indici corti
        keyboard = []
        for index, course_name in course_types_mapping.items():
            button_text = f"📚 {course_name}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f'type_{index}')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📚 CALENDARIO REALE EASYFIT\n\n"
            f"✅ Trovate {len(future_courses)} lezioni nei prossimi 7 giorni\n\n"
            f"Quale lezione vuoi prenotare?",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"❌ Errore /prenota: {e}")
        await update.message.reply_text(
            "❌ Errore nel recuperare le lezioni.\n"
            "Riprova tra qualche minuto."
        )


# Callback lezione selezionata
async def class_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    type_index = int(query.data.split('_', 1)[1])
    
    course_types_mapping = context.user_data.get('course_types', {})
    class_name = course_types_mapping.get(type_index)
    
    if not class_name:
        await query.edit_message_text("❌ Errore: tipo di corso non trovato.")
        return
    
    context.user_data['class_name'] = class_name
    
    all_courses = context.user_data.get('courses', [])
    
    courses_by_date = {}
    for course in all_courses:
        if course.get('name') == class_name:
            for slot in course.get('slots', []):
                start_datetime_str = slot.get('startDateTime', '')
                if start_datetime_str:
                    start_datetime_str = start_datetime_str.split('[')[0]
                    date_key = start_datetime_str.split('T')[0]
                    
                    if date_key not in courses_by_date:
                        courses_by_date[date_key] = []
                    courses_by_date[date_key].append(slot)
    
    context.user_data['courses_by_date'] = courses_by_date
    context.user_data['course_name'] = class_name
    
    keyboard = []
    for date_key in sorted(courses_by_date.keys()):
        dt = datetime.strptime(date_key, '%Y-%m-%d')
        day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][dt.weekday()]
        date_str = dt.strftime('%d/%m')
        count = len(courses_by_date[date_key])
        
        button_text = f"{day_name} {date_str} ({count} orari)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f'date_{date_key}')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📚 Hai scelto: {class_name}\n\n"
        f"📅 Quale giorno?",
        reply_markup=reply_markup
    )


# Callback data selezionata
async def date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    date_str = query.data.split('_')[1]
    context.user_data['date'] = date_str
    
    courses_by_date = context.user_data.get('courses_by_date', {})
    date_slots = courses_by_date.get(date_str, [])
    
    context.user_data['date_slots'] = date_slots
    
    all_courses = context.user_data.get('courses', [])
    course_name = context.user_data.get('class_name', '')
    
    matching_courses = [c for c in all_courses if c.get('name') == course_name]
    
    keyboard = []
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    
    for slot in date_slots:
        start_datetime_str = slot.get('startDateTime', '')
        if start_datetime_str:
            start_datetime_str_clean = start_datetime_str.split('[')[0]
            time_str = start_datetime_str_clean.split('T')[1][:5]
            
            slot_datetime = parse_course_datetime(start_datetime_str_clean)
            
            hours_until = (slot_datetime - now_utc).total_seconds() / 3600 if slot_datetime else 0
            
            employees = slot.get('employees', [])
            instructor_name = ""
            if employees and len(employees) > 0:
                instructor = employees[0]
                displayed_name = instructor.get('displayedName', '')
                if displayed_name:
                    instructor_name = f" • {displayed_name}"
                else:
                    firstname = instructor.get('firstname', '')
                    lastname = instructor.get('lastname', '')
                    if firstname or lastname:
                        instructor_name = f" • {firstname} {lastname}".strip()
            
            course_for_slot = None
            for course in matching_courses:
                for course_slot in course.get('slots', []):
                    if course_slot.get('startDateTime') == start_datetime_str:
                        course_for_slot = course
                        break
                if course_for_slot:
                    break
            
            if course_for_slot:
                booked = course_for_slot.get('bookedParticipants', 0)
                max_part = course_for_slot.get('maxParticipants', 0)
                wait_active = course_for_slot.get('waitingListActive', False)
                wait_count = course_for_slot.get('waitingListParticipants', 0)
                max_wait = course_for_slot.get('maxWaitingListParticipants', 0)
                
                if hours_until > 72:
                    status = "🟢 Prenotabile"
                elif booked < max_part:
                    status = "✅ Posti liberi"
                elif wait_active and wait_count < max_wait:
                    status = "⏳ Lista d'attesa"
                else:
                    status = "🚫 Completa"
            else:
                status = "🟢 Prenotabile" if hours_until > 72 else "❓ Verifica app"
            
            button_text = f"🕐 {time_str}{instructor_name} ({status})"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f'time_{time_str}')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
    class_name = context.user_data.get('class_name', 'Lezione')
    
    await query.edit_message_text(
        f"📚 {class_name}\n"
        f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n\n"
        f"🕐 Che orario?",
        reply_markup=reply_markup
    )


# Callback orario selezionato e conferma
async def time_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    time_str = query.data.split('_')[1]
    context.user_data['time'] = time_str
    
    # L'orario selezionato è in ora italiana (come mostrato nel calendario)
    class_datetime_naive = datetime.strptime(
        f"{context.user_data['date']} {time_str}",
        '%Y-%m-%d %H:%M'
    )
    
    # Localizza in ora italiana (pytz gestisce automaticamente CET/CEST)
    class_datetime_rome = ROME_TZ.localize(class_datetime_naive)
    
    # Sottrai 72 ore mantenendo il timezone
    booking_datetime_rome = class_datetime_rome - timedelta(hours=72)
    
    # Converti in UTC per salvare nel database
    booking_datetime_utc = booking_datetime_rome.astimezone(pytz.utc)
    
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
                context.user_data['class_name'],
                context.user_data['date'],
                time_str,
                booking_datetime_utc,
                'pending'
            )
        )
        
        booking_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        release_db_connection(conn)
        
        date_obj = datetime.strptime(context.user_data['date'], '%Y-%m-%d')
        day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
        
        await query.edit_message_text(
            f"✅ PRENOTAZIONE PROGRAMMATA!\n\n"
            f"📚 Lezione: {context.user_data['class_name']}\n"
            f"📅 Data: {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
            f"🕐 Orario: {time_str}\n\n"
            f"⏰ Prenoterò automaticamente:\n"
            f"   {booking_datetime_rome.strftime('%d/%m/%Y alle %H:%M')} (ora italiana)\n"
            f"   (72 ore prima)\n\n"
            f"Usa /lista per verificare!\n\n"
            f"ID Prenotazione: #{booking_id}"
        )
        
    except Exception as e:
        logger.error(f"Errore salvataggio database: {e}")
        await query.edit_message_text(
            "❌ Errore nel salvare la prenotazione. Riprova."
        )


# Comando /lista
async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
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
        release_db_connection(conn)
        
        if not bookings:
            await update.message.reply_text(
                "📋 Non hai prenotazioni.\n\n"
                "Usa /prenota per programmarne una!"
            )
            return
        
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        
        future_bookings = []
        for booking in bookings:
            booking_id, class_name, class_date, class_time, booking_date, status = booking
            
            # Localizza l'orario della lezione in ora italiana e converti in UTC
            class_datetime_naive = datetime.strptime(f"{class_date} {class_time}", '%Y-%m-%d %H:%M')
            class_datetime_rome = ROME_TZ.localize(class_datetime_naive)
            class_datetime_utc = class_datetime_rome.astimezone(pytz.utc)
            
            if class_datetime_utc > now_utc:
                future_bookings.append(booking)
        
        if not future_bookings:
            await update.message.reply_text(
                "📋 Non hai prenotazioni future.\n\n"
                "Usa /prenota per programmarne una!"
            )
            return
        
        message = "📋 LE TUE PRENOTAZIONI:\n\n"
        
        pending = []
        completed = []
        waitlisted = []
        
        for booking in future_bookings:
            booking_id, class_name, class_date, class_time, booking_date, status = booking
            
            if status == 'pending':
                pending.append(booking)
            elif status == 'completed':
                completed.append(booking)
            elif status == 'waitlisted':
                waitlisted.append(booking)
        
        # Programmate
        if pending:
            message += "⏳ PROGRAMMATE:\n"
            for booking in pending:
                booking_id, class_name, class_date, class_time, booking_date, status = booking
                date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
                day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
                
                # Converti booking_date in ora italiana con pytz
                from datetime import timezone
                if booking_date.tzinfo is None:
                    booking_date = booking_date.replace(tzinfo=pytz.utc)
                booking_date_ita = booking_date.astimezone(ROME_TZ)
                
                message += f"#{booking_id} - {class_name}\n"
                message += f"   📅 {day_name} {date_obj.strftime('%d/%m/%Y')} ore {class_time}\n"
                message += f"   ⏰ Prenoterò: {booking_date_ita.strftime('%d/%m/%Y %H:%M')} (ora italiana)\n\n"
        
        # Completate
        if completed:
            message += "✅ PRENOTATE:\n"
            for booking in completed:
                booking_id, class_name, class_date, class_time, booking_date, status = booking
                date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
                day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
                
                message += f"#{booking_id} - {class_name}\n"
                message += f"   📅 {day_name} {date_obj.strftime('%d/%m/%Y')} ore {class_time}\n\n"
        
        # Lista d'attesa
        if waitlisted:
            message += "📋 LISTA D'ATTESA:\n"
            for booking in waitlisted:
                booking_id, class_name, class_date, class_time, booking_date, status = booking
                date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
                day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
                
                message += f"#{booking_id} - {class_name}\n"
                message += f"   📅 {day_name} {date_obj.strftime('%d/%m/%Y')} ore {class_time}\n\n"
        
        message += "💡 Usa /cancella <ID> per cancellare"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Errore recupero prenotazioni: {e}")
        await update.message.reply_text("❌ Errore nel recuperare le prenotazioni.")


# Comando /cancella
async def cancella(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text(
            "❌ Devi specificare l'ID della prenotazione.\n\n"
            "Esempio: /cancella 5\n\n"
            "Usa /lista per vedere gli ID."
        )
        return
    
    try:
        booking_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID non valido. Deve essere un numero.")
        return
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            "SELECT class_name, class_date, class_time, status, easyfit_booking_id FROM bookings WHERE id = %s AND user_id = %s",
            (booking_id, user_id)
        )
        
        result = cur.fetchone()
        
        if not result:
            await update.message.reply_text(f"❌ Prenotazione #{booking_id} non trovata.")
            cur.close()
            release_db_connection(conn)
            return
        
        class_name, class_date, class_time, status, easyfit_booking_id = result
        
        if status == 'pending':
            cur.execute("DELETE FROM bookings WHERE id = %s", (booking_id,))
            conn.commit()
            cur.close()
            release_db_connection(conn)
            
            await update.message.reply_text(
                f"✅ PRENOTAZIONE PROGRAMMATA CANCELLATA\n\n"
                f"#{booking_id} - {class_name}\n"
                f"📅 {class_date} ore {class_time}\n\n"
                f"ℹ️ La prenotazione non era ancora stata eseguita.\n"
                f"Rimossa solo dal database del bot."
            )
            return
        
        if status in ['completed', 'waitlisted']:
            if not easyfit_booking_id:
                await update.message.reply_text(
                    f"⚠️ CANCELLAZIONE PARZIALE\n\n"
                    f"#{booking_id} - {class_name}\n"
                    f"📅 {class_date} ore {class_time}\n\n"
                    f"❌ Non ho l'ID della prenotazione su EasyFit.\n"
                    f"Cancellata solo dal bot.\n\n"
                    f"⚠️ Devi cancellare manualmente dall'app EasyFit!"
                )
                
                cur.execute("DELETE FROM bookings WHERE id = %s", (booking_id,))
                conn.commit()
                cur.close()
                release_db_connection(conn)
                return
            
            await update.message.reply_text("🔄 Cancellazione in corso...\n⏳ Attendi...")
            
            session = easyfit_login()
            if not session:
                await update.message.reply_text(
                    f"❌ ERRORE LOGIN EASYFIT\n\n"
                    f"Non riesco a connettermi a EasyFit.\n\n"
                    f"💡 Prova:\n"
                    f"1. Cancella manualmente dall'app\n"
                    f"2. Riprova tra qualche minuto"
                )
                cur.close()
                release_db_connection(conn)
                return
            
            success = cancel_booking_easyfit(session, easyfit_booking_id)
            
            if success:
                cur.execute("DELETE FROM bookings WHERE id = %s", (booking_id,))
                conn.commit()
                
                await update.message.reply_text(
                    f"✅ PRENOTAZIONE CANCELLATA!\n\n"
                    f"#{booking_id} - {class_name}\n"
                    f"📅 {class_date} ore {class_time}\n\n"
                    f"✅ Cancellata su EasyFit\n"
                    f"✅ Rimossa dal bot\n\n"
                    f"Il posto è ora disponibile per altri!"
                )
            else:
                await update.message.reply_text(
                    f"⚠️ CANCELLAZIONE FALLITA\n\n"
                    f"#{booking_id} - {class_name}\n"
                    f"📅 {class_date} ore {class_time}\n\n"
                    f"❌ Errore nella cancellazione su EasyFit.\n\n"
                    f"💡 Prova a cancellare manualmente dall'app.\n"
                    f"La prenotazione rimane nel database del bot."
                )
            
            cur.close()
            release_db_connection(conn)
            return
        
        await update.message.reply_text(
            f"⚠️ Status prenotazione sconosciuto: {status}\n"
            f"Contatta l'amministratore."
        )
        cur.close()
        release_db_connection(conn)
        
    except Exception as e:
        logger.error(f"Errore cancellazione: {e}")
        await update.message.reply_text("❌ Errore nella cancellazione.")


# Comando /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 GUIDA EASYFIT BOT\n\n"
        "🤖 Cosa fa questo bot:\n"
        "Prenota automaticamente le tue lezioni EasyFit "
        "esattamente 72 ore prima dell'inizio.\n\n"
        "📋 COMANDI:\n\n"
        "/prenota - Programma una nuova prenotazione\n"
        "   Mostra calendario REALE EasyFit.\n"
        "   Scegli lezione, giorno e orario.\n"
        "   Il bot prenoterà automaticamente 72h prima.\n\n"
        "/lista - Vedi tutte le prenotazioni\n"
        "   Mostra cosa hai in programma.\n\n"
        "/cancella <ID> - Cancella una prenotazione\n"
        "   Esempio: /cancella 5\n"
        "   ⚠️ Se già prenotata, cancella anche su EasyFit!\n\n"
        "⏰ ORARI:\n"
        "Il bot è attivo dalle 8:00 alle 21:00 ogni giorno.\n"
        "Controlla ogni minuto se ci sono prenotazioni da fare.\n\n"
        "📋 LISTA D'ATTESA:\n"
        "Se una lezione è piena, il bot proverà automaticamente\n"
        "ad inserirti in lista d'attesa!\n\n"
        "❓ Problemi? Usa /lista per controllare lo stato."
    )


# =============================================================================
# SCHEDULER FUNCTION
# =============================================================================

def check_and_book(application):
    """
    Controlla e prenota lezioni
    Esegue nello scheduler ogni minuto (8-21)
    """
    
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour
    
    if not (8 <= current_hour < 21):
        return
    
    logger.info(f"🔍 CONTROLLO PRENOTAZIONI")
    logger.info(f"⏰ Ora UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            SELECT id, user_id, class_name, class_date, class_time, booking_date
            FROM bookings
            WHERE status = 'pending'
            AND booking_date <= %s
            ORDER BY booking_date ASC
            """,
            (now_utc,)
        )
        
        bookings_to_make = cur.fetchall()
        
        logger.info(f"📋 Trovate {len(bookings_to_make)} prenotazioni da processare")
        
        if not bookings_to_make:
            release_db_connection(conn)
            return
        
        session = easyfit_login()
        if not session:
            logger.error("❌ Login fallito - salto controllo")
            cur.close()
            release_db_connection(conn)
            return
        
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time, booking_date = booking
            
            logger.info(f"📝 PRENOTAZIONE #{booking_id}")
            logger.info(f"   📚 {class_name}")
            logger.info(f"   📅 {class_date} ore {class_time}")
            
            delay = (now_utc - booking_date.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if delay > 5:
                logger.warning(f"   ⚠️ In ritardo di {int(delay)} minuti")
            
            try:
                course_appointment_id = find_course_id(session, class_name, str(class_date), class_time)
                
                if not course_appointment_id:
                    cur.execute(
                        "UPDATE bookings SET status = 'completed' WHERE id = %s",
                        (booking_id,)
                    )
                    conn.commit()
                    
                    logger.warning(f"⚠️ Prenotazione #{booking_id} - Lezione non trovata")
                    continue
                
                success, status, response = book_course_easyfit(session, course_appointment_id)
                
                if success:
                    easyfit_booking_id = None
                    if response and isinstance(response, dict):
                        easyfit_booking_id = response.get('id')
                    
                    cur.execute(
                        "UPDATE bookings SET status = %s, easyfit_booking_id = %s WHERE id = %s",
                        (status, easyfit_booking_id, booking_id)
                    )
                    conn.commit()
                    
                    logger.info(f"💾 Salvato easyfit_booking_id: {easyfit_booking_id}")
                    logger.info(f"🎉 Prenotazione #{booking_id} completata - Status: {status}")
                else:
                    logger.error(f"❌ Prenotazione #{booking_id} fallita - Status: {status}")
                    
                    cur.execute(
                        "UPDATE bookings SET status = 'completed' WHERE id = %s",
                        (booking_id,)
                    )
                    conn.commit()
            
            except Exception as booking_error:
                logger.error(f"❌ Errore processamento prenotazione #{booking_id}: {booking_error}")
                continue
        
        cur.close()
        release_db_connection(conn)
        
    except psycopg2.OperationalError as db_error:
        logger.error(f"❌ Errore connessione DB: {db_error}")
        logger.info("⏭️ Salto questo controllo, riproverò al prossimo minuto")
        
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
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'OK')
    
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.send_header('Content-Length', '2')
        self.end_headers()
    
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
    
    from datetime import timezone
    startup_time = datetime.now(timezone.utc)
    startup_time_ita = startup_time.astimezone(ROME_TZ)
    
    logger.info("=" * 60)
    logger.info("🚀 AVVIO EASYFIT BOT")
    logger.info(f"⏰ Ora UTC: {startup_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"⏰ Ora ITA: {startup_time_ita.strftime('%Y-%m-%d %H:%M:%S')} ({startup_time_ita.tzname()})")
    logger.info("=" * 60)
    
    # Inizializza connection pool
    init_db_pool()
    
    # Crea applicazione
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Registra handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("prenota", prenota))
    application.add_handler(CommandHandler("lista", lista))
    application.add_handler(CommandHandler("cancella", cancella))
    application.add_handler(CommandHandler("help", help_command))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(class_selected, pattern="^type_"))
    application.add_handler(CallbackQueryHandler(date_selected, pattern="^date_"))
    application.add_handler(CallbackQueryHandler(time_selected, pattern="^time_"))
    
    # Avvia health server in thread separato
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Avvia scheduler
    scheduler = BackgroundScheduler()
    
    scheduler.add_job(
        lambda: check_and_book(application),
        'cron',
        hour='8-21',
        minute='*',
        id='check_bookings'
    )
    
    scheduler.add_job(
        keep_alive_ping,
        'cron',
        minute='*/10',
        id='keep_alive'
    )
    
    scheduler.start()
    
    logger.info("=" * 60)
    logger.info("✅ BOT PRONTO E OPERATIVO!")
    logger.info("⏰ Attivo 8-21 UTC per prenotazioni")
    logger.info("💓 Keep-alive attivo 24/7")
    logger.info("📱 Comandi disponibili su Telegram")
    logger.info("=" * 60)
    
    import signal
    import sys
    
    def shutdown_handler(signum, frame):
        logger.warning("=" * 60)
        logger.warning("⚠️ SHUTDOWN RICHIESTO")
        logger.warning(f"Signal: {signum}")
        from datetime import timezone
        logger.warning(f"⏰ Ora UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
        logger.warning("=" * 60)
        scheduler.shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        logger.warning("⚠️ Bot fermato da utente")
    except Exception as e:
        logger.error(f"❌ Errore critico: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        logger.warning("👋 Bot terminato")

if __name__ == '__main__':
    main()
