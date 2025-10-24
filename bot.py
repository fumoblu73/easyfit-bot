import os
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import base64

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
STUDIO_ID = "ZWFzeWZpdDoxMjE2OTE1Mzgw"
ORGANIZATION_UNIT_ID = "1216915380"
TENANT = "easyfit"
FACILITY_GROUP = "BRANDEDAPP-263FBF081EAB42E6A62602B2DDDE4506"

COMMON_HEADERS = {
    "x-tenant": TENANT,
    "x-ms-web-context": f"/studio/{STUDIO_ID}",
    "x-nox-client-type": "WEB",
    "x-nox-web-context": "v=1",
    "x-public-facility-group": FACILITY_GROUP,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "it"
}

# Connessione database
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# =============================================================================
# EASYFIT API FUNCTIONS
# =============================================================================

def easyfit_login():
    """Effettua login su EasyFit e restituisce la session"""
    try:
        logger.info(f"🔐 Login EasyFit...")
        
        credentials = f"{EASYFIT_EMAIL}:{EASYFIT_PASSWORD}"
        basic_auth = base64.b64encode(credentials.encode()).decode()
        
        url = f"{EASYFIT_BASE_URL}/login"
        
        headers = {
            **COMMON_HEADERS,
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "username": EASYFIT_EMAIL,
            "password": EASYFIT_PASSWORD
        }
        
        session = requests.Session()
        response = session.post(url, json=payload, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            session_id = data.get('sessionId')
            logger.info(f"✅ Login effettuato! SessionID: {session_id[:20]}...")
            return session, session_id
        else:
            logger.error(f"❌ Login fallito: {response.status_code}")
            return None, None
            
    except Exception as e:
        logger.error(f"❌ Errore login: {e}")
        return None, None


def get_session_headers(session_id):
    """Ritorna headers con session cookie"""
    return {
        **COMMON_HEADERS,
        "Content-Type": "application/json",
        "Cookie": f"SESSION={session_id}"
    }


def find_course_id(session, session_id, class_name, class_date, class_time):
    """Trova l'ID del corso specifico"""
    try:
        logger.info(f"🔍 Ricerca corso: {class_name} del {class_date} ore {class_time}")
        
        # Calcola range date
        date_obj = datetime.strptime(class_date, "%Y-%m-%d")
        start_date = (date_obj - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = (date_obj + timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Ottieni calendario
        url = f"{EASYFIT_BASE_URL}/nox/v2/bookableitems/courses/with-canceled"
        params = {
            "startDate": start_date,
            "endDate": end_date,
            "employeeIds": "",
            "organizationUnitIds": ORGANIZATION_UNIT_ID
        }
        
        response = session.get(url, params=params, headers=get_session_headers(session_id))
        
        if response.status_code != 200:
            logger.error(f"❌ Errore recupero calendario: {response.status_code}")
            return None
        
        courses = response.json()
        target_datetime_str = f"{class_date}T{class_time}"
        
        # Cerca il corso
        for course in courses:
            if course['name'].lower() == class_name.lower():
                for slot in course.get('slots', []):
                    if target_datetime_str in slot['startDateTime']:
                        course_id = course['id']
                        logger.info(f"✅ Corso trovato! ID: {course_id}")
                        return course_id
        
        logger.warning(f"⚠️  Corso non trovato")
        return None
        
    except Exception as e:
        logger.error(f"❌ Errore find_course_id: {e}")
        return None


def check_bookability(session, session_id, course_id):
    """Verifica se un corso è prenotabile"""
    try:
        url = f"{EASYFIT_BASE_URL}/nox/v1/bookableitems/course/{course_id}"
        response = session.get(url, headers=get_session_headers(session_id))
        
        if response.status_code != 200:
            return False, "Errore recupero dettagli"
        
        details = response.json()
        slots = details.get('slots', [])
        
        if not slots:
            return False, "Nessuno slot disponibile"
        
        slot = slots[0]
        
        if not slot.get('bookable', False):
            return False, "Non prenotabile"
        
        if slot.get('alreadyBooked', False):
            return False, "Già prenotata"
        
        booked = details.get('bookedParticipants', 0)
        max_p = details.get('maxParticipants', 0)
        
        if max_p > 0 and booked >= max_p:
            return False, f"Posti esauriti ({booked}/{max_p})"
        
        return True, f"OK ({booked}/{max_p})"
        
    except Exception as e:
        logger.error(f"❌ Errore check_bookability: {e}")
        return False, str(e)


def book_course_real(session, session_id, course_id):
    """Prenota effettivamente il corso"""
    try:
        logger.info(f"🎫 Prenotazione corso ID: {course_id}...")
        
        url = f"{EASYFIT_BASE_URL}/nox/v1/calendar/bookcourse"
        payload = {
            "courseAppointmentId": course_id,
            "expectedCustomerStatus": "BOOKED"
        }
        
        response = session.post(url, json=payload, headers=get_session_headers(session_id))
        
        if response.status_code == 200:
            data = response.json()
            if data.get('participantStatus') == 'BOOKED':
                logger.info(f"✅ PRENOTAZIONE EFFETTUATA: {data.get('name')}")
                return True
        
        logger.error(f"❌ Prenotazione fallita: {response.status_code}")
        return False
        
    except Exception as e:
        logger.error(f"❌ Errore book_course_real: {e}")
        return False


def book_class_easyfit(class_name, class_date, class_time):
    """
    FUNZIONE PRINCIPALE: Prenota una lezione su EasyFit
    Sostituisce simulate_booking()
    """
    logger.info("="*60)
    logger.info(f"🚀 PRENOTAZIONE REALE EASYFIT")
    logger.info(f"   Lezione: {class_name}")
    logger.info(f"   Data: {class_date}")
    logger.info(f"   Ora: {class_time}")
    logger.info("="*60)
    
    try:
        # 1. Login
        session, session_id = easyfit_login()
        if not session or not session_id:
            logger.error("❌ Login fallito")
            return False
        
        # 2. Trova corso
        course_id = find_course_id(session, session_id, class_name, class_date, class_time)
        if not course_id:
            logger.error(f"❌ Corso non trovato")
            return False
        
        # 3. Verifica prenotabilità
        bookable, reason = check_bookability(session, session_id, course_id)
        logger.info(f"📊 Stato: {reason}")
        
        if not bookable:
            logger.error(f"❌ Non prenotabile: {reason}")
            return False
        
        # 4. Prenota!
        success = book_course_real(session, session_id, course_id)
        
        if success:
            logger.info("="*60)
            logger.info("🎉 PRENOTAZIONE COMPLETATA!")
            logger.info("="*60)
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Errore generale: {e}")
        return False

# =============================================================================
# TELEGRAM BOT COMMANDS
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
    await update.message.reply_text(
        "🔍 Recupero calendario lezioni da EasyFit...\n"
        "⏳ Attendi qualche secondo..."
    )
    
    try:
        logger.info(f"📱 Utente {update.effective_user.id} ha richiesto /prenota")
        
        # Login e recupero calendario reale
        logger.info("🔐 Tentativo login EasyFit...")
        session, session_id = easyfit_login()
        
        if not session or not session_id:
            logger.error("❌ Login fallito - session o session_id mancante")
            await update.message.reply_text(
                "❌ Errore di connessione a EasyFit.\n"
                "Riprova tra qualche minuto."
            )
            return
        
        logger.info(f"✅ Login riuscito - SessionID: {session_id[:20]}...")
        
        # Ottieni calendario prossimi 7 giorni
        today = datetime.now()
        start_date = today.strftime("%Y-%m-%d")
        end_date = (today + timedelta(days=7)).strftime("%Y-%m-%d")
        
        logger.info(f"📅 Richiesta calendario: {start_date} - {end_date}")
        
        url = f"{EASYFIT_BASE_URL}/nox/v2/bookableitems/courses/with-canceled"
        params = {
            "startDate": start_date,
            "endDate": end_date,
            "employeeIds": "",
            "organizationUnitIds": ORGANIZATION_UNIT_ID,
            "includeWaitingList": "true"
        }
        
        # Retry logic: prova fino a 3 volte
        max_retries = 3
        response = None
        
        for attempt in range(max_retries):
            try:
                logger.info(f"🌐 Chiamata GET (tentativo {attempt + 1}/{max_retries}): {url}")
                response = session.get(
                    url, 
                    params=params, 
                    headers=get_session_headers(session_id),
                    timeout=10
                )
                logger.info(f"📡 Response status: {response.status_code}")
                
                if response.status_code == 200:
                    break  # Successo!
                    
                logger.warning(f"⚠️ Tentativo {attempt + 1} fallito con status {response.status_code}")
                
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2)  # Aspetta 2 secondi prima di riprovare
                    
            except Exception as e:
                logger.error(f"❌ Errore richiesta (tentativo {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2)
        
        if not response or response.status_code != 200:
            error_msg = response.text[:200] if response else "Nessuna risposta"
            logger.error(f"❌ Errore API dopo {max_retries} tentativi: {error_msg}")
            await update.message.reply_text(
                "❌ Errore nel recuperare il calendario da EasyFit.\n"
                f"Il server ha risposto con errore dopo {max_retries} tentativi.\n\n"
                "Possibili cause:\n"
                "• Il server EasyFit è temporaneamente non disponibile\n"
                "• Manutenzione in corso\n\n"
                "💡 Riprova tra qualche minuto o verifica che l'app web funzioni."
            )
            return
        
        courses = response.json()
        logger.info(f"📊 Ricevuti {len(courses)} corsi dal calendario")
        
        # Estrai nomi corsi unici
        course_names = set()
        for course in courses:
            course_names.add(course['name'])
        
        logger.info(f"📚 Trovati {len(course_names)} tipi di lezioni: {sorted(course_names)}")
        
        if not course_names:
            logger.warning("⚠️ Nessuna lezione trovata nel calendario")
            await update.message.reply_text(
                "ℹ️ Nessuna lezione disponibile nei prossimi 7 giorni."
            )
            return
        
        # Salva calendario nel context per uso successivo
        context.user_data['calendar'] = courses
        context.user_data['session_id'] = session_id
        
        # Crea bottoni con nomi corsi reali
        keyboard = []
        for course_name in sorted(course_names):
            keyboard.append([InlineKeyboardButton(
                f"📚 {course_name}", 
                callback_data=f'class_{course_name}'
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        logger.info(f"✅ Invio {len(course_names)} lezioni all'utente")
        await update.message.reply_text(
            f"✅ Trovate {len(course_names)} tipologie di lezioni!\n\n"
            f"📚 Che lezione vuoi prenotare?",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"❌ Errore CRITICO in prenota: {type(e).__name__}: {str(e)}", exc_info=True)
        await update.message.reply_text(
            f"❌ Si è verificato un errore.\n"
            f"Tipo: {type(e).__name__}\n"
            f"Riprova con /prenota"
        )


async def class_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    class_name = query.data.split('_', 1)[1]  # Supporta nomi con spazi
    context.user_data['class_name'] = class_name
    
    # Recupera calendario
    calendar = context.user_data.get('calendar', [])
    
    # Trova date disponibili per questa classe
    available_dates = set()
    
    for course in calendar:
        if course['name'] == class_name:
            for slot in course.get('slots', []):
                # Estrai data (formato: 2025-10-27T10:30:00...)
                date_str = slot['startDateTime'][:10]  # YYYY-MM-DD
                available_dates.add(date_str)
    
    if not available_dates:
        await query.edit_message_text(
            f"❌ Nessuna data disponibile per {class_name}.\n\n"
            f"Usa /prenota per riprovare."
        )
        return
    
    # Crea bottoni con date reali disponibili
    keyboard = []
    
    for date_str in sorted(available_dates)[:7]:  # Max 7 giorni
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
        date_display = date_obj.strftime('%d/%m')
        button_text = f"{day_name} {date_display}"
        keyboard.append([InlineKeyboardButton(
            button_text, 
            callback_data=f"date_{date_str}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📅 Hai scelto: {class_name}\n\n"
        f"Quale giorno? ({len(available_dates)} disponibili)",
        reply_markup=reply_markup
    )


async def date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    date_str = query.data.split('_')[1]
    context.user_data['date'] = date_str
    
    # Recupera calendario e filtra per classe e data
    calendar = context.user_data.get('calendar', [])
    class_name = context.user_data.get('class_name')
    
    # Trova orari disponibili per questa classe e data
    available_times = set()
    
    for course in calendar:
        if course['name'] == class_name:
            for slot in course.get('slots', []):
                slot_date = slot['startDateTime'][:10]  # YYYY-MM-DD
                if slot_date == date_str:
                    # Estrai ora (formato: 2025-10-27T10:30:00...)
                    time_str = slot['startDateTime'][11:16]  # HH:MM
                    available_times.add(time_str)
    
    if not available_times:
        await query.edit_message_text(
            f"❌ Nessun orario disponibile per {class_name} in questa data.\n\n"
            f"Usa /prenota per riprovare."
        )
        return
    
    # Crea bottoni con orari reali
    keyboard = []
    for time in sorted(available_times):
        emoji = "🕐"
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {time}", 
            callback_data=f'time_{time}'
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
    
    await query.edit_message_text(
        f"📚 {class_name}\n"
        f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n\n"
        f"🕐 Scegli l'orario ({len(available_times)} disponibili):",
        reply_markup=reply_markup
    )


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
        
        message += "💡 Usa /cancella <ID> per cancellare"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Errore recupero prenotazioni: {e}")
        await update.message.reply_text("❌ Errore nel recuperare le prenotazioni.")


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
        "Controlla ogni ora se ci sono prenotazioni da fare.\n\n"
        "📲 NOTIFICHE:\n"
        "Riceverai un messaggio quando il bot prenota per te!\n\n"
        "❓ Problemi? Il bot ora prenota REALMENTE su EasyFit! 🎉"
    )


def send_telegram_notification(application, user_id, class_name, class_date, class_time, success):
    """Invia notifica Telegram all'utente"""
    try:
        import asyncio
        
        date_obj = datetime.strptime(class_date, '%Y-%m-%d')
        day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
        
        if success:
            message = (
                f"✅ PRENOTAZIONE EFFETTUATA!\n\n"
                f"📚 {class_name}\n"
                f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                f"🕐 Ore {class_time}\n\n"
                f"🎉 Ci vediamo in palestra!"
            )
        else:
            message = (
                f"❌ PRENOTAZIONE FALLITA\n\n"
                f"📚 {class_name}\n"
                f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                f"🕐 Ore {class_time}\n\n"
                f"⚠️ Prova a prenotare manualmente o contatta il supporto."
            )
        
        # Invia messaggio
        asyncio.run(application.bot.send_message(chat_id=user_id, text=message))
        logger.info(f"📲 Notifica inviata a {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Errore invio notifica: {e}")


def check_and_book(application):
    """Controlla se ci sono prenotazioni da effettuare"""
    
    current_hour = datetime.now().hour
    if not (8 <= current_hour < 21):
        logger.info("⏰ Fuori orario attivo (8-21). Salto controllo.")
        return
    
    logger.info("🔍 Controllo prenotazioni da effettuare...")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)
        
        cur.execute(
            """
            SELECT id, user_id, class_name, class_date, class_time
            FROM bookings
            WHERE status = 'pending'
            AND booking_date BETWEEN %s AND %s
            """,
            (one_hour_ago, now)
        )
        
        bookings_to_make = cur.fetchall()
        
        if not bookings_to_make:
            logger.info("ℹ️ Nessuna prenotazione da effettuare.")
            cur.close()
            conn.close()
            return
        
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time = booking
            
            logger.info(f"🎯 Prenotazione #{booking_id}: {class_name} per {class_date} {class_time}")
            
            # PRENOTAZIONE REALE!
            success = book_class_easyfit(class_name, str(class_date), class_time)
            
            if success:
                # Aggiorna status
                cur.execute(
                    """
                    UPDATE bookings
                    SET status = 'completed'
                    WHERE id = %s
                    """,
                    (booking_id,)
                )
                conn.commit()
                
                # Notifica utente
                send_telegram_notification(application, user_id, class_name, str(class_date), class_time, True)
                
                logger.info(f"✅ Prenotazione #{booking_id} completata!")
            else:
                # Notifica fallimento
                send_telegram_notification(application, user_id, class_name, str(class_date), class_time, False)
                logger.error(f"❌ Prenotazione #{booking_id} fallita!")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Errore nel controllo prenotazioni: {e}")


def main():
    """Avvia il bot"""
    
    # Crea applicazione
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Registra handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("prenota", prenota))
    application.add_handler(CommandHandler("lista", lista))
    application.add_handler(CommandHandler("help", help_command))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(class_selected, pattern="^class_"))
    application.add_handler(CallbackQueryHandler(date_selected, pattern="^date_"))
    application.add_handler(CallbackQueryHandler(time_selected, pattern="^time_"))
    
    # Avvia scheduler (controlla ogni 2 minuti dalle 8 alle 21)
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: check_and_book(application),
        'cron',
        hour='8-21',
        minute='*/2'  # Ogni 2 minuti
    )
    scheduler.start()
    
    logger.info("="*60)
    logger.info("🚀 BOT AVVIATO CON PRENOTAZIONE REALE EASYFIT!")
    logger.info("⏰ Attivo dalle 8:00 alle 21:00 (controllo ogni 2 minuti)")
    logger.info("🎯 Prenotazioni automatiche attive")
    logger.info("="*60)
    
    # Avvia bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
