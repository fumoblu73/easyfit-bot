#!/usr/bin/env python3
"""
EasyFit Bot - Versione con Debug Database
Logging dettagliato per capire perché l'update non funziona
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

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


# ============================================================================
# FUNZIONE CHECK_AND_BOOK CON DEBUG DATABASE
# ============================================================================

def check_and_book(application):
    """Controlla e prenota - CON DEBUG DATABASE"""
    
    logger.info("="*60)
    logger.info("🔍 CONTROLLO PRENOTAZIONI")
    logger.info(f"⏰ Ora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Controllo orario
    current_hour = datetime.now().hour
    
    if not (8 <= current_hour < 21):
        logger.info(f"⏰ Fuori orario attivo (8-21). Ora: {current_hour}h")
        logger.info("="*60)
        return
    
    logger.info("✅ Dentro orario attivo (8-21)")
    
    # Connessione database
    conn = None
    cur = None
    
    try:
        logger.info("🔌 Connessione al database...")
        conn = get_db_connection()
        conn.autocommit = False  # Assicurati che autocommit sia disabilitato
        cur = conn.cursor()
        logger.info("✅ Database connesso (autocommit=False)")
    except Exception as e:
        logger.error(f"❌ Errore connessione DB: {e}")
        return
    
    try:
        now = datetime.now()
        
        # Cerca TUTTE le prenotazioni pending il cui booking_date è passato
        logger.info(f"🔎 Cerco prenotazioni pending con booking_date <= {now}")
        
        cur.execute(
            """
            SELECT id, user_id, class_name, class_date, class_time, booking_date, status
            FROM bookings
            WHERE status = 'pending'
            AND booking_date <= %s
            ORDER BY booking_date
            """,
            (now,)
        )
        
        bookings_to_make = cur.fetchall()
        
        logger.info(f"📊 Prenotazioni trovate: {len(bookings_to_make)}")
        
        if not bookings_to_make:
            logger.info("ℹ️  Nessuna prenotazione da effettuare")
            cur.close()
            conn.close()
            logger.info("="*60)
            return
        
        # Processa ogni prenotazione
        for booking in bookings_to_make:
            booking_id, user_id, class_name, class_date, class_time, booking_date, current_status = booking
            
            logger.info("")
            logger.info(f"{'─'*60}")
            logger.info(f"📝 PRENOTAZIONE #{booking_id}")
            logger.info(f"   📚 {class_name}")
            logger.info(f"   📅 {class_date} ore {class_time}")
            logger.info(f"   🔔 Booking date: {booking_date}")
            logger.info(f"   📍 Status corrente: {current_status}")
            
            # SIMULAZIONE
            logger.info(f"🔄 [SIMULAZIONE] Prenotazione in corso...")
            success = True
            
            if success:
                logger.info(f"✅ Prenotazione simulata OK")
                
                # === DEBUG DATABASE UPDATE ===
                logger.info(f"💾 Inizio UPDATE database...")
                logger.info(f"   UPDATE bookings SET status = 'completed' WHERE id = {booking_id}")
                
                try:
                    # Esegui UPDATE
                    cur.execute(
                        """
                        UPDATE bookings
                        SET status = 'completed'
                        WHERE id = %s
                        """,
                        (booking_id,)
                    )
                    
                    rows_affected = cur.rowcount
                    logger.info(f"   ✅ Query eseguita. Righe modificate: {rows_affected}")
                    
                    if rows_affected == 0:
                        logger.warning(f"   ⚠️  ATTENZIONE: Nessuna riga modificata!")
                    
                    # COMMIT ESPLICITO
                    logger.info(f"   💾 Eseguo COMMIT...")
                    conn.commit()
                    logger.info(f"   ✅ COMMIT completato!")
                    
                    # VERIFICA IMMEDIATA
                    logger.info(f"   🔍 Verifico aggiornamento...")
                    cur.execute(
                        """
                        SELECT status FROM bookings WHERE id = %s
                        """,
                        (booking_id,)
                    )
                    
                    new_status = cur.fetchone()[0]
                    logger.info(f"   📊 Status dopo update: {new_status}")
                    
                    if new_status == 'completed':
                        logger.info(f"   🎉 Verifica OK: status = 'completed'")
                    else:
                        logger.error(f"   ❌ ERRORE: Status ancora '{new_status}'!")
                    
                except psycopg2.Error as db_error:
                    logger.error(f"   ❌ ERRORE DATABASE: {db_error}")
                    logger.error(f"   Tipo errore: {type(db_error)}")
                    conn.rollback()
                    logger.error(f"   ROLLBACK eseguito")
                    continue
                except Exception as update_error:
                    logger.error(f"   ❌ ERRORE GENERICO: {update_error}")
                    logger.exception("   Stack trace:")
                    conn.rollback()
                    continue
                
                # Notifica Telegram
                try:
                    logger.info(f"📱 Invio notifica Telegram...")
                    send_telegram_notification(
                        application, user_id, class_name,
                        class_date, class_time, True
                    )
                    logger.info(f"   ✅ Notifica inviata")
                except Exception as e:
                    logger.error(f"   ❌ Errore notifica: {e}")
                
                logger.info(f"🎉 Prenotazione #{booking_id} COMPLETATA")
            else:
                logger.error(f"❌ Prenotazione #{booking_id} FALLITA")
            
            logger.info(f"{'─'*60}")
        
        # Chiusura connessione
        logger.info("🔒 Chiusura connessione database...")
        cur.close()
        conn.close()
        logger.info("✅ Connessione chiusa")
        
    except Exception as e:
        logger.error(f"❌ ERRORE GENERALE: {e}")
        logger.exception("Stack trace completo:")
        if conn:
            try:
                conn.rollback()
                logger.error("ROLLBACK eseguito")
            except:
                pass
            try:
                conn.close()
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
        logger.error(f"Errore invio notifica: {e}")


# ============================================================================
# HANDLER TELEGRAM
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot EasyFit - Debug Database\n\n"
        "Comandi:\n"
        "/test - Forza controllo prenotazioni\n"
        "/status - Stato prenotazioni\n"
        "/reset4 - Reset record #4 a pending"
    )


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forza controllo manuale"""
    await update.message.reply_text("🔍 Controllo prenotazioni in corso...")
    
    try:
        check_and_book(context.application)
        await update.message.reply_text(
            "✅ Fatto!\n\n"
            "Controlla i logs su Render per dettagli.\n"
            "Poi ricontrolla su Supabase (ricarica pagina)"
        )
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
            ORDER BY id DESC
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


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset record #4 a pending per test"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE bookings
            SET status = 'pending'
            WHERE id = 4
        """)
        
        conn.commit()
        
        await update.message.reply_text("✅ Record #4 resettato a 'pending'")
        
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
    logger.info("🚀 EASYFIT BOT - DEBUG DATABASE")
    logger.info("="*70)
    logger.info(f"📊 DATABASE_URL presente: {'✅' if DATABASE_URL else '❌'}")
    
    # Crea applicazione
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handler
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("reset4", reset_command))
    
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
    logger.info("✅ BOT PRONTO - DEBUG MODE")
    logger.info("="*70)
    logger.info("")
    
    # Avvia
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
