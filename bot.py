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
import json

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

# Cache globale per sessione (per lo scheduler)
_global_session = None

# Connessione database
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


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
        
        logger.info(f"🔍 Richiesta calendario: {start_date} → {end_date}")
        
        # USA LA SESSIONE (con cookie) invece di fare nuova richiesta
        response = session.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            courses = response.json()
            logger.info(f"✅ Calendario: {len(courses)} corsi")
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
            
            payload["expectedCustomerStatus"] = "WAITLISTED"
            
            response = session.post(url, json=payload, headers=headers, timeout=10)
            
            if response.status_code == 200:
                logger.info(f"📋 LISTA D'ATTESA!")
                return True, "waitlisted", response.json()
        
        logger.error(f"❌ Prenotazione fallita: {response.status_code}")
        logger.error(f"   Response: {response.text[:200]}")
        return False, "failed", None
            
    except Exception as e:
        logger.error(f"❌ Errore book_course_easyfit: {e}")
        return False, "failed", None


def find_course_appointment_id(session, class_name, class_date, class_time):
    """Trova il courseAppointmentId cercando nel calendario"""
    try:
        date_obj = datetime.strptime(class_date, '%Y-%m-%d')
        start_date = date_obj.strftime('%Y-%m-%d')
        end_date = date_obj.strftime('%Y-%m-%d')
        
        logger.info(f"🔎 Cerco: {class_name} {class_date} {class_time}")
        
        courses = get_calendar_courses(session, start_date, end_date)
        
        for course in courses:
            if course['name'].lower() == class_name.lower():
                for slot in course.get('slots', []):
                    slot_datetime_str = slot['startDateTime']
                    slot_datetime = slot_datetime_str.split('[')[0]
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
    """Salva la prenotazione nel database o prenota subito se < 72h"""
    query = update.callback_query
    await query.answer()
    
    time_str = query.data.split('_')[1]
    context.user_data['time'] = time_str
    
    class_name = context.user_data['class_name']
    class_date = context.user_data['date']
    
    class_datetime = datetime.strptime(f"{class_date} {time_str}", '%Y-%m-%d %H:%M')
    booking_datetime = class_datetime - timedelta(hours=72)
    now = datetime.now()
    
    # CALCOLA ORE MANCANTI
    hours_until_class = (class_datetime - now).total_seconds() / 3600
    
    # SE MANCANO MENO DI 72 ORE → PRENOTA SUBITO
    if hours_until_class < 72:
        logger.info(f"⚡ Prenotazione immediata! Mancano {hours_until_class:.1f} ore")
        
        await query.edit_message_text(
            f"⚡ Mancano meno di 72 ore!\n\n"
            f"Sto prenotando SUBITO...\n"
            f"⏳ Attendi qualche secondo..."
        )
        
        # IMPORTANTE: Fai NUOVO LOGIN per avere sessione fresca
        # (la sessione precedente potrebbe essere scaduta)
        logger.info("🔐 Nuovo login per prenotazione immediata...")
        session = easyfit_login()
        
        if not session:
            await query.edit_message_text(
                "❌ Impossibile connettersi a EasyFit.\n"
                "Riprova con /prenota"
            )
            return
        
        # TROVA COURSE ID
        course_appointment_id, slot = find_course_appointment_id(
            session, class_name, class_date, time_str
        )
        
        if not course_appointment_id:
            await query.edit_message_text(
                f"❌ Lezione non trovata su EasyFit.\n\n"
                f"📚 {class_name}\n"
                f"📅 {class_date} {time_str}\n\n"
                f"Verifica che sia ancora disponibile sull'app."
            )
            return
        
        # PRENOTA SUBITO
        success, final_status, result = book_course_easyfit(session, course_appointment_id)
        
        date_obj = datetime.strptime(class_date, '%Y-%m-%d')
        day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
        
        if success:
            if final_status == "completed":
                await query.edit_message_text(
                    f"✅ PRENOTATO SUBITO!\n\n"
                    f"📚 {class_name}\n"
                    f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                    f"🕐 {time_str}\n\n"
                    f"⚡ Prenotazione immediata completata!\n"
                    f"(Mancavano meno di 72 ore)\n\n"
                    f"Ci vediamo in palestra! 💪"
                )
            elif final_status == "waitlisted":
                await query.edit_message_text(
                    f"📋 IN LISTA D'ATTESA!\n\n"
                    f"📚 {class_name}\n"
                    f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                    f"🕐 {time_str}\n\n"
                    f"⚠️ La lezione era piena!\n"
                    f"Sei stato inserito in lista d'attesa.\n\n"
                    f"🔔 Controlla l'app EasyFit per aggiornamenti."
                )
        else:
            await query.edit_message_text(
                f"❌ PRENOTAZIONE FALLITA\n\n"
                f"📚 {class_name}\n"
                f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                f"🕐 {time_str}\n\n"
                f"Prova manualmente su app EasyFit."
            )
        
        logger.info(f"⚡ Prenotazione immediata: {class_name} - {final_status}")
        return
    
    # ALTRIMENTI → PROGRAMMA PER 72H PRIMA (COMPORTAMENTO NORMALE)
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
        logger.error(f"❌ Errore salvataggio: {e}")
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
            date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
            day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
            
            status_icon = "📋" if status == "waitlisted" else "⏳"
            status_text = "Lista d'attesa" if status == "waitlisted" else "Programmata"
            
            message += f"{status_icon} #{booking_id} - {class_name}\n"
            message += f"   📅 {day_name} {date_obj.strftime('%d/%m/%Y')} ore {class_time}\n"
            message += f"   Status: {status_text}\n"
            
            if status == "pending":
                message += f"   ⏰ Prenoterò: {booking_date.strftime('%d/%m/%Y alle %H:%M')}\n"
            
            message += "\n"
        
        message += "💡 Usa /cancella per cancellare una prenotazione"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"❌ Errore lista: {e}")
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
                "📋 Non hai prenotazioni da cancellare."
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
                callback_data=f"cancel_{booking_id}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🗑️ Quale prenotazione vuoi cancellare?",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"❌ Errore cancella: {e}")
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
            DELETE FROM bookings
            WHERE id = %s AND user_id = %s
            """,
            (booking_id, user_id)
        )
        
        conn.commit()
        cur.close()
        conn.close()
        
        await query.edit_message_text(
            f"✅ Prenotazione #{booking_id} cancellata!"
        )
        
        logger.info(f"✅ Prenotazione #{booking_id} cancellata da utente {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Errore cancellazione: {e}")
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
        "🎯 NOVITÀ: Il bot ora mostra le lezioni VERE da EasyFit! 🎉\n\n"
        "📋 LISTA D'ATTESA:\n"
        "Se una lezione è piena, il bot prova automaticamente\n"
        "ad iscriverti alla lista d'attesa!"
    )


def send_telegram_notification(application, user_id, class_name, class_date, class_time, status):
    """Invia notifica Telegram dopo la prenotazione"""
    try:
        import asyncio
        
        date_obj = datetime.strptime(class_date, '%Y-%m-%d')
        day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
        
        if status == "completed":
            message = (
                f"✅ PRENOTAZIONE EFFETTUATA!\n\n"
                f"📚 {class_name}\n"
                f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                f"🕐 {class_time}\n\n"
                f"Ci vediamo in palestra! 💪"
            )
        elif status == "waitlisted":
            message = (
                f"⏳ IN LISTA D'ATTESA\n\n"
                f"📚 {class_name}\n"
                f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                f"🕐 {class_time}\n\n"
                f"⚠️ La lezione era piena!\n"
                f"Sei stato inserito in lista d'attesa.\n\n"
                f"🔔 Ti avviseremo se si libera un posto!\n"
                f"Controlla l'app EasyFit per aggiornamenti."
            )
        else:
            message = (
                f"❌ PRENOTAZIONE NON POSSIBILE\n\n"
                f"📚 {class_name}\n"
                f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
                f"🕐 {class_time}\n\n"
                f"La lezione è piena e anche la lista d'attesa.\n"
                f"Prova manualmente su app EasyFit o scegli altra lezione."
            )
        
        asyncio.run(application.bot.send_message(chat_id=user_id, text=message))
        logger.info(f"📲 Notifica inviata a {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Errore invio notifica: {e}")


def check_and_book(application):
    """Controlla e prenota (chiamata dallo scheduler)"""
    global _global_session
    
    current_hour = datetime.now().hour
    if not (8 <= current_hour < 21):
        logger.info("⏰ Fuori orario attivo (8-21)")
        return
    
    logger.info("🔍 CONTROLLO PRENOTAZIONI")
    
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
            logger.info("ℹ️ Nessuna prenotazione")
            cur.close()
            conn.close()
            return
        
        # FASE 1: Login UNICO per tutte le prenotazioni
        if not _global_session:
            _global_session = easyfit_login()
        
        if not _global_session:
            logger.error("❌ Login fallito")
            cur.close()
            conn.close()
            return
        
        # FASE 2: Processa ogni prenotazione
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time = booking
            
            logger.info(f"📝 PRENOTAZIONE #{booking_id}")
            logger.info(f"   📚 {class_name}")
            logger.info(f"   📅 {class_date} ore {class_time}")
            
            # Trova courseAppointmentId
            course_appointment_id, slot = find_course_appointment_id(
                _global_session, class_name, class_date, class_time
            )
            
            if not course_appointment_id:
                logger.error(f"❌ Corso non trovato")
                send_telegram_notification(application, user_id, class_name, class_date, class_time, "failed")
                
                # Marca come completed anche se fallita
                cur.execute(
                    "UPDATE bookings SET status = 'completed' WHERE id = %s",
                    (booking_id,)
                )
                conn.commit()
                continue
            
            # Prenota (con tentativo waitlist automatico)
            success, final_status, result = book_course_easyfit(_global_session, course_appointment_id)
            
            if success:
                # Aggiorna database
                cur.execute(
                    "UPDATE bookings SET status = %s WHERE id = %s",
                    (final_status, booking_id)
                )
                conn.commit()
                
                # Notifica
                send_telegram_notification(application, user_id, class_name, class_date, class_time, final_status)
                
                logger.info(f"🎉 Completata! Status: {final_status}")
            else:
                send_telegram_notification(application, user_id, class_name, class_date, class_time, "failed")
                
                # Marca come completed anche se fallita
                cur.execute(
                    "UPDATE bookings SET status = 'completed' WHERE id = %s",
                    (booking_id,)
                )
                conn.commit()
                
                logger.error(f"❌ Fallita!")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Errore check_and_book: {e}")


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
        logger.info(f"🌐 Health check server su porta {port}")
    except Exception as e:
        logger.error(f"❌ Errore health server: {e}")


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
    logger.info("📋 Gestione automatica lista d'attesa")
    logger.info("="*60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
