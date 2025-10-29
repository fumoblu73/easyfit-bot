import os
import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

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

# Health check server
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running')
    
    def log_message(self, format, *args):
        return  # Silenzia i log del server HTTP

def start_health_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"🏥 Health check server avviato su porta {port}")

# Keep-alive ping
def keep_alive_ping():
    """Fa un ping a se stesso ogni 10 minuti per evitare spin-down"""
    try:
        port = int(os.environ.get('PORT', 10000))
        url = f"http://localhost:{port}/"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            logger.info("💓 Keep-alive ping OK")
    except Exception as e:
        logger.warning(f"⚠️ Keep-alive ping fallito: {e}")

# Connessione database
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Ciao {user.first_name}!\n\n"
        f"Sono il bot per prenotare le lezioni EasyFit.\n\n"
        f"🤖 Cosa posso fare:\n"
        f"• Prenotare lezioni 72 ore prima automaticamente\n"
        f"• Attivo 24/7\n"
        f"• Notifiche quando prenoto\n\n"
        f"📋 Comandi disponibili:\n"
        f"/prenota - Programma una nuova prenotazione\n"
        f"/lista - Vedi prenotazioni programmate\n"
        f"/cancella <ID> - Cancella prenotazione\n"
        f"/help - Guida completa"
    )

# Comando /prenota
async def prenota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔄 Recupero calendario lezioni da EasyFit...\n"
        "Attendi qualche secondo..."
    )
    
    # Login e recupero calendario
    try:
        token = login_easyfit()
        if not token:
            await update.message.reply_text(
                "❌ Errore nel login a EasyFit.\n"
                "Riprova tra qualche minuto."
            )
            return
        
        calendar = get_calendar(token)
        if not calendar:
            await update.message.reply_text(
                "❌ Errore nel recuperare il calendario.\n"
                "Riprova tra qualche minuto."
            )
            return
        
        # Filtra lezioni future (prossimi 7 giorni)
        now = datetime.now()
        seven_days = now + timedelta(days=7)
        
        # Raggruppa per nome lezione
        classes = {}
        for appointment in calendar:
            class_name = appointment.get('courseName', 'Sconosciuto')
            if class_name not in classes:
                classes[class_name] = []
            
            # Parsing data
            start_time = appointment.get('startTime')
            if start_time:
                lesson_date = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                
                # Solo lezioni future nei prossimi 7 giorni
                if now < lesson_date < seven_days:
                    classes[class_name].append({
                        'id': appointment.get('id'),
                        'date': lesson_date,
                        'instructor': appointment.get('instructorName', 'N/A')
                    })
        
        # Crea bottoni per tipologie lezioni
        keyboard = []
        for class_name in sorted(classes.keys()):
            if classes[class_name]:  # Solo se ci sono lezioni disponibili
                keyboard.append([
                    InlineKeyboardButton(
                        f"📚 {class_name}",
                        callback_data=f"class_{class_name}"
                    )
                ])
        
        if not keyboard:
            await update.message.reply_text(
                "❌ Nessuna lezione disponibile nei prossimi 7 giorni.\n"
                "Riprova più tardi!"
            )
            return
        
        # Salva calendario nel context per uso successivo
        context.user_data['calendar'] = classes
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "📚 Che lezione vuoi prenotare?",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Errore in /prenota: {e}")
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
    
    # Recupera calendario dal context
    calendar = context.user_data.get('calendar', {})
    lessons = calendar.get(class_name, [])
    
    if not lessons:
        await query.edit_message_text("❌ Nessuna lezione disponibile per questa classe.")
        return
    
    # Crea bottoni per date/orari
    keyboard = []
    for lesson in lessons[:10]:  # Max 10 lezioni
        lesson_date = lesson['date']
        day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][lesson_date.weekday()]
        date_str = lesson_date.strftime('%d/%m')
        time_str = lesson_date.strftime('%H:%M')
        
        button_text = f"{day_name} {date_str} ore {time_str}"
        callback_data = f"lesson_{lesson['id']}"
        
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📚 {class_name}\n\n"
        f"📅 Scegli data e orario:",
        reply_markup=reply_markup
    )

# Callback lezione specifica selezionata
async def lesson_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    lesson_id = int(query.data.split('_')[1])
    
    # Trova lezione nel calendario
    calendar = context.user_data.get('calendar', {})
    class_name = context.user_data.get('class_name')
    
    selected_lesson = None
    for lesson in calendar.get(class_name, []):
        if lesson['id'] == lesson_id:
            selected_lesson = lesson
            break
    
    if not selected_lesson:
        await query.edit_message_text("❌ Errore nel recuperare i dettagli della lezione.")
        return
    
    lesson_date = selected_lesson['date']
    
    # Calcola quando prenotare (72h prima)
    booking_datetime = lesson_date - timedelta(hours=72)
    
    # Salva nel database
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO bookings 
            (user_id, class_name, class_date, class_time, booking_date, status, class_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                str(query.from_user.id),
                class_name,
                lesson_date.date(),
                lesson_date.strftime('%H:%M'),
                booking_datetime,
                'pending',
                lesson_id
            )
        )
        
        booking_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][lesson_date.weekday()]
        
        await query.edit_message_text(
            f"✅ PRENOTAZIONE PROGRAMMATA!\n\n"
            f"📚 Lezione: {class_name}\n"
            f"📅 Data: {day_name} {lesson_date.strftime('%d/%m/%Y')}\n"
            f"🕐 Orario: {lesson_date.strftime('%H:%M')}\n"
            f"👨‍🏫 Istruttore: {selected_lesson['instructor']}\n\n"
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
                "📋 Non hai prenotazioni attive.\n\n"
                "Usa /prenota per programmarne una!"
            )
            return
        
        message = "📋 LE TUE PRENOTAZIONI:\n\n"
        
        for booking in bookings:
            booking_id, class_name, class_date, class_time, booking_date, status = booking
            date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
            day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date_obj.weekday()]
            
            status_emoji = {
                'pending': '⏳',
                'waitlisted': '📋'
            }.get(status, '❓')
            
            status_text = {
                'pending': 'Programmata',
                'waitlisted': 'Lista d\'attesa'
            }.get(status, 'Sconosciuto')
            
            message += f"{status_emoji} #{booking_id} - {class_name}\n"
            message += f"   📅 {day_name} {date_obj.strftime('%d/%m/%Y')} ore {class_time}\n"
            message += f"   Status: {status_text}\n"
            
            if status == 'pending':
                message += f"   ⏰ Prenoterò: {booking_date.strftime('%d/%m/%Y alle %H:%M')}\n"
            
            message += "\n"
        
        message += "💡 Usa /cancella <ID> per cancellare"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Errore recupero prenotazioni: {e}")
        await update.message.reply_text("❌ Errore nel recuperare le prenotazioni.")

# Comando /cancella
async def cancella(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Devi specificare l'ID della prenotazione.\n\n"
            "Esempio: /cancella 5\n\n"
            "Usa /lista per vedere gli ID."
        )
        return
    
    try:
        booking_id = int(context.args[0])
        user_id = str(update.effective_user.id)
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Verifica che la prenotazione appartenga all'utente
        cur.execute(
            """
            SELECT id FROM bookings
            WHERE id = %s AND user_id = %s AND status IN ('pending', 'waitlisted')
            """,
            (booking_id, user_id)
        )
        
        if not cur.fetchone():
            await update.message.reply_text(
                f"❌ Prenotazione #{booking_id} non trovata o già completata."
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
            f"✅ Prenotazione #{booking_id} cancellata con successo!"
        )
        
    except ValueError:
        await update.message.reply_text("❌ ID non valido. Deve essere un numero.")
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
        "   Mostra le lezioni reali disponibili.\n"
        "   Scegli quella che vuoi e il bot prenoterà 72h prima.\n\n"
        "/lista - Vedi tutte le prenotazioni programmate\n"
        "   Mostra cosa hai in programma e lo status.\n\n"
        "/cancella <ID> - Cancella una prenotazione\n"
        "   Esempio: /cancella 5\n\n"
        "⏰ FUNZIONAMENTO:\n"
        "Il bot controlla OGNI MINUTO se ci sono prenotazioni da fare.\n"
        "È attivo 24/7 per non perdere nessuna lezione!\n\n"
        "📲 NOTIFICHE:\n"
        "Riceverai un messaggio quando il bot prenota per te,\n"
        "sia che trovi posto sia se finisci in lista d'attesa.\n\n"
        "🔄 LISTA D'ATTESA:\n"
        "Se la lezione è piena, il bot ti inserisce\n"
        "automaticamente in lista d'attesa!\n\n"
        "❓ Problemi? Contatta il supporto."
    )

# === FUNZIONI EASYFIT API ===

def login_easyfit():
    """Effettua login su EasyFit e ottiene token"""
    try:
        url = "https://app-easyfitpalestre.it/nox/v1/auth/login"
        
        payload = {
            "email": EASYFIT_EMAIL,
            "password": EASYFIT_PASSWORD
        }
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        logger.info(f"🔐 Tentativo login...")
        logger.info(f"   URL: {url}")
        logger.info(f"   Email: {EASYFIT_EMAIL}")
        logger.info(f"   Password: {'*' * len(EASYFIT_PASSWORD)}")
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        logger.info(f"📥 Response Status: {response.status_code}")
        logger.info(f"📥 Response Headers: {dict(response.headers)}")
        logger.info(f"📥 Response Body: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            token = data.get('accessToken')
            if token:
                logger.info("✅ Login EasyFit OK")
                logger.info(f"   Token: {token[:50]}...")
                return token
            else:
                logger.error("❌ Token non trovato nella risposta")
                return None
        else:
            logger.error(f"❌ Login fallito: {response.status_code}")
            logger.error(f"   Body: {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Errore login: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def get_calendar(token):
    """Recupera calendario lezioni"""
    try:
        url = "https://app-easyfitpalestre.it/nox/v1/calendar/mycalendar"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Range di 7 giorni
        now = datetime.now()
        start = now.strftime('%Y-%m-%dT00:00:00.000Z')
        end = (now + timedelta(days=7)).strftime('%Y-%m-%dT23:59:59.999Z')
        
        params = {
            "from": start,
            "to": end
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            appointments = data.get('courseAppointments', [])
            logger.info(f"✅ Recuperate {len(appointments)} lezioni")
            return appointments
        else:
            logger.error(f"❌ Errore calendario: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Errore get_calendar: {e}")
        return None

def book_class_easyfit(class_id, class_name, class_date, class_time):
    """
    Prenota effettivamente la lezione su EasyFit
    
    🔧 FIX APPLICATO: expectedCustomerStatus = "WAITING_LIST" (non "WAITLISTED")
    """
    try:
        # 1. Login
        logger.info(f"🔐 Login EasyFit per prenotazione...")
        token = login_easyfit()
        if not token:
            logger.error("❌ Login fallito")
            return False, None
        
        # 2. Prepara richiesta prenotazione
        url = "https://app-easyfitpalestre.it/nox/v1/calendar/bookcourse"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Primo tentativo: prenotazione normale
        payload = {
            "courseAppointmentId": class_id,
            "expectedCustomerStatus": "BOOKED"
        }
        
        logger.info(f"📝 Tentativo prenotazione normale...")
        logger.info(f"   Lesson ID: {class_id}")
        logger.info(f"   Payload: {payload}")
        
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        logger.info(f"📥 Response status: {response.status_code}")
        logger.info(f"📥 Response body: {response.text}")
        
        if response.status_code == 200:
            logger.info(f"✅ PRENOTAZIONE EFFETTUATA: {class_name} {class_date} {class_time}")
            return True, "completed"
        
        # Se fallisce, prova lista d'attesa
        logger.info(f"⚠️ Prenotazione normale fallita, provo lista d'attesa...")
        
        # 🔧 FIX: Usa "WAITING_LIST" invece di "WAITLISTED"
        payload_waitlist = {
            "courseAppointmentId": class_id,
            "expectedCustomerStatus": "WAITING_LIST"  # ✅ CORRETTO
        }
        
        logger.info(f"📝 Tentativo lista d'attesa...")
        logger.info(f"   Payload: {payload_waitlist}")
        
        response_waitlist = requests.post(url, headers=headers, json=payload_waitlist, timeout=10)
        
        logger.info(f"📥 Response status: {response_waitlist.status_code}")
        logger.info(f"📥 Response body: {response_waitlist.text}")
        
        if response_waitlist.status_code == 200:
            logger.info(f"📋 IN LISTA D'ATTESA: {class_name} {class_date} {class_time}")
            return True, "waitlisted"
        
        # Entrambi i tentativi falliti
        logger.error(f"❌ Prenotazione fallita completamente")
        logger.error(f"   Normale: {response.status_code} - {response.text}")
        logger.error(f"   Waitlist: {response_waitlist.status_code} - {response_waitlist.text}")
        
        return False, None
        
    except Exception as e:
        logger.error(f"❌ Errore book_class_easyfit: {e}")
        return False, None

# Funzione che controlla e prenota
def check_and_book(application):
    """
    Controlla se ci sono prenotazioni da fare
    
    FIX TIMEZONE: Usa datetime.now(timezone.utc) per confronto corretto
    FIX RECUPERO: Recupera TUTTE le prenotazioni scadute, non solo ultime 2 ore
    """
    
    now_utc = datetime.now(timezone.utc)
    logger.info(f"🔍 CONTROLLO PRENOTAZIONI (ogni minuto)")
    logger.info(f"   ⏰ Ora UTC: {now_utc}")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Trova TUTTE le prenotazioni scadute (anche vecchie, per recuperare arretrati)
        cur.execute(
            """
            SELECT id, user_id, class_name, class_date, class_time, class_id, booking_date
            FROM bookings
            WHERE status = 'pending'
            AND booking_date <= %s
            ORDER BY booking_date ASC
            """,
            (now_utc,)
        )
        
        bookings_to_make = cur.fetchall()
        
        if not bookings_to_make:
            logger.info("   ℹ️ Nessuna prenotazione da effettuare")
            cur.close()
            conn.close()
            return
        
        logger.info(f"   📋 Trovate {len(bookings_to_make)} prenotazioni da processare")
        
        # Per ogni prenotazione da fare
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time, class_id, booking_date = booking
            
            # Calcola ritardo
            delay = now_utc - booking_date
            delay_minutes = int(delay.total_seconds() / 60)
            
            if delay_minutes > 0:
                logger.warning(f"   ⚠️ Prenotazione #{booking_id} in ritardo di {delay_minutes} minuti!")
            
            logger.info(f"📝 PRENOTAZIONE #{booking_id}")
            logger.info(f"   📚 {class_name}")
            logger.info(f"   📅 {class_date} ore {class_time}")
            logger.info(f"   🆔 Class ID: {class_id}")
            logger.info(f"   👤 User ID: {user_id}")
            
            # Effettua prenotazione
            success, status = book_class_easyfit(class_id, class_name, class_date, class_time)
            
            if success:
                # Aggiorna status nel database
                cur.execute(
                    """
                    UPDATE bookings
                    SET status = %s
                    WHERE id = %s
                    """,
                    (status, booking_id)
                )
                conn.commit()
                
                # Invia notifica Telegram
                asyncio.run(send_telegram_notification(
                    application,
                    user_id,
                    class_name,
                    class_date,
                    class_time,
                    status
                ))
                
                logger.info(f"🎉 Prenotazione #{booking_id} completata con status: {status}")
            else:
                # Segna come completata anche se fallita (per non ritentare)
                cur.execute(
                    """
                    UPDATE bookings
                    SET status = 'completed'
                    WHERE id = %s
                    """,
                    (booking_id,)
                )
                conn.commit()
                
                # Notifica fallimento
                asyncio.run(send_telegram_notification_failure(
                    application,
                    user_id,
                    class_name,
                    class_date,
                    class_time
                ))
                
                logger.error(f"❌ Prenotazione #{booking_id} fallita!")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Errore nel controllo prenotazioni: {e}")

import asyncio

async def send_telegram_notification(application, user_id, class_name, class_date, class_time, status):
    """Invia notifica Telegram all'utente"""
    try:
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
        
        await application.bot.send_message(chat_id=int(user_id), text=message)
        logger.info(f"📲 Notifica inviata a {user_id}")
        
    except Exception as e:
        logger.error(f"Errore invio notifica: {e}")

async def send_telegram_notification_failure(application, user_id, class_name, class_date, class_time):
    """Invia notifica di fallimento"""
    try:
        message = (
            f"❌ PRENOTAZIONE NON POSSIBILE\n\n"
            f"📚 {class_name}\n"
            f"📅 {class_date}\n"
            f"🕐 {class_time}\n\n"
            f"La lezione è piena e anche la lista d'attesa.\n"
            f"Prova manualmente su app EasyFit o scegli altra lezione."
        )
        
        await application.bot.send_message(chat_id=int(user_id), text=message)
        logger.info(f"📲 Notifica fallimento inviata a {user_id}")
        
    except Exception as e:
        logger.error(f"Errore invio notifica fallimento: {e}")

# Funzione principale
def main():
    """Avvia il bot"""
    
    # Avvia health check server
    start_health_server()
    
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
    application.add_handler(CallbackQueryHandler(lesson_selected, pattern="^lesson_"))
    
    # Avvia scheduler
    scheduler = BackgroundScheduler()
    
    # Keep-alive: ping ogni 10 minuti (24/7) per evitare spin-down
    scheduler.add_job(
        keep_alive_ping,
        'cron',
        minute='*/10'
    )
    
    # Controllo prenotazioni: ogni minuto (sempre attivo)
    scheduler.add_job(
        lambda: check_and_book(application),
        'cron',
        minute='*'
    )
    
    scheduler.start()
    
    logger.info("🚀 Bot avviato!")
    logger.info("   💓 Keep-alive: ogni 10 minuti")
    logger.info("   🔍 Controllo prenotazioni: ogni minuto")
    logger.info("   🌍 Timezone: UTC")
    
    # Avvia bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
