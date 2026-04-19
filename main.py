from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import threading
import queue
import concurrent.futures
import json
import logging
import time

# Custom components
from scrapling.fetchers import StealthySession
from scrapper import extract_product_images
import database

# Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
q = queue.Queue()

class ExtractRequest(BaseModel):
    url: str
    escalation_level: Optional[int] = 1

def browser_worker():
    logging.info("Iniciando navegador fantasma na thread background...")
    try:
        session = StealthySession(headless=True)
        session.start()
        logging.info("Navegador ativo e aguardando requisições.")
    except Exception as e:
        logging.error(f"Falha fatal ao iniciar StealthySession: {e}")
        return
    
    while True:
        try:
            future, task_url, level = q.get()
            if task_url is None: # Sentinel for shutdown
                break
            
            logging.info(f"Worker processando URL: {task_url} (Nível {level})")
            start_time = time.time()
            images = extract_product_images(task_url, session=session, escalation_level=level)
            elapsed = time.time() - start_time
            logging.info(f"Worker terminou URL: {task_url} ({len(images)} imagens em {elapsed:.2f}s)")
            
            future.set_result(images)
            q.task_done()
        except Exception as e:
            logging.error(f"Erro no worker extraindo {task_url}: {e}")
            if "future" in locals() and not future.done():
                future.set_exception(e)

app = FastAPI(title="Fashion Bot API", description="API de extração escalonável de imagens de e-commerce")

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/dashboard/index.html")

app.mount("/dashboard", StaticFiles(directory="static"), name="dashboard")

@app.on_event("startup")
def startup_event():
    # Inicializa banco postgres e roda a migração do json local se existir
    try:
        database.init_db()
        database.migrate_json_to_db()
    except Exception as e:
        logging.error(f"PostgreSQL connection error: {e}")
        
    global worker_thread
    worker_thread = threading.Thread(target=browser_worker, daemon=True)
    worker_thread.start()

@app.post("/api/extract", response_model=List[str])
def extract(req: ExtractRequest):
    """
    Extrai imagens do produto da URL.
    - O processo utiliza uma fila background (uma única tab stealth ativa para evitar detecção e vazamentos de memória).
    - O `escalation_level` padrão é 1 (rápido). Valores maiores forçam SPA dinâmico e anulação de filtros de ruído.
    """
    future = concurrent.futures.Future()
    q.put((future, req.url, req.escalation_level))
    try:
        # FastAPI executa rotas `def` (sync) em uma threadpool separada pra n bloquear o loop assíncrono.
        # Assim é seguro fazer um future.result() blocante aqui.
        result = future.result(timeout=120) 
        return result
    except concurrent.futures.TimeoutError:
        raise HTTPException(status_code=504, detail="Tempo limite de extração esgotado (timeout=120s)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/profiles")
def get_profiles():
    """
    Retorna o banco de dados dinâmico de aprendizado do scrapper diretamente do PostgreSQL.
    """
    try:
        return database.get_all_profiles()
    except Exception as e:
        logging.error(f"Failed to fetch profiles from DB: {e}")
        return {}
