#!/usr/bin/env python3
"""
EasyFit Bot - VERSIONE FINALE COMPLETA
- API EasyFit reale integrata
- Database funzionante (fix commit)
- Calendario reale
- Prenotazioni reali
- Logging completo
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
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
DATABASE_URL = os.getenv('DATABASE_URL')
EASYFIT_EMAIL = os.getenv('EASYFIT_EMAIL')
EASYFIT_PASSWORD = os.getenv('EASYFIT_PASSWORD')

# Costanti EasyFit
EASYFIT_BASE_URL = "https://app-easyfitpalestre.it"
STUDIO_ID = "ZWFzeWZpdDoxMjE2OTE1Mzgw"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


# ============================================================================
# FUNZIONI API EASYFIT
# ============================================================================

def easyfit_login():
    """Effettua login su EasyFit e restituisce session object"""
    try:
        logger.info("🔐 Tentativo login EasyFit...")
        
        # Crea una nuova sessione
        session = requests.Session()
        
        url = f"{EASYFIT_BASE_URL}/login"
        
        # Crea Basic Auth header
        credentials = f"{EASYFIT_EMAIL}:{EASYFIT_PASSWORD}"
        basic_auth = base64.b64encode(credentials.encode()).decode()
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "it-IT,it;q=0.9",
            "Authorization": f"Basic {basic_auth}",
            "Origin": EASYFIT_BASE_URL,
            "Referer": f"{EASYFIT_BASE_URL}/studio/{STUDIO_ID}/course",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "x-tenant": "easyfit",
            "x-ms-web-context": f"/studio/{STUDIO_ID}",
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
            
            logger.info(f"✅ Login OK - SessionID: {session_id[:20]}...")
            return session
        else:
            logger.error(f"❌ Login fallito: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Errore login: {e}")
        return None


def find_course_appointment_id(session, class_name, class_date, class_time):
    """Trova il courseAppointmentId per una lezione specifica"""
    try:
        logger.info(f"🔎 Cerco lezione: {class_name} del {class_date} ore {class_time}")
        
        # Calcola range date (72h prima + 7 giorni dopo)
        date_obj = datetime.strptime(class_date, '%Y-%m-%d')
        start_date = (date_obj - timedelta(hours=72)).strftime('%Y-%m-%d')
        end_date = (date_obj + timedelta(days=7)).strftime('%Y-%m-%d')
        
        url = f"{EASYFIT_BASE_URL}/nox/v2/bookableitems/courses/with-canceled"
        
        params = {
            "facilityId": "easyfit:1216915380",
            "startDate": start_date,
            "endDate": end_date
        }
        
        response = session.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            courses = response.json()
            logger.info(f"📚 Trovati {len(courses)} corsi")
            
            # Cerca il corso specifico
            target_datetime = f"{class_date}T{class_time}:00"
            
            for course in courses:
                if course.get('name') == class_name:
                    for slot in course.get('slots', []):
                        slot_time = slot.get('startDateTime', '')
                        if slot_time.startswith(target_datetime):
                            course_id = slot.get('id')
                            logger.info(f"✅ Trovato! ID: {course_id}")
                            return course_id, slot
            
            logger.warning(f"⚠️  Lezione non trovata: {class_name} {class_date} {class_time}")
            return None, None
        else:
            logger.error(f"❌ Errore ricerca: {response.status_code}")
            return None, None
            
    except Exception as e:
        logger.error(f"❌ Errore find_course: {e}")
        return None, None


def book_course_easyfit(session, course_appointment_id):
    """Prenota un corso su EasyFit usando l'API reale"""
    try:
        logger.info(f"📝 Prenotazione corso ID: {course_appointment_id}")
        
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
                logger.info(f"✅ PRENOTAZIONE CONFERMATA!")
                return True, data
            else:
                logger.warning(f"⚠️  Status: {status}")
                return False, data
        else:
            logger.error(f"❌ Prenotazione fallita: {response.status_code}")
            logger.error(f"   Response: {response.text}")
            return False, None
            
    except Exception as e:
        logger.error(f"❌ Errore prenotazione: {e}")
        return False, None


# ============================================================================
# FUNZIONE CHECK_AND_BOOK COMPLETA
# ============================================================================

def check_and_book(application):
    """Controlla e prenota - VERSIONE FINALE CON API REALE"""
    
    logger.info("="*60)
    logger.info("🔍 CONTROLLO PRENOTAZIONI")
    logger.info(f"⏰ Ora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Controllo orario
    current_hour = datetime.now().hour
    
    if not (8 <= current_hour < 21):
        logger.info(f"⏰ Fuori orario (8-21). Ora: {current_hour}h")
        logger.info("="*60)
        return
    
    logger.info("✅ Dentro orario attivo (8-21)")
    
    # Connessione database
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
        
        # Cerca prenotazioni pending il cui booking_date è passato
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
            logger.info("ℹ️  Nessuna prenotazione da effettuare")
            cur.close()
            conn.close()
            logger.info("="*60)
            return
        
        # Processa ogni prenotazione
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time = booking
            
            logger.info("")
            logger.info(f"{'─'*60}")
            logger.info(f"📝 PRENOTAZIONE #{booking_id}")
            logger.info(f"   📚 {class_name}")
            logger.info(f"   📅 {class_date} ore {class_time}")
            
            # === PRENOTAZIONE REALE CON API EASYFIT ===
            
            # 1. Login
            session = easyfit_login()
            
            if not session:
                logger.error(f"❌ Login fallito per #{booking_id}")
                send_telegram_notification(
                    application, user_id, class_name,
                    class_date, class_time, False
                )
                continue
            
            # 2. Trova courseAppointmentId
            course_id, slot = find_course_appointment_id(
                session, class_name, class_date, class_time
            )
            
            if not course_id:
                logger.error(f"❌ Corso non trovato per #{booking_id}")
                send_telegram_notification(
                    application, user_id, class_name,
                    class_date, class_time, False
                )
                continue
            
            # 3. Prenota
            success, result = book_course_easyfit(session, course_id)
            
            if success:
                logger.info(f"✅ Prenotazione #{booking_id} RIUSCITA!")
                
                # Aggiorna database
                logger.info(f"💾 Aggiornamento database...")
                
                cur.execute(
                    """
                    UPDATE bookings
                    SET status = 'completed'
                    WHERE id = %s
                    """,
                    (booking_id,)
                )
                
                conn.commit()
                logger.info(f"✅ Database aggiornato (commit OK)")
                
                # Notifica
                send_telegram_notification(
                    application, user_id, class_name,
                    class_date, class_time, True
                )
                
                logger.info(f"🎉 Prenotazione #{booking_id} COMPLETATA!")
            else:
                logger.error(f"❌ Prenotazione #{booking_id} FALLITA!")
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
                f"Riprova manualmente su app EasyFit"
            )
        
        import asyncio
        asyncio.run(application.bot.send_message(chat_id=user_id, text=message))
        
    except Exception as e:
        logger.error(f"Errore notifica: {e}")


# ============================================================================
# HANDLER TELEGRAM
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot EasyFit\n\n"
        "✅ Sistema attivo e funzionante\n"
        "✅ API EasyFit reale integrata\n"
        "✅ Controllo ogni 2 minuti (8-21)\n\n"
        "Comandi:\n"
        "/test - Forza controllo prenotazioni\n"
        "/status - Stato prenotazioni"
    )


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forza controllo manuale"""
    await update.message.reply_text("🔍 Controllo in corso...")
    
    try:
        check_and_book(context.application)
        await update.message.reply_text("✅ Fatto! Controlla i logs su Render.")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra prenotazioni"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, class_name, class_date, class_time, booking_date, status
            FROM bookings
            ORDER BY booking_date DESC
            LIMIT 10
        """)
        
        bookings = cur.fetchall()
        
        if not bookings:
            await update.message.reply_text("📋 Nessuna prenotazione")
            return
        
        message = "📊 PRENOTAZIONI:\n\n"
        for b in bookings:
            emoji = "✅" if b[5] == "completed" else "⏳" if b[5] == "pending" else "❌"
            message += f"{emoji} #{b[0]} - {b[1]}\n"
            message += f"   📅 {b[2]} ore {b[3]}\n"
            message += f"   Status: {b[5]}\n\n"
        
        await update.message.reply_text(message)
        
        cur.close()
        conn.close()
        
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


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
    logger.info("🚀 EASYFIT BOT - VERSIONE FINALE COMPLETA")
    logger.info("="*70)
    logger.info("✅ API EasyFit reale integrata")
    logger.info("✅ Database funzionante")
    logger.info("✅ Prenotazioni automatiche attive")
    logger.info("="*70)
    
    # Crea applicazione
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handler
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("status", status_command))
    
    # Scheduler
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
    
    # Health server
    start_health_server()
    
    logger.info("")
    logger.info("="*70)
    logger.info("✅ BOT PRONTO E OPERATIVO")
    logger.info("="*70)
    logger.info("")
    
    # Avvia
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
