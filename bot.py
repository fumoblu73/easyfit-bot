import os
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio

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

# Variabile globale per l'application
app_instance = None

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
    keyboard = [
        [InlineKeyboardButton("📚 Pilates", callback_data='class_Pilates')],
        [InlineKeyboardButton("🧘 Yoga", callback_data='class_Yoga')],
        [InlineKeyboardButton("🚴 Spinning", callback_data='class_Spinning')],
        [InlineKeyboardButton("💪 CrossFit", callback_data='class_CrossFit')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📚 Che lezione vuoi prenotare?",
        reply_markup=reply_markup
    )

# Callback lezione selezionata
async def class_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    class_name = query.data.split('_')[1]
    context.user_data['class_name'] = class_name
    
    # Mostra giorni disponibili (prossimi 7 giorni)
    keyboard = []
    today = datetime.now()
    
    for i in range(7):
        date = today + timedelta(days=i)
        day_name = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'][date.weekday()]
        date_str = date.strftime('%d/%m')
        button_text = f"{day_name} {date_str}"
        callback_data = f"date_{date.strftime('%Y-%m-%d')}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📅 Hai scelto: {class_name}\n\nQuale giorno?",
        reply_markup=reply_markup
    )

# Callback data selezionata
async def date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    date_str = query.data.split('_')[1]
    context.user_data['date'] = date_str
    
    # Mostra orari disponibili
    keyboard = [
        [InlineKeyboardButton("🕙 10:00", callback_data='time_10:00')],
        [InlineKeyboardButton("🕕 18:00", callback_data='time_18:00')],
        [InlineKeyboardButton("🕖 19:00", callback_data='time_19:00')],
    ]
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

# Comando /help
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
        "❓ Problemi? Scrivi a: cinzia.caia@hotmail.it"
    )

# Funzione che controlla e prenota (chiamata ogni ora)
async def check_and_book():
    """Controlla se ci sono prenotazioni da fare"""
    
    # Controlla se siamo nell'orario attivo (8-21)
    current_hour = datetime.now().hour
    if not (8 <= current_hour < 21):
        logger.info("⏰ Fuori orario attivo (8-21). Salto controllo.")
        return
    
    logger.info("🔍 Controllo prenotazioni da effettuare...")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Trova prenotazioni da fare (booking_date <= ora attuale, con margine di 1 ora)
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
            logger.info("ℹ️ Nessuna prenotazione da effettuare in questo momento.")
            cur.close()
            conn.close()
            return
        
        # Per ogni prenotazione da fare
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time = booking
            
            logger.info(f"📝 Prenotazione {booking_id}: {class_name} per {class_date} {class_time}")
            
            # PLACEHOLDER: Qui andrà la logica vera di prenotazione su EasyFit
            success = simulate_booking(class_name, class_date, class_time)
            
            if success:
                # Aggiorna status nel database
                cur.execute(
                    """
                    UPDATE bookings
                    SET status = 'completed'
                    WHERE id = %s
                    """,
                    (booking_id,)
                )
                conn.commit()
                
                # Invia notifica Telegram
                await send_telegram_notification(
                    user_id, 
                    class_name, 
                    class_date, 
                    class_time
                )
                
                logger.info(f"✅ Prenotazione {booking_id} completata!")
            else:
                logger.error(f"❌ Prenotazione {booking_id} fallita!")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Errore nel controllo prenotazioni: {e}")

def simulate_booking(class_name, class_date, class_time):
    """PLACEHOLDER: Simula prenotazione"""
    logger.info(f"🔄 [SIMULAZIONE] Prenotazione {class_name} per {class_date} {class_time}")
    return True

async def send_telegram_notification(user_id, class_name, class_date, class_time):
    """Invia notifica Telegram all'utente"""
    try:
        date_obj = datetime.strptime(str(class_date), '%Y-%m-%d')
        day_name = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'][date_obj.weekday()]
        
        message = (
            f"✅ PRENOTAZIONE EFFETTUATA!\n\n"
            f"📚 {class_name}\n"
            f"📅 {day_name} {date_obj.strftime('%d/%m/%Y')}\n"
            f"🕐 Ore {class_time}\n\n"
            f"Ci vediamo alla lezione! 💪"
        )
        
        await app_instance.bot.send_message(chat_id=user_id, text=message)
        logger.info(f"📲 Notifica inviata a {user_id}")
    except Exception as e:
        logger.error(f"❌ Errore invio notifica: {e}")

def scheduler_job():
    """Wrapper sincrono per lo scheduler"""
    asyncio.run(check_and_book())

# Funzione principale
def main():
    """Avvia il bot"""
    global app_instance
    
    # Crea applicazione
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    app_instance = application
    
    # Registra handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("prenota", prenota))
    application.add_handler(CommandHandler("lista", lista))
    application.add_handler(CommandHandler("help", help_command))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(class_selected, pattern="^class_"))
    application.add_handler(CallbackQueryHandler(date_selected, pattern="^date_"))
    application.add_handler(CallbackQueryHandler(time_selected, pattern="^time_"))
    
    # Avvia scheduler (controlla ogni 2 minuti)
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        scheduler_job,
        'cron',
        hour='8-21',      # Solo dalle 8 alle 21
        minute='*/2'      # Ogni 2 minuti
    )
    scheduler.start()
    
    logger.info("🚀 Bot avviato! Attivo dalle 8 alle 21.")
    
    # Avvia bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
