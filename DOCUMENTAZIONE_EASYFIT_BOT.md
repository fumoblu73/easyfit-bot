# 📚 Documentazione Completa - EasyFit Bot

**Progetto**: Bot Telegram per prenotazioni automatiche lezioni EasyFit  
**Versione**: 2.0  
**Data**: Novembre 2025  
**Autore**: Emanuele (fumoblu73)

---

## 📋 Indice

1. [Panoramica del Progetto](#panoramica-del-progetto)
2. [Architettura del Sistema](#architettura-del-sistema)
3. [Servizi Utilizzati](#servizi-utilizzati)
4. [Workflow Completo](#workflow-completo)
5. [Setup Iniziale](#setup-iniziale)
6. [Configurazione Dettagliata](#configurazione-dettagliata)
7. [Funzionalità del Bot](#funzionalità-del-bot)
8. [Manutenzione e Troubleshooting](#manutenzione-e-troubleshooting)
9. [Costi e Limiti](#costi-e-limiti)

---

## 🎯 Panoramica del Progetto

### Problema
Le palestre EasyFit permettono di prenotare le lezioni **solo 72 ore prima**. Per le lezioni più popolari, è necessario prenotare esattamente al momento dell'apertura delle prenotazioni, altrimenti i posti finiscono rapidamente.

### Soluzione
Un bot Telegram che:
- Prenota automaticamente le lezioni **esattamente 72 ore prima**
- Funziona 24/7 su cloud (non richiede dispositivo acceso)
- Gestisce prenotazioni multiple per più utenti
- Supporta liste d'attesa quando i posti sono esauriti

### Risultato
✅ **100% automatico** - nessun intervento manuale necessario  
✅ **Affidabile** - prenotazioni eseguite con precisione  
✅ **Gratuito** - 0€/mese di costi  
✅ **Multi-utente** - utilizzabile da più persone contemporaneamente

---

## 🏗️ Architettura del Sistema

```
┌─────────────────┐
│  UTENTE         │
│  (Telegram)     │
└────────┬────────┘
         │
         │ Comandi (/prenota, /lista)
         │
         ▼
┌─────────────────────────────────────┐
│  BOT TELEGRAM                       │
│  (Python - python-telegram-bot)     │
│  ┌───────────────────────────────┐ │
│  │ • Interface utente            │ │
│  │ • Gestione comandi            │ │
│  │ • Callback handlers           │ │
│  └───────────────────────────────┘ │
└─────────────┬───────────────────────┘
              │
              ├──────────────────┐
              │                  │
              ▼                  ▼
    ┌──────────────────┐  ┌─────────────────┐
    │  DATABASE        │  │  SCHEDULER      │
    │  (Supabase       │  │  (APScheduler)  │
    │   PostgreSQL)    │  │                 │
    │                  │  │  • Check ogni   │
    │  • Bookings      │  │    minuto       │
    │  • User data     │  │  • Esegue       │
    │  • Status        │  │    prenotazioni │
    └──────────────────┘  └────────┬────────┘
                                   │
                                   ▼
                          ┌─────────────────┐
                          │  EASYFIT API    │
                          │                 │
                          │  • Login        │
                          │  • Calendario   │
                          │  • Prenotazioni │
                          └─────────────────┘
```

### Flusso di una Prenotazione

```
1. UTENTE → Telegram: /prenota
   ↓
2. BOT → Database: Salva prenotazione (status: pending)
   ↓
3. SCHEDULER (ogni minuto):
   - Controlla database per prenotazioni da eseguire
   - Se mancano <= 0 ore a 72h prima della lezione:
     ↓
4. BOT → EasyFit API: Login
   ↓
5. BOT → EasyFit API: Recupera calendario lezioni
   ↓
6. BOT → EasyFit API: Prenota lezione
   ↓
7. BOT → Database: Aggiorna status (completed/waitlisted/failed)
   ↓
8. BOT → Telegram: Notifica utente risultato
```

---

## 🛠️ Servizi Utilizzati

### 1. **GitHub** (Controllo Versione)
- **Costo**: Gratuito
- **Utilizzo**: Repository del codice
- **Repository**: `fumoblu73/easyfit-bot`
- **Branch principale**: `main`
- **Link**: https://github.com/fumoblu73/easyfit-bot

**Contenuti del repository**:
```
easyfit-bot/
├── bot.py              # Codice principale del bot
├── requirements.txt    # Dipendenze Python
├── runtime.txt         # Versione Python (3.11.10)
├── .python-version     # Versione locale (3.11.9)
├── README.md           # Documentazione base
└── .gitignore          # File da ignorare
```

---

### 2. **Telegram Bot** (Interfaccia Utente)
- **Costo**: Gratuito
- **Utilizzo**: Interfaccia per gli utenti
- **Bot**: @EasyfitBookingBot
- **Token**: `8453565301:AAEdeoRAUBo8MDJglwhlKT92QQA8ii0e-Kc`
- **Creato con**: @BotFather su Telegram

**Come creare un nuovo bot Telegram**:
1. Apri Telegram e cerca `@BotFather`
2. Invia `/newbot`
3. Segui le istruzioni (nome bot, username)
4. Salva il **token** fornito

---

### 3. **Supabase** (Database PostgreSQL)
- **Costo**: Gratuito (fino a 500MB)
- **Utilizzo**: Memorizzazione prenotazioni
- **Piano**: Free tier
- **Link**: https://supabase.com

**Schema Database**:
```sql
CREATE TABLE bookings (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    class_name VARCHAR(100) NOT NULL,
    class_date DATE NOT NULL,
    class_time TIME NOT NULL,
    booking_date TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indici per performance
CREATE INDEX idx_user_id ON bookings(user_id);
CREATE INDEX idx_status ON bookings(status);
CREATE INDEX idx_booking_date ON bookings(booking_date);
```

**Setup Supabase**:
1. Crea account su https://supabase.com
2. Crea nuovo progetto
3. Vai su **SQL Editor**
4. Esegui lo script SQL sopra
5. Copia il **Connection String** da Settings → Database

**Connection String Format**:
```
postgresql://postgres.[project-ref]:[password]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres?sslmode=require
```

---

### 4. **Koyeb** (Hosting Cloud)
- **Costo**: Gratuito (piano Hobby)
- **Utilizzo**: Esecuzione bot 24/7
- **Piano**: Hobby (1 Web Service gratuito)
- **Istanza**: Nano (512MB RAM, 0.1 vCPU)
- **Link**: https://app.koyeb.com

**Specifiche tecniche**:
- **Region**: Frankfurt (fra)
- **Instance Type**: Nano (FREE)
- **Auto-scaling**: Disabilitato (1 istanza fissa)
- **Health Check**: TCP su porta 8000
- **Auto-deploy**: Attivato (deploy automatico al push su GitHub)

---

### 5. **UptimeRobot** (Monitoraggio)
- **Costo**: Gratuito
- **Utilizzo**: Verifica che il bot sia sempre online
- **Intervallo**: Check ogni 5 minuti
- **Tipo**: HTTP(s) Monitor
- **Link**: https://uptimerobot.com

**Configurazione**:
- **Monitor Type**: HTTP(s)
- **URL**: `https://[your-koyeb-url].koyeb.app/`
- **Monitoring Interval**: 5 minuti
- **Alert Contacts**: Email quando il servizio è down

---

### 6. **EasyFit API** (Sistema Prenotazioni)
- **Costo**: Incluso nell'abbonamento palestra
- **Utilizzo**: API per prenotare lezioni
- **Base URL**: `https://app-easyfitpalestre.it`
- **Autenticazione**: Email + Password

**Credenziali utilizzate**:
- **Email**: `cinzia.caia@hotmail.it`
- **Password**: `Agrigento9965`
- **Organization ID**: `1216915380`

**API Endpoints utilizzati**:
```
POST /studio/auth/login
GET  /calendar/items
POST /calendar/item/{id}/booking
POST /calendar/item/{id}/waiting-list-booking
```

---

## ⚙️ Workflow Completo

### Fase 1: Setup Iniziale (Una tantum)

#### 1.1 Creazione Repository GitHub
```bash
# Crea repository su GitHub
1. Vai su https://github.com/new
2. Nome: easyfit-bot
3. Visibilità: Public
4. Crea repository

# Carica i file
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/fumoblu73/easyfit-bot.git
git push -u origin main
```

#### 1.2 Creazione Bot Telegram
```
1. Apri Telegram → cerca @BotFather
2. /newbot
3. Nome: EasyFit Booking Bot
4. Username: EasyfitBookingBot (o simile)
5. Salva TOKEN ricevuto
```

#### 1.3 Setup Database Supabase
```
1. Registrati su https://supabase.com
2. Crea progetto: "easyfit-bot"
3. SQL Editor → esegui schema database
4. Settings → Database → copia Connection String
```

#### 1.4 Deploy su Koyeb
```
1. Registrati su https://app.koyeb.com
2. Crea nuova organizzazione (piano Hobby - FREE)
3. Clicca "Create Service"
4. Seleziona "GitHub"
5. Connetti repository: fumoblu73/easyfit-bot
6. Branch: main
7. Instance Type: Nano (FREE)
8. Region: Frankfurt
```

**Variabili d'ambiente da configurare**:
```
TELEGRAM_TOKEN=8453565301:AAEdeoRAUBo8MDJglwhlKT92QQA8ii0e-Kc
DATABASE_URL=postgresql://postgres.[ref]:[password]@...
EASYFIT_EMAIL=cinzia.caia@hotmail.it
EASYFIT_PASSWORD=Agrigento9965
PORT=8000
```

**Configurazione Build**:
- **Run command**: `python bot.py` (lasciare vuoto, Koyeb lo rileva automaticamente)
- **Health check**: TCP su porta 8000

---

### Fase 2: Utilizzo Quotidiano

#### 2.1 Prenotare una Lezione
```
1. Apri Telegram → cerca @EasyfitBookingBot
2. /start
3. /prenota
4. Scegli tipo lezione (es. Pilates Matwork)
5. Scegli data
6. Scegli orario
7. ✅ Prenotazione programmata!
```

**Cosa succede dietro le quinte**:
1. Bot salva la prenotazione nel database con status `pending`
2. Calcola l'orario esatto (72 ore prima della lezione)
3. Ogni minuto lo scheduler controlla il database
4. Quando arriva il momento giusto:
   - Login su EasyFit
   - Recupera calendario lezioni
   - Trova la lezione specifica
   - Tenta la prenotazione
   - Se piena → prova lista d'attesa
   - Aggiorna database
   - Invia notifica Telegram

#### 2.2 Vedere Prenotazioni Programmate
```
/lista
```

Mostra:
- ⏳ **PROGRAMMATE**: prenotazioni che devono ancora essere eseguite
- ✅ **PRENOTATE**: prenotazioni completate con successo
- 📋 **LISTA D'ATTESA**: prenotazioni in lista d'attesa

**Nota**: Vengono mostrate solo le lezioni future, quelle già svolte vengono automaticamente nascoste.

#### 2.3 Cancellare una Prenotazione
```
/cancella <ID>
```

Esempio: `/cancella 42`

**⚠️ Importante**: Questo cancella solo la prenotazione nel database del bot, **non** la prenotazione su EasyFit. Per cancellare su EasyFit devi farlo manualmente dall'app.

---

### Fase 3: Indicatori di Stato delle Lezioni

Quando selezioni una lezione e un orario, il bot mostra:

#### 🟢 **Prenotabile** (> 72 ore)
La lezione è oltre le 72 ore, quindi prenotabile dal bot.
```
🕐 09:15 • Chiara Basile (🟢 Prenotabile)
```

#### ✅ **Posti liberi** (< 72 ore, disponibilità)
La lezione è entro 72 ore e ci sono posti disponibili.
```
🕐 10:30 • Fabio Aloscari (✅ Posti liberi)
```

#### ⏳ **Lista d'attesa** (< 72 ore, piena ma con lista)
La lezione è piena ma c'è posto in lista d'attesa.
```
🕐 11:15 • Silvia Gozzi (⏳ Lista d'attesa)
```

#### 🚫 **Completa** (< 72 ore, tutto pieno)
La lezione è completamente piena, anche la lista d'attesa.
```
🕐 18:00 • Gianluca Rossi (🚫 Completa)
```

---

## 🔧 Configurazione Dettagliata

### File: `bot.py`

**Struttura del codice**:

```python
# 1. IMPORTS E CONFIGURAZIONE
import os
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
import requests

# 2. VARIABILI D'AMBIENTE
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
EASYFIT_EMAIL = os.getenv('EASYFIT_EMAIL')
EASYFIT_PASSWORD = os.getenv('EASYFIT_PASSWORD')

# 3. DATABASE CONNECTION POOL
db_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=1,
    maxconn=5,
    dsn=DATABASE_URL
)

# 4. FUNZIONI EASYFIT API
def login_easyfit()
def get_calendar(session, start_date, end_date)
def book_class(session, class_id)

# 5. COMMAND HANDLERS
async def start(update, context)      # /start
async def prenota(update, context)    # /prenota
async def lista(update, context)      # /lista
async def cancella(update, context)   # /cancella
async def help_command(update, context) # /help

# 6. CALLBACK HANDLERS
async def class_selected(update, context)
async def date_selected(update, context)
async def time_selected(update, context)

# 7. SCHEDULER
def check_and_book(application)

# 8. HEALTH CHECK SERVER
class HealthCheckHandler(BaseHTTPRequestHandler)
def run_health_server()

# 9. MAIN
def main()
```

---

### File: `requirements.txt`

```
python-telegram-bot==20.7
APScheduler==3.10.4
psycopg2-binary==2.9.9
requests==2.31.0
python-dotenv==1.0.0
nest_asyncio==1.6.0
```

**Spiegazione dipendenze**:
- `python-telegram-bot`: Libreria per interagire con Telegram Bot API
- `APScheduler`: Scheduler per eseguire task periodici (controllo prenotazioni)
- `psycopg2-binary`: Driver PostgreSQL per Python
- `requests`: HTTP client per chiamare API EasyFit
- `python-dotenv`: Gestione variabili d'ambiente (per sviluppo locale)
- `nest_asyncio`: Supporto per event loop annidati

---

### File: `runtime.txt`

```
python-3.11.10
```

Specifica la versione Python per Koyeb.

---

## 📱 Funzionalità del Bot

### Comandi Telegram

| Comando | Descrizione | Esempio |
|---------|-------------|---------|
| `/start` | Avvia il bot e mostra messaggio di benvenuto | `/start` |
| `/prenota` | Programma una nuova prenotazione | `/prenota` |
| `/lista` | Mostra tutte le prenotazioni future | `/lista` |
| `/cancella <ID>` | Cancella una prenotazione programmata | `/cancella 42` |
| `/help` | Mostra guida completa | `/help` |

---

### Logica delle Prenotazioni

#### Timing: 72 Ore Esatte
```python
# Esempio: lezione venerdì 15/11 ore 18:00
class_datetime = datetime(2025, 11, 15, 18, 0)  # Ora italiana

# Calcola 72 ore prima
booking_datetime = class_datetime - timedelta(hours=72)
# = martedì 12/11 ore 18:00 (ora italiana)

# Converti in UTC per salvare nel database
booking_datetime_utc = booking_datetime - timedelta(hours=1)
# = martedì 12/11 ore 17:00 (UTC)
```

#### Scheduler: Controllo Ogni Minuto
```python
# Lo scheduler gira dalle 8 alle 21 UTC (9-22 ora italiana)
scheduler.add_job(
    check_and_book,
    'cron',
    hour='8-21',  # UTC
    minute='*',   # Ogni minuto
    id='check_bookings'
)
```

**Perché solo 8-21 UTC?**
- Le prenotazioni EasyFit si aprono dalle 9:00 alle 22:00 ora italiana
- 9:00 ITA = 8:00 UTC
- 22:00 ITA = 21:00 UTC
- Risparmio risorse fuori dagli orari utili

#### Gestione Posti Pieni e Liste d'Attesa
```python
# 1. Tenta prenotazione normale
response = book_class(session, class_id)

if response.status_code == 200:
    # ✅ Prenotazione riuscita!
    status = 'completed'
    
elif "lista d'attesa" in response.text:
    # ⏳ Piena, prova lista d'attesa
    response = book_waitlist(session, class_id)
    
    if response.status_code == 200:
        status = 'waitlisted'
    else:
        status = 'failed'
else:
    # ❌ Errore generico
    status = 'failed'

# Aggiorna database
UPDATE bookings SET status = status WHERE id = booking_id
```

---

### Stati delle Prenotazioni

| Status | Descrizione | Visualizzazione |
|--------|-------------|-----------------|
| `pending` | In attesa di essere eseguita | ⏳ PROGRAMMATE |
| `completed` | Prenotazione riuscita | ✅ PRENOTATE |
| `waitlisted` | In lista d'attesa | 📋 LISTA D'ATTESA |
| `failed` | Fallita (lezione non trovata o errore) | Non mostrata |

---

## 🔍 Manutenzione e Troubleshooting

### Monitoraggio

#### 1. Dashboard Koyeb
**URL**: https://app.koyeb.com/services/easyfit-bot

**Metriche da controllare**:
- **Status**: Deve essere "Healthy" (verde)
- **CPU**: Normalmente < 5%
- **RAM**: Circa 200-300MB / 512MB
- **Deployment**: Ultima build deve essere "Successful"

#### 2. Log di Koyeb
**Come accedere**:
1. Dashboard Koyeb → Service "easyfit-bot"
2. Tab "Logs"
3. Filtra per livello: INFO, WARNING, ERROR

**Log importanti da cercare**:
```
✅ Login OK! SessionID: xxx        # Login EasyFit riuscito
🎉 Prenotazione #X completata      # Prenotazione eseguita
⚠️ In ritardo di X minuti          # Prenotazione eseguita in ritardo
❌ Errore                           # Problema generico
```

#### 3. Database Supabase
**Come accedere**:
1. https://supabase.com → Progetto "easyfit-bot"
2. Table Editor → `bookings`

**Query utili**:
```sql
-- Prenotazioni pending (da eseguire)
SELECT * FROM bookings WHERE status = 'pending' ORDER BY booking_date;

-- Prenotazioni completate oggi
SELECT * FROM bookings WHERE status = 'completed' 
AND created_at::date = CURRENT_DATE;

-- Prenotazioni fallite nell'ultima settimana
SELECT * FROM bookings WHERE status = 'failed' 
AND created_at > NOW() - INTERVAL '7 days';
```

---

### Problemi Comuni e Soluzioni

#### ❌ Bot non risponde su Telegram

**Diagnosi**:
1. Controlla status su Koyeb → deve essere "Healthy"
2. Verifica log Koyeb → cerca errori all'avvio

**Soluzioni**:
```bash
# Se Koyeb è "Unhealthy"
1. Koyeb Dashboard → "Redeploy"
2. Attendi 2-3 minuti
3. Verifica status torna "Healthy"

# Se persiste
1. Controlla variabili d'ambiente
2. TELEGRAM_TOKEN deve essere corretto
3. DATABASE_URL deve essere valido
```

---

#### ❌ Prenotazione non eseguita

**Diagnosi**:
1. Controlla database → status della prenotazione
2. Verifica log Koyeb nell'orario previsto (72h prima)

**Possibili cause**:
```
Causa 1: Lezione non trovata
→ Log: "❌ Lezione non trovata"
→ Soluzione: Nome lezione errato o lezione non disponibile quel giorno

Causa 2: Login EasyFit fallito
→ Log: "❌ Login fallito"
→ Soluzione: Controlla credenziali in variabili d'ambiente

Causa 3: Prenotazione in ritardo
→ Log: "⚠️ In ritardo di X minuti"
→ Soluzione: Bot era offline → controlla Koyeb uptime

Causa 4: Lista d'attesa completa
→ Log: "✅ IN LISTA D'ATTESA!" poi status 'waitlisted'
→ Soluzione: Normale, sei in lista d'attesa
```

---

#### ❌ Errore database "SSL connection"

**Causa**: Connessione Supabase interrotta

**Soluzione**:
```python
# Il bot ha già retry automatico
# Se persiste:
1. Verifica DATABASE_URL corretto
2. Controlla Supabase non sia in manutenzione
3. Redeploy su Koyeb
```

---

#### ❌ "Health check failed" su Koyeb

**Causa**: Koyeb non riesce a connettersi alla porta 8000

**Soluzione**:
```bash
# Verifica health check configurato correttamente:
1. Koyeb → Settings → Health checks
2. Type: TCP
3. Port: 8000
4. Path: / (o vuoto)

# Se persiste → Redeploy
```

---

### Aggiornamenti del Codice

#### Come aggiornare il bot:

**Metodo 1: Via GitHub Web**:
```
1. Vai su https://github.com/fumoblu73/easyfit-bot
2. Clicca su bot.py
3. Clicca icona matita (Edit)
4. Modifica il codice
5. "Commit changes"
6. Koyeb farà auto-deploy (2-3 minuti)
```

**Metodo 2: Via Git CLI**:
```bash
# Scarica repository
git clone https://github.com/fumoblu73/easyfit-bot.git
cd easyfit-bot

# Modifica bot.py
nano bot.py  # o qualsiasi editor

# Commit e push
git add bot.py
git commit -m "Descrizione modifica"
git push origin main

# Koyeb farà auto-deploy automaticamente
```

**Verifica deploy**:
```
1. Koyeb Dashboard → "Activity"
2. Attendi "Deployment successful"
3. Status torna "Healthy"
4. Testa su Telegram
```

---

## 💰 Costi e Limiti

### Costi Mensili

| Servizio | Piano | Costo | Limite |
|----------|-------|-------|--------|
| **GitHub** | Free | €0 | Repo pubblici illimitati |
| **Telegram** | - | €0 | Bot illimitati |
| **Supabase** | Free | €0 | 500MB database, 2GB bandwidth |
| **Koyeb** | Hobby | €0 | 1 Web Service, 5GB traffic |
| **UptimeRobot** | Free | €0 | 50 monitor, check ogni 5 min |
| | | | |
| **TOTALE** | | **€0/mese** | |

### Limiti Tecnici

#### Koyeb (Piano Hobby)
- ✅ 1 Web Service gratuito
- ✅ Instance Nano (512MB RAM, 0.1 vCPU)
- ✅ ~5GB traffico/mese
- ✅ Deploy illimitati
- ❌ No custom domain (ma non serve)
- ❌ No auto-scaling (ma non serve)

**Traffico stimato per il bot**:
```
- Controllo prenotazioni: 1 richiesta/minuto × 13 ore/giorno = 780 req/giorno
- API EasyFit: ~10 richieste per prenotazione
- Telegram polling: ~1MB/ora
- Totale: ~50MB/giorno = ~1.5GB/mese

✅ Ben dentro il limite di 5GB
```

#### Supabase (Piano Free)
- ✅ 500MB database (ampiamente sufficiente)
- ✅ 2GB egress/mese
- ✅ 50,000 richieste/mese
- ✅ Backup automatici

**Spazio database stimato**:
```
- 1 prenotazione ≈ 200 bytes
- 1000 prenotazioni ≈ 200KB
- Storage di 1 anno ≈ 10MB

✅ Ben dentro il limite di 500MB
```

---

## 🚀 Replicare il Progetto da Zero

### Prerequisiti
- Account GitHub
- Account Telegram
- Email valida per registrazioni

### Tempo necessario
⏱️ **30-45 minuti** (setup completo)

---

### Step 1: GitHub (5 minuti)

```bash
1. Crea account su https://github.com
2. Crea nuovo repository:
   - Nome: easyfit-bot
   - Visibilità: Public
   - ✅ Add README
   
3. Carica i file:
   - bot.py
   - requirements.txt
   - runtime.txt
   - .gitignore
```

---

### Step 2: Telegram Bot (2 minuti)

```
1. Apri Telegram
2. Cerca @BotFather
3. Invia: /newbot
4. Nome: [Scegli un nome]
5. Username: [username]_bot
6. 💾 SALVA IL TOKEN ricevuto
```

---

### Step 3: Supabase Database (10 minuti)

```bash
1. Vai su https://supabase.com
2. "Start your project" → Crea account
3. "New project":
   - Name: easyfit-bot
   - Database Password: [scegli password sicura] 💾 SALVALA
   - Region: Europe (Frankfurt)
   - Plan: Free
   
4. Attendi creazione progetto (2-3 min)

5. SQL Editor → New query → esegui:
```

```sql
CREATE TABLE bookings (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    class_name VARCHAR(100) NOT NULL,
    class_date DATE NOT NULL,
    class_time TIME NOT NULL,
    booking_date TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_user_id ON bookings(user_id);
CREATE INDEX idx_status ON bookings(status);
CREATE INDEX idx_booking_date ON bookings(booking_date);
```

```bash
6. Settings → Database → Connection string (URI)
7. 💾 COPIA e SALVA la stringa completa
   Formato: postgresql://postgres.[ref]:[password]@...
```

---

### Step 4: Koyeb Hosting (15 minuti)

```bash
1. Vai su https://app.koyeb.com
2. Crea account
3. Crea organizzazione (quando richiesto):
   - Nome: [scegli nome]
   - Piano: Hobby (FREE)
   
4. "Create Service"

5. Connetti GitHub:
   - "Install GitHub app"
   - Autorizza accesso
   - Seleziona: All repositories
   - Save
   
6. Configura servizio:
   - Source: GitHub → [tuo-username]/easyfit-bot
   - Branch: main
   - Builder: Buildpack
   - Instance: Nano (FREE) ✅
   - Region: Frankfurt
   
7. Environment Variables (clicca +Add variable):
```

```bash
TELEGRAM_TOKEN
[il token da BotFather]

DATABASE_URL
[la stringa da Supabase]

EASYFIT_EMAIL
[tua email EasyFit]

EASYFIT_PASSWORD
[tua password EasyFit]

PORT
8000
```

```bash
8. Health checks:
   - Type: TCP
   - Port: 8000
   
9. Service name: easyfit-bot

10. 🚀 DEPLOY!

11. Attendi 3-5 minuti → Status: Healthy ✅
```

---

### Step 5: UptimeRobot (5 minuti)

```bash
1. Vai su https://uptimerobot.com
2. Crea account Free
3. "Add New Monitor":
   - Monitor Type: HTTP(s)
   - Friendly Name: EasyFit Bot
   - URL: [il tuo URL Koyeb].koyeb.app
   - Monitoring Interval: 5 minutes
   - Alert Contacts: [la tua email]
   
4. "Create Monitor" ✅
```

---

### Step 6: Test Finale (5 minuti)

```bash
✅ CHECKLIST DI VERIFICA:

1. Koyeb Dashboard → Status = "Healthy" 🟢
2. Logs Koyeb → vedi "✅ BOT PRONTO E OPERATIVO!"
3. Telegram → cerca il tuo bot
4. Invia: /start
5. Risponde? ✅ FUNZIONA!
6. Prova: /prenota
7. Vedi calendario lezioni? ✅ API FUNZIONA!
8. Prova: /lista
9. Database funziona? ✅ TUTTO OK!
```

---

## 📞 Supporto e Contatti

### Repository GitHub
https://github.com/fumoblu73/easyfit-bot

### Issues
Per segnalare bug o richiedere funzionalità:
https://github.com/fumoblu73/easyfit-bot/issues

### Documentazione Servizi
- **Telegram Bot API**: https://core.telegram.org/bots/api
- **Koyeb Docs**: https://www.koyeb.com/docs
- **Supabase Docs**: https://supabase.com/docs
- **APScheduler**: https://apscheduler.readthedocs.io

---

## 📄 Licenza

Questo progetto è rilasciato sotto licenza MIT.

```
MIT License

Copyright (c) 2025 Emanuele (fumoblu73)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 🎓 Appendice: Concetti Tecnici

### A. Connection Pooling

**Cos'è**: Mantenere un pool di connessioni database riutilizzabili invece di aprire/chiudere ogni volta.

**Perché**: 
- ✅ Performance migliori
- ✅ Gestione automatica delle connessioni corrotte
- ✅ Retry automatico su errori temporanei

```python
db_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=1,      # Minimo 1 connessione sempre aperta
    maxconn=5,      # Massimo 5 connessioni simultanee
    dsn=DATABASE_URL
)
```

---

### B. Event Loop e Asyncio

**Cos'è**: Gestione asincrona delle operazioni I/O.

**Perché**: Telegram Bot API è asincrona, permette di gestire più utenti contemporaneamente senza bloccare.

```python
async def prenota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Questa funzione può essere messa "in pausa" mentre aspetta risposta
    await update.message.reply_text("Caricamento...")
    
    # Durante questo await, il bot può gestire altri utenti
    courses = get_courses()  
    
    await update.message.reply_text("Ecco le lezioni!")
```

---

### C. Timezone Handling

**Problema**: Il server (Koyeb) usa UTC, l'Italia usa CET/CEST (UTC+1/+2).

**Soluzione**: Salvare sempre in UTC, convertire per la visualizzazione.

```python
# Input utente: "15/11/2025 ore 18:00" (ora italiana)
user_time = datetime(2025, 11, 15, 18, 0)  # naive (senza timezone)

# Calcola 72 ore prima (in ora italiana)
booking_time_ita = user_time - timedelta(hours=72)

# Converti in UTC per salvare nel database
booking_time_utc = booking_time_ita - timedelta(hours=1)  # -1 per CET
booking_time_utc = booking_time_utc.replace(tzinfo=timezone.utc)

# Salva nel database
INSERT INTO bookings (booking_date) VALUES (booking_time_utc)

# Quando visualizzi all'utente, riconverti in ora italiana
booking_time_display = booking_time_utc + timedelta(hours=1)
```

---

### D. Health Checks

**Cos'è**: Koyeb verifica periodicamente che il bot risponda.

**Come funziona**:
```python
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Risponde "OK" alle richieste HTTP GET su porta 8000
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    
    def do_HEAD(self):
        # Risponde anche alle richieste HEAD (usate da Koyeb)
        self.send_response(200)
        self.end_headers()
```

**Perché HEAD**: Koyeb usa HEAD requests (più leggere) per verificare il servizio senza scaricare dati.

---

### E. API Reverse Engineering

**Cos'è**: Analizzare come funziona l'API EasyFit osservando le richieste dell'app web.

**Come è stato fatto**:
```bash
1. Apri sito EasyFit con Chrome DevTools
2. Network tab → filtra per XHR/Fetch
3. Fai login → osserva richiesta POST /auth/login
4. Prenota lezione → osserva richiesta POST /booking
5. Copia headers, body, parametri
6. Replica in Python con requests
```

**Headers importanti**:
```python
headers = {
    'Authorization': f'Bearer {session_token}',
    'Content-Type': 'application/json',
    'X-Organization-Unit-Id': '1216915380'
}
```

---

## 📚 Glossario

| Termine | Significato |
|---------|-------------|
| **API** | Application Programming Interface - modo per comunicare tra software |
| **Bot** | Software automatizzato che interagisce con utenti |
| **Callback** | Funzione chiamata in risposta a un evento |
| **Cron** | Sistema per schedulare task periodici |
| **Deploy** | Pubblicare il codice su un server |
| **Egress** | Traffico in uscita da un server |
| **Handler** | Funzione che gestisce un tipo specifico di richiesta |
| **Health Check** | Verifica automatica che un servizio sia funzionante |
| **Hook** | Punto di estensione nel codice |
| **Instance** | Singola istanza di un server/applicazione |
| **Payload** | Dati effettivi trasmessi in una richiesta |
| **Polling** | Controllo periodico per nuovi dati |
| **Pool** | Insieme di risorse riutilizzabili |
| **Repository** | Archivio di codice sorgente |
| **REST API** | Tipo di API basata su HTTP |
| **Scheduler** | Sistema che esegue task a orari prestabiliti |
| **Session** | Contesto mantenuto tra richieste multiple |
| **SSL/TLS** | Protocollo per connessioni sicure |
| **Token** | Chiave di autenticazione |
| **UTC** | Coordinated Universal Time - fuso orario di riferimento |
| **Webhook** | Notifica automatica via HTTP |

---

## 🔄 Changelog

### v2.0 - Novembre 2025
- ✅ Migrazione da Render a Koyeb
- ✅ Aggiunto nome istruttore negli orari
- ✅ Migliorati indicatori di stato (Prenotabile, Posti liberi, Lista d'attesa, Completa)
- ✅ Filtro automatico lezioni passate in `/lista`
- ✅ Ottimizzato connection pool database
- ✅ Migliorata gestione timezone

### v1.0 - Ottobre 2025
- ✅ Prima versione funzionante
- ✅ Prenotazioni automatiche 72 ore prima
- ✅ Supporto lista d'attesa
- ✅ Comandi Telegram base
- ✅ Deploy su Render

---

## ✨ Conclusioni

Questo bot rappresenta una soluzione completa, robusta e **completamente gratuita** per automatizzare le prenotazioni delle lezioni in palestra.

### Punti di forza:
- ✅ **100% automatico** - zero intervento manuale
- ✅ **Affidabile** - funziona 24/7 su cloud
- ✅ **Gratuito** - 0€/mese di costi
- ✅ **Scalabile** - può gestire molti utenti
- ✅ **Manutenibile** - codice chiaro e documentato
- ✅ **Replicabile** - può essere adattato per altre palestre

### Possibili miglioramenti futuri:
- 📧 Notifiche email oltre a Telegram
- 📊 Dashboard web per statistiche
- 🔔 Notifiche quando si libera un posto
- 👥 Gestione gruppi/famiglie
- 📱 App mobile nativa
- 🌐 Supporto multi-palestra

---

**Buona fortuna con la replica del progetto! 🚀**

*Per domande o supporto, apri una issue su GitHub.*