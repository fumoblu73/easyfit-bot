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
import time

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
    """
    Trova il courseAppointmentId per una lezione specifica
    MODIFICATO: Cerca dentro gli slots, non in 'start' che non esiste
    """
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
            course_id = course.get('id')  # ID del corso base
            
            # L'API restituisce gli orari dentro 'slots'
            slots = course.get('slots', [])
            
            for slot in slots:
                start_datetime_str = slot.get('startDateTime', '')
                
                if not start_datetime_str:
                    continue
                
                # Rimuovi timezone [Europe/Rome] se presente
                start_datetime_str = start_datetime_str.split('[')[0]
                
                # Parse datetime
                slot_datetime = parse_course_datetime(start_datetime_str)
                
                if not slot_datetime:
                    continue
                
                # Estrai ora locale (già in +01:00)
                slot_time_str = slot_datetime.strftime('%H:%M')
                
                # Match nome E orario
                name_match = class_name.lower() in course_name.lower()
                time_match = slot_time_str == class_time
                
                if name_match and time_match:
                    logger.info(f"✅ Trovato corso!")
                    logger.info(f"   ID: {course_id}")
                    logger.info(f"   Nome: {course_name}")
                    logger.info(f"   Orario: {slot_time_str}")
                    logger.info(f"   Prenotabile: {slot.get('bookable', 'N/A')}")
                    logger.info(f"   Già prenotato: {slot.get('alreadyBooked', 'N/A')}")
                    return course_id
        
        logger.warning(f"❌ Lezione non trovata: {class_name} {class_date} {class_time}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Errore find_course_id: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


# =============================================================================
# TELEGRAM BOT FUNCTIONS
# =============================================================================

def send_notification_sync(bot, user_id, message):
    """
    Invia notifica Telegram in modo sincrono da scheduler
    Usa un thread separato per evitare interferenze con il loop principale
    """
    import threading
    
    result = {'success': False, 'error': None}
    
    def run_in_thread():
        """Esegue la notifica in un thread con loop dedicato"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def send():
            await bot.send_message(chat_id=user_id, text=message)
        
        try:
            loop.run_until_complete(send())
            result['success'] = True
            logger.info(f"📲 Notifica inviata a {user_id}")
        except Exception as e:
            result['error'] = e
            logger.error(f"❌ Errore send_message: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            loop.close()
    
    try:
        # Esegui in thread separato
        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        thread.join(timeout=10)
        
        if not result['success'] and result['error']:
            raise result['error']
    except Exception as e:
        logger.error(f"❌ Errore invio notifica: {e}")


# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Ciao {user.first_name}!\n\n"
        f"Sono il bot per prenotare le lezioni EasyFit.\n\n"
        f"🤖 Cosa posso fare:\n"
        f"• Prenotare lezioni 72 ore prima automaticamente\n"
        f"• Gestire automaticamente la lista d'attesa\n"
        f"• Attivo dalle 8 alle 21 ogni giorno\n"
        f"• Notifiche quando prenoto\n\n"
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
            
            # L'API restituisce un array 'slots' per ogni corso
            for slot in course.get('slots', []):
                start_datetime_str = slot.get('startDateTime', '')
                
                if start_datetime_str:
                    # Rimuovi timezone [Europe/Rome] se presente
                    start_datetime_str = start_datetime_str.split('[')[0]
                    
                    # Parse datetime
                    slot_datetime = parse_course_datetime(start_datetime_str)
                    
                    if slot_datetime and slot_datetime > now_utc:
                        course_has_future_slots = True
                        break  # Basta uno slot futuro
            
            if course_has_future_slots:
                future_courses.append(course)
        
        logger.info(f"✅ Lezioni future: {len(future_courses)}/{len(courses)}")
        
        if not future_courses:
            now_ita = now_utc + timedelta(hours=1)  # UTC+1 per ora italiana
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
        
        # Crea bottoni per nomi corsi
        keyboard = []
        for course_name in sorted(courses_by_name.keys()):
            count = len(courses_by_name[course_name])
            button_text = f"📚 {course_name} ({count} disponibili)"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f'class_{course_name}')])
        
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
    
    class_name = query.data.split('_', 1)[1]
    context.user_data['class_name'] = class_name
    
    # Filtra lezioni per questo nome e raggruppa per data
    all_courses = context.user_data.get('courses', [])
    
    courses_by_date = {}
    for course in all_courses:
        if course.get('name') == class_name:
            for slot in course.get('slots', []):
                start_datetime_str = slot.get('startDateTime', '')
                if start_datetime_str:
                    # Rimuovi timezone [Europe/Rome]
                    start_datetime_str = start_datetime_str.split('[')[0]
                    date_key = start_datetime_str.split('T')[0]
                    
                    if date_key not in courses_by_date:
                        courses_by_date[date_key] = []
                    courses_by_date[date_key].append(slot)
    
    # Salva per dopo
    context.user_data['courses_by_date'] = courses_by_date
    context.user_data['course_name'] = class_name
    
    # Crea bottoni per date
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
    
    # Filtra slot per questa data
    courses_by_date = context.user_data.get('courses_by_date', {})
    date_slots = courses_by_date.get(date_str, [])
    
    # Salva per dopo
    context.user_data['date_slots'] = date_slots
    
    # Crea bottoni per orari
    keyboard = []
    for slot in date_slots:
        start_datetime_str = slot.get('startDateTime', '')
        if start_datetime_str:
            # Rimuovi timezone
            start_datetime_str = start_datetime_str.split('[')[0]
            time_str = start_datetime_str.split('T')[1][:5]  # HH:MM
            
            available = slot.get('availableSlots', 0)
            max_slots = slot.get('maxSlots', 0)
            
            if available > 0:
                status = f"✅ {available}/{max_slots}"
            else:
                status = "⏳ Piena"
            
            button_text = f"🕐 {time_str} ({status})"
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
    
    # Calcola quando prenotare (72h prima)
    class_datetime = datetime.strptime(
        f"{context.user_data['date']} {time_str}", 
        '%Y-%m-%d %H:%M'
    )
    
    # Timezone-aware UTC
    from datetime import timezone
    class_datetime_utc = class_datetime.replace(tzinfo=timezone.utc)
    booking_datetime = class_datetime_utc - timedelta(hours=72)
    
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
                booking_datetime,
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
            f"   {booking_datetime.strftime('%d/%m/%Y alle %H:%M')} UTC\n"
            f"   (72 ore prima)\n\n"
            f"📲 Ti avviserò quando prenoto!\n\n"
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
        
        message = "📋 LE TUE PRENOTAZIONI:\n\n"
        
        pending = []
        completed = []
        waitlisted = []
        
        for booking in bookings:
            booking_id, class_name, class_date, class_time, booking_date, status = booking
            
            if status == 'pending':
                pending.append(booking)
            elif status == 'completed':
                completed.append(booking)
            elif status == 'waitlisted':
                waitlisted.append(booking)
        
        # Programmata
        if pending:
            message += "⏳ PROGRAMMATE:\n"
            for booking in pending:
                booking_id, class_name, class_date, class_time, booking_date, status = booking
                date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
                day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
                
                message += f"#{booking_id} - {class_name}\n"
                message += f"   📅 {day_name} {date_obj.strftime('%d/%m/%Y')} ore {class_time}\n"
                message += f"   ⏰ Prenoterò: {booking_date.strftime('%d/%m/%Y %H:%M')}\n\n"
        
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
        
        # Verifica che la prenotazione esista e appartenga all'utente
        cur.execute(
            "SELECT class_name, class_date, class_time FROM bookings WHERE id = %s AND user_id = %s",
            (booking_id, user_id)
        )
        
        result = cur.fetchone()
        
        if not result:
            await update.message.reply_text(f"❌ Prenotazione #{booking_id} non trovata.")
            cur.close()
            release_db_connection(conn)
            return
        
        class_name, class_date, class_time = result
        
        # Cancella
        cur.execute(
            "DELETE FROM bookings WHERE id = %s",
            (booking_id,)
        )
        conn.commit()
        cur.close()
        release_db_connection(conn)
        
        await update.message.reply_text(
            f"✅ PRENOTAZIONE CANCELLATA\n\n"
            f"#{booking_id} - {class_name}\n"
            f"📅 {class_date} ore {class_time}"
        )
        
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
        "   Esempio: /cancella 5\n\n"
        "⏰ ORARI:\n"
        "Il bot è attivo dalle 8:00 alle 21:00 ogni giorno.\n"
        "Controlla ogni minuto se ci sono prenotazioni da fare.\n\n"
        "📋 LISTA D'ATTESA:\n"
        "Se una lezione è piena, il bot proverà automaticamente\n"
        "ad inserirti in lista d'attesa!\n\n"
        "📲 NOTIFICHE:\n"
        "Riceverai un messaggio quando il bot prenota per te!\n\n"
        "❓ Problemi? Controlla i logs su Render."
    )


# =============================================================================
# SCHEDULER FUNCTION
# =============================================================================

def check_and_book(application):
    """
    Controlla e prenota lezioni
    Esegue nello scheduler ogni minuto (8-21)
    """
    
    # Controlla se siamo nell'orario attivo (8-21 UTC)
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour
    
    if not (8 <= current_hour < 21):
        return
    
    logger.info(f"🔍 CONTROLLO PRENOTAZIONI")
    logger.info(f"⏰ Ora UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # Connessione con retry
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Trova tutte le prenotazioni pending scadute
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
        
        # LOGIN UNA VOLTA SOLA
        session = easyfit_login()
        if not session:
            logger.error("❌ Login fallito - salto controllo")
            cur.close()
            release_db_connection(conn)
            return
        
        # Processa ogni prenotazione
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time, booking_date = booking
            
            logger.info(f"📝 PRENOTAZIONE #{booking_id}")
            logger.info(f"   📚 {class_name}")
            logger.info(f"   📅 {class_date} ore {class_time}")
            
            # Calcola ritardo
            delay = (now_utc - booking_date.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if delay > 5:
                logger.warning(f"   ⚠️ In ritardo di {int(delay)} minuti")
            
            try:
                # TROVA ID LEZIONE
                course_appointment_id = find_course_id(session, class_name, str(class_date), class_time)
                
                if not course_appointment_id:
                    # Segna come completata (non trovata)
                    cur.execute(
                        "UPDATE bookings SET status = 'completed' WHERE id = %s",
                        (booking_id,)
                    )
                    conn.commit()
                    
                    # Notifica utente
                    message = (
                        f"❌ PRENOTAZIONE NON POSSIBILE\n\n"
                        f"📚 {class_name}\n"
                        f"📅 {class_date}\n"
                        f"🕐 {class_time}\n\n"
                        f"La lezione non è stata trovata nel calendario.\n"
                        f"Potrebbe essere stata cancellata."
                    )
                    
                    send_notification_sync(
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
                    
                    # Notifica utente
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
                    
                    send_notification_sync(
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
                    
                    send_notification_sync(
                        application.bot,
                        user_id,
                        message
                    )
            
            except Exception as booking_error:
                # Errore specifico per questa prenotazione
                logger.error(f"❌ Errore processamento prenotazione #{booking_id}: {booking_error}")
                
                # Notifica utente dell'errore
                try:
                    message = (
                        f"⚠️ ERRORE TECNICO\n\n"
                        f"📚 {class_name}\n"
                        f"📅 {class_date}\n"
                        f"🕐 {class_time}\n\n"
                        f"Si è verificato un errore tecnico.\n"
                        f"Riproverò al prossimo controllo.\n\n"
                        f"Se il problema persiste, prenota manualmente."
                    )
                    
                    send_notification_sync(
                        application.bot,
                        user_id,
                        message
                    )
                except:
                    pass
                
                # Continua con le altre prenotazioni
                continue
        
        cur.close()
        release_db_connection(conn)
        
    except psycopg2.OperationalError as db_error:
        # Errore connessione database - non bloccare lo scheduler
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
# MAIN CON AUTO-RESTART
# =============================================================================

def main():
    """Avvia il bot con sistema di auto-restart"""
    
    from datetime import timezone
    startup_time = datetime.now(timezone.utc)
    
    logger.info("=" * 60)
    logger.info("🚀 AVVIO EASYFIT BOT v2.1 FINAL")
    logger.info(f"⏰ Ora UTC: {startup_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"⏰ Ora ITA: {(startup_time + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')}")
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
    
    logger.info("=" * 60)
    logger.info("✅ BOT PRONTO E OPERATIVO!")
    logger.info("⏰ Attivo 8-21 UTC per prenotazioni")
    logger.info("💓 Keep-alive attivo 24/7")
    logger.info("🔄 Sistema auto-restart abilitato")
    logger.info("📱 Comandi disponibili su Telegram")
    logger.info("=" * 60)
    
    # Handler per shutdown
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
    
    # =========================================================================
    # LOOP CON AUTO-RESTART
    # =========================================================================
    max_retries = 5
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            logger.info(f"🚀 Avvio polling (tentativo {retry_count + 1}/{max_retries})...")
            application.run_polling(allowed_updates=Update.ALL_TYPES)
            break  # Uscita pulita
            
        except KeyboardInterrupt:
            logger.warning("⚠️ Bot fermato da utente")
            break
            
        except Exception as e:
            retry_count += 1
            logger.error(f"❌ Errore critico (tentativo {retry_count}/{max_retries}): {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            if retry_count < max_retries:
                wait_time = min(60 * retry_count, 300)  # Max 5 minuti
                logger.warning(f"⏳ Riavvio tra {wait_time} secondi...")
                time.sleep(wait_time)
                logger.info("🔄 Riavvio bot...")
            else:
                logger.error("❌ Troppi errori consecutivi, termino.")
    
    logger.warning("👋 Bot terminato")

if __name__ == '__main__':
    main()
