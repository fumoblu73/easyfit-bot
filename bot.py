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
            course_start = course.get('start', '')
            
            # Parse time da ISO
            if course_start:
                course_datetime = datetime.fromisoformat(course_start.replace('Z', '+00:00'))
                course_time_str = course_datetime.strftime('%H:%M')
                
                # Match nome E orario
                if class_name.lower() in course_name.lower() and course_time_str == class_time:
                    course_id = course.get('courseAppointmentId')
                    logger.info(f"✅ Trovato ID: {course_id}")
                    logger.info(f"   Nome: {course_name}")
                    logger.info(f"   Orario: {course_time_str}")
                    logger.info(f"   Posti: {course.get('availableSlots', 'N/A')}/{course.get('maxSlots', 'N/A')}")
                    return course_id
        
        logger.warning(f"❌ Lezione non trovata: {class_name} {class_date} {class_time}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Errore find_course_id: {e}")
        return None


# =============================================================================
# TELEGRAM BOT FUNCTIONS
# =============================================================================

def send_notification_from_thread(bot, user_id, message):
    """
    Invia notifica Telegram da thread scheduler
    Usa asyncio.run_coroutine_threadsafe per evitare errori
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    async def send():
        await bot.send_message(chat_id=user_id, text=message)
    
    try:
        future = asyncio.run_coroutine_threadsafe(send(), loop)
        future.result(timeout=10)
        logger.info(f"📲 Notifica inviata a {user_id}")
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
        
        # Range: oggi + 7 giorni
        today = datetime.now()
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
        
        # Filtra solo lezioni future
        now = datetime.now()
        future_courses = []
        
        for course in courses:
            start_time = course.get('start')
            if start_time:
                course_datetime = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                # Converti a naive per confronto
                course_datetime_naive = course_datetime.replace(tzinfo=None)
                
                if course_datetime_naive > now:
                    future_courses.append(course)
        
        if not future_courses:
            await update.message.reply_text(
                "❌ Nessuna lezione futura disponibile.\n"
                "Tutte le lezioni sono già passate."
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
    
    # Filtra lezioni per questo nome
    all_courses = context.user_data.get('courses', [])
    class_courses = [c for c in all_courses if c.get('name') == class_name]
    
    # Raggruppa per data
    courses_by_date = {}
    for course in class_courses:
        start = course.get('start')
        if start:
            dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            date_key = dt.strftime('%Y-%m-%d')
            
            if date_key not in courses_by_date:
                courses_by_date[date_key] = []
            courses_by_date[date_key].append(course)
    
    # Salva per dopo
    context.user_data['courses_by_date'] = courses_by_date
    
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
    
    # Filtra lezioni per questa data
    courses_by_date = context.user_data.get('courses_by_date', {})
    date_courses = courses_by_date.get(date_str, [])
    
    # Salva per dopo
    context.user_data['date_courses'] = date_courses
    
    # Crea bottoni per orari
    keyboard = []
    for course in date_courses:
        start = course.get('start')
        if start:
            dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            time_str = dt.strftime('%H:%M')
            
            available = course.get('availableSlots', 0)
            max_slots = course.get('maxSlots', 0)
            
            if available > 0:
                status = f"✅ {available}/{max_slots}"
            else:
                status = "⏳ Piena"
            
            button_text = f"🕐 {time_str} ({status})"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f'time_{time_str}')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
    
    await query.edit_message_text(
        f"📚 {context.user_data['class_name']}\n"
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
        conn.close()
        
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
        conn.close()
        
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
            conn.close()
            return
        
        class_name, class_date, class_time = result
        
        # Cancella
        cur.execute(
            "DELETE FROM bookings WHERE id = %s",
            (booking_id,)
        )
        conn.commit()
        cur.close()
        conn.close()
        
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
            cur.close()
            conn.close()
            return
        
        # LOGIN UNA VOLTA SOLA
        session = easyfit_login()
        if not session:
            logger.error("❌ Login fallito - salto controllo")
            cur.close()
            conn.close()
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
            
            # TROVA ID LEZIONE
            course_appointment_id = find_course_id(session, class_name, str(class_date), class_time)
            
            if not course_appointment_id:
                # Segna come completata (non trovata)
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
