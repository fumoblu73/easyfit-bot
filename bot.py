#!/usr/bin/env python3
"""
EasyFit Bot - Versione con Debug Dettagliato
Aggiunge logging per capire perché lo scheduler non funziona
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

# Configurazione logging PIÙ DETTAGLIATO
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

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


# ============================================================================
# FUNZIONE CHECK_AND_BOOK CON LOGGING DETTAGLIATO
# ============================================================================

def check_and_book(application):
    """Controlla e prenota - VERSIONE DEBUG"""
    
    logger.info("="*60)
    logger.info("🔍 INIZIO CONTROLLO PRENOTAZIONI")
    logger.info(f"⏰ Ora attuale: {datetime.now()}")
    
    # 1. Controllo orario
    current_hour = datetime.now().hour
    logger.info(f"📍 Orario corrente: {current_hour}:xx")
    
    if not (8 <= current_hour < 21):
        logger.info(f"⏰ Fuori orario attivo (8-21). Orario attuale: {current_hour}h")
        logger.info("="*60)
        return
    
    logger.info("✅ Dentro orario attivo (8-21)")
    
    # 2. Connessione database
    try:
        logger.info("🔌 Connessione al database...")
        conn = get_db_connection()
        cur = conn.cursor()
        logger.info("✅ Connesso al database")
    except Exception as e:
        logger.error(f"❌ Errore connessione database: {e}")
        logger.info("="*60)
        return
    
    # 3. Query prenotazioni
    try:
        now = datetime.now()
        two_hours_ago = now - timedelta(hours=2)
        
        logger.info(f"🔎 Cerco prenotazioni tra {two_hours_ago} e {now}")
        
        cur.execute(
            """
            SELECT id, user_id, class_name, class_date, class_time, booking_date, status
            FROM bookings
            WHERE status = 'pending'
            ORDER BY booking_date
            """
        )
        
        all_pending = cur.fetchall()
        logger.info(f"📊 Totale prenotazioni 'pending': {len(all_pending)}")
        
        if all_pending:
            logger.info("📋 Lista prenotazioni pending:")
            for b in all_pending:
                logger.info(f"   #{b[0]} - {b[2]} - {b[3]} {b[4]} - booking_date: {b[5]}")
        
        # Query con filtro temporale
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
        
        logger.info(f"🎯 Prenotazioni DA FARE ORA: {len(bookings_to_make)}")
        
        if not bookings_to_make:
            logger.info("ℹ️  Nessuna prenotazione da effettuare in questo momento")
            cur.close()
            conn.close()
            logger.info("="*60)
            return
        
        # 4. Processa prenotazioni
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time = booking
            
            logger.info("")
            logger.info(f"{'─'*60}")
            logger.info(f"📝 PROCESSO PRENOTAZIONE #{booking_id}")
            logger.info(f"   Lezione: {class_name}")
            logger.info(f"   Data: {class_date}")
            logger.info(f"   Ora: {class_time}")
            logger.info(f"   User ID: {user_id}")
            
            # Simula prenotazione per debug
            logger.info(f"🔄 [DEBUG MODE] Simulazione prenotazione...")
            success = True  # Simulato
            
            if success:
                logger.info(f"✅ Prenotazione simulata con successo")
                
                # Aggiorna database
                cur.execute(
                    """
                    UPDATE bookings
                    SET status = 'completed'
                    WHERE id = %s
                    """,
                    (booking_id,)
                )
                conn.commit()
                logger.info(f"💾 Database aggiornato: status → 'completed'")
                
                # Invia notifica Telegram
                try:
                    send_telegram_notification(
                        application,
                        user_id,
                        class_name,
                        class_date,
                        class_time,
                        True
                    )
                    logger.info(f"📱 Notifica Telegram inviata")
                except Exception as e:
                    logger.error(f"❌ Errore invio notifica: {e}")
                
                logger.info(f"🎉 Prenotazione #{booking_id} COMPLETATA!")
            else:
                logger.error(f"❌ Prenotazione #{booking_id} FALLITA!")
            
            logger.info(f"{'─'*60}")
        
        cur.close()
        conn.close()
        logger.info("✅ Controllo completato, database chiuso")
        
    except Exception as e:
        logger.error(f"❌ ERRORE durante controllo: {e}")
        logger.exception("Stack trace:")
    
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
        
        # Usa il chat_id specifico
        import asyncio
        asyncio.run(application.bot.send_message(chat_id=user_id, text=message))
        
    except Exception as e:
        logger.error(f"Errore invio notifica: {e}")


# ============================================================================
# HANDLER TELEGRAM (versione semplificata per debug)
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot EasyFit - Versione DEBUG\n\n"
        "Comandi:\n"
        "/test - Forza controllo prenotazioni ORA\n"
        "/status - Vedi prenotazioni pending\n"
        "/logs - Info scheduler"
    )


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forza controllo prenotazioni manualmente"""
    await update.message.reply_text("🔍 Forzando controllo prenotazioni...")
    
    try:
        check_and_book(context.application)
        await update.message.reply_text("✅ Controllo completato! Guarda i logs su Render.")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra stato prenotazioni"""
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
            await update.message.reply_text("📋 Nessuna prenotazione nel database")
            return
        
        message = "📊 STATO PRENOTAZIONI:\n\n"
        for b in bookings:
            status_emoji = "✅" if b[5] == "completed" else "⏳" if b[5] == "pending" else "❌"
            message += f"{status_emoji} #{b[0]} - {b[1]}\n"
            message += f"   📅 {b[2]} ore {b[3]}\n"
            message += f"   🔔 Prenota: {b[4]}\n"
            message += f"   Status: {b[5]}\n\n"
        
        await update.message.reply_text(message)
        
        cur.close()
        conn.close()
        
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


# ============================================================================
# HEALTH CHECK SERVER
# ============================================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot EasyFit DEBUG is running')
    
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


# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("")
    logger.info("="*70)
    logger.info("🚀 AVVIO BOT EASYFIT - VERSIONE DEBUG")
    logger.info("="*70)
    
    # Verifica variabili ambiente
    logger.info("🔍 Verifica configurazione:")
    logger.info(f"   TELEGRAM_TOKEN: {'✅ OK' if TELEGRAM_TOKEN else '❌ MANCANTE'}")
    logger.info(f"   DATABASE_URL: {'✅ OK' if DATABASE_URL else '❌ MANCANTE'}")
    logger.info(f"   EASYFIT_EMAIL: {EASYFIT_EMAIL if EASYFIT_EMAIL else '❌ MANCANTE'}")
    
    # Crea applicazione
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Aggiungi handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("status", status_command))
    
    # Configura scheduler
    logger.info("")
    logger.info("⏰ Configurazione scheduler:")
    logger.info("   Intervallo: ogni 2 minuti")
    logger.info("   Orario attivo: 8:00 - 21:00")
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: check_and_book(application),
        'cron',
        hour='8-21',
        minute='*/2',
        id='check_bookings',
        name='Controllo Prenotazioni'
    )
    scheduler.start()
    
    logger.info("✅ Scheduler avviato")
    
    # Avvia health server
    start_health_server()
    
    # Info finale
    logger.info("")
    logger.info("="*70)
    logger.info("✅ BOT PRONTO!")
    logger.info("="*70)
    logger.info("")
    logger.info("📝 Comandi disponibili su Telegram:")
    logger.info("   /test   - Forza controllo prenotazioni ORA")
    logger.info("   /status - Vedi stato prenotazioni")
    logger.info("")
    logger.info("🔍 Il prossimo controllo automatico sarà tra max 2 minuti...")
    logger.info("")
    
    # Avvia bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
