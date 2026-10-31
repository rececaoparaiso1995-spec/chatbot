"""
config.py — Configurações globais do projeto Paraíso Villas & Spa Bot
Todas as constantes, caminhos e parâmetros configuram-se aqui.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Diretórios base
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
AUDIT_SCREENSHOTS_DIR = DATA_DIR / "audit_screenshots"
CHROME_PROFILE_DIR = DATA_DIR / "chrome_profiles" / "whatsapp"
CHROME_PMS_PROFILE_DIR = DATA_DIR / "chrome_profiles" / "newhotel"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "hotel_bot.db"

# Criar diretórios se não existirem (seguro chamar múltiplas vezes)
DATA_DIR.mkdir(exist_ok=True)
AUDIT_SCREENSHOTS_DIR.mkdir(exist_ok=True)
CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
CHROME_PMS_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# URLs do PMS Newhotel Cloud
# ---------------------------------------------------------------------------
NEWHOTEL_URL = "https://www.newhotelcloud.com/#/clubeparaiso/pms/(rateslookup)"
NEWHOTEL_BASE_URL = "https://www.newhotelcloud.com"

# ---------------------------------------------------------------------------
# Opções de Pensão (regime alimentar)
# ---------------------------------------------------------------------------
PENSION_BB = "BB"  # Alojamento + Pequeno-Almoço (Bed & Breakfast)
PENSION_RO = "RO"  # Apenas Alojamento (Room Only)

# Texto que aparece no dropdown do Newhotel para cada regime
PENSION_LABELS = {
    PENSION_BB: "Alojamento + Pequeno-Almoço",
    PENSION_RO: "Alojamento",
}

# ---------------------------------------------------------------------------
# Capacidades das tipologias de alojamento
# ---------------------------------------------------------------------------
TIPOLOGIA_CAPACITY = {
    "T0": {"max_pax": 2, "max_children": 0},   # Máx 2 adultos, sem crianças
    "T1": {"max_pax": 3, "max_children": 1},   # Máx 2 adultos + 1 criança
    "T2": {"max_pax": 6, "max_children": 2},   # Máx 4 adultos + 2 crianças
}

TIPOLOGIA_CODES = ["T0", "T1", "T2"]

# ---------------------------------------------------------------------------
# Timeouts e parâmetros do browser (em segundos)
# ---------------------------------------------------------------------------
PMS_SEARCH_TIMEOUT = 15       # Timeout para aguardar resultados da pesquisa
PMS_RETRY_WAIT = 10           # Espera antes de retry após falha de rede
PMS_LOGIN_WAIT = 60           # Tempo máximo para intervenção manual no login
BROWSER_SLOW_MO = 200         # Atraso entre ações do browser (ms) para estabilidade
BROWSER_VIEWPORT_WIDTH = 1280
BROWSER_VIEWPORT_HEIGHT = 900

# ---------------------------------------------------------------------------
# Horário automático do bot
# ---------------------------------------------------------------------------
ACTIVE_START_HOUR = 22
ACTIVE_START_MINUTE = 30
ACTIVE_END_HOUR = 7
ACTIVE_END_MINUTE = 0

# Hora da mensagem de transição (antes do fecho)
PRE_CLOSING_HOUR = 6
PRE_CLOSING_MINUTE = 59

# ---------------------------------------------------------------------------
# Retenção de dados
# ---------------------------------------------------------------------------
DATA_RETENTION_DAYS = 7       # Dias máximos de retenção (exceto reservas pending/confirmed)

# ---------------------------------------------------------------------------
# Debounce do WhatsApp (agrupamento de mensagens)
# ---------------------------------------------------------------------------
MESSAGE_DEBOUNCE_SECONDS = 15      # Espera após última mensagem antes de processar
MESSAGE_DEBOUNCE_MAX_SECONDS = 60  # Tempo máximo de agrupamento

# ---------------------------------------------------------------------------
# Configuração do modelo Ollama (IA local)
# ---------------------------------------------------------------------------
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b-instruct-q4_K_M"
OLLAMA_TIMEOUT = 30          # Timeout para resposta do Ollama (segundos)
OLLAMA_TEMPERATURE = 0.3     # Temperatura (respostas consistentes e controladas)
OLLAMA_HOST = "http://localhost:11434"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = "DEBUG"           # Nível mínimo de logging: DEBUG | INFO | WARNING | ERROR | CRITICAL
LOG_MAX_BYTES = 5 * 1024 * 1024   # Tamanho máximo por ficheiro de log (5 MB)
LOG_BACKUP_COUNT = 3              # Número de ficheiros de backup a manter

# ---------------------------------------------------------------------------
# Screenshots de auditoria
# ---------------------------------------------------------------------------
SCREENSHOT_PREFIX = "audit"
# Formato do nome: audit_YYYYMMDD_HHMMSS_checkin_YYYYMMDD_nights_N.png
