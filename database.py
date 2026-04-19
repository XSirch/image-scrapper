import os
import json
import logging
import psycopg2
from psycopg2.extras import RealDictCursor

# Default fallback to a local URL if running directly outside docker
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/botdb")
MEMORY_FILE = 'site_profiles.json'

def get_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    logging.info("Inicializando banco de dados PostgreSQL...")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS site_profiles (
            domain VARCHAR(255) PRIMARY KEY,
            escalation_level INTEGER DEFAULT 1,
            wait_idle BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()
    logging.info("Tabelas garantidas com sucesso.")

def migrate_json_to_db():
    if not os.path.exists(MEMORY_FILE):
        return
        
    logging.info("Migrando profiles do JSON antigo para PostgreSQL...")
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        for domain, profile in data.items():
            level = profile.get('escalation_level', 1)
            wait_idle = profile.get('wait_idle', False)
            upsert_profile(domain, level, wait_idle)
            
        # Renomeio para servir como "backup já migrado" sem rodar denovo
        new_name = f"{MEMORY_FILE}.bkp"
        os.rename(MEMORY_FILE, new_name)
        logging.info(f"Migração completa. Arquivo renomeado para {new_name}")
    except Exception as e:
        logging.error(f"Falha ao migrar JSON: {e}")

def get_profile(domain: str):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM site_profiles WHERE domain = %s", (domain,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if row:
        return {"escalation_level": row['escalation_level'], "wait_idle": row['wait_idle']}
    return {}

def get_all_profiles():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT domain, escalation_level, wait_idle FROM site_profiles")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    # Formata de volta no estilo do dicionário antigo
    return {row['domain']: {"escalation_level": row['escalation_level'], "wait_idle": row['wait_idle']} for row in rows}

def upsert_profile(domain: str, level: int, wait_idle: bool):
    conn = get_connection()
    cur = conn.cursor()
    
    # Query de inserção com conflito no dominio para fazer UPDATE
    query = '''
        INSERT INTO site_profiles (domain, escalation_level, wait_idle, updated_at) 
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (domain) DO UPDATE 
        SET escalation_level = EXCLUDED.escalation_level,
            wait_idle = EXCLUDED.wait_idle,
            updated_at = CURRENT_TIMESTAMP;
    '''
    cur.execute(query, (domain, level, wait_idle))
    conn.commit()
    cur.close()
    conn.close()
