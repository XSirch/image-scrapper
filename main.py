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
import os
import uuid

# Custom components
from scrapling.fetchers import StealthySession
from scrapper import extract_product_images
import database

# Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WORKER_COUNT = int(os.getenv("WORKER_COUNT", "3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "120"))

q = queue.Queue()

# Tracking for monitoring
active_tasks = {}  # worker_id -> {url, started_at}
active_tasks_lock = threading.Lock()
stats = {"total_requests": 0, "completed": 0, "failed": 0, "timed_out": 0}
stats_lock = threading.Lock()

class ExtractRequest(BaseModel):
    url: str
    escalation_level: Optional[int] = 1

def browser_worker(worker_id: int):
    """
    Worker thread que mantém sua própria instância de browser stealth.
    Cada worker consome tarefas da fila compartilhada e processa independentemente.
    """
    logging.info(f"[Worker-{worker_id}] Iniciando navegador fantasma...")
    try:
        session = StealthySession(headless=True)
        session.start()
        logging.info(f"[Worker-{worker_id}] Navegador ativo e aguardando requisições.")
    except Exception as e:
        logging.error(f"[Worker-{worker_id}] Falha fatal ao iniciar StealthySession: {e}")
        return
    
    while True:
        try:
            future, task_url, level, request_id = q.get()
            if task_url is None:  # Sentinel for shutdown
                break
            
            with active_tasks_lock:
                active_tasks[worker_id] = {"url": task_url, "started_at": time.time(), "request_id": request_id}
            
            logging.info(f"[Worker-{worker_id}] [{request_id}] Processando URL: {task_url} (Nível {level})")
            start_time = time.time()
            
            try:
                images = extract_product_images(task_url, session=session, escalation_level=level)
                elapsed = time.time() - start_time
                logging.info(f"[Worker-{worker_id}] [{request_id}] Concluído: {task_url} ({len(images)} imagens em {elapsed:.2f}s)")
                future.set_result(images)
                with stats_lock:
                    stats["completed"] += 1
            except Exception as e:
                logging.error(f"[Worker-{worker_id}] [{request_id}] Erro extraindo {task_url}: {e}")
                if not future.done():
                    future.set_exception(e)
                with stats_lock:
                    stats["failed"] += 1
            finally:
                with active_tasks_lock:
                    active_tasks.pop(worker_id, None)
                q.task_done()
                
        except Exception as e:
            logging.error(f"[Worker-{worker_id}] Erro inesperado no loop do worker: {e}")

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
        
    # Inicia pool de workers — cada um com seu próprio browser stealth
    logging.info(f"Iniciando pool de {WORKER_COUNT} workers de browser...")
    global worker_threads
    worker_threads = []
    for i in range(WORKER_COUNT):
        t = threading.Thread(target=browser_worker, args=(i,), daemon=True, name=f"BrowserWorker-{i}")
        t.start()
        worker_threads.append(t)
        # Stagger startup para evitar pico de CPU/memória simultâneo
        time.sleep(2)
    logging.info(f"Pool de {WORKER_COUNT} workers iniciado com sucesso!")

@app.post("/api/extract", response_model=List[str])
def extract(req: ExtractRequest):
    """
    Extrai imagens do produto da URL.
    - Requests são distribuídos entre múltiplos workers (browsers stealth) em paralelo.
    - O `escalation_level` padrão é 1 (rápido). Valores maiores forçam SPA dinâmico e anulação de filtros de ruído.
    """
    request_id = uuid.uuid4().hex[:8]
    
    with stats_lock:
        stats["total_requests"] += 1
    
    future = concurrent.futures.Future()
    q.put((future, req.url, req.escalation_level, request_id))
    
    queue_depth = q.qsize()
    if queue_depth > WORKER_COUNT:
        logging.info(f"[{request_id}] Enfileirado (posição ~{queue_depth} na fila, {WORKER_COUNT} workers ativos)")
    
    try:
        result = future.result(timeout=REQUEST_TIMEOUT) 
        return result
    except concurrent.futures.TimeoutError:
        with stats_lock:
            stats["timed_out"] += 1
        raise HTTPException(status_code=504, detail=f"Tempo limite de extração esgotado (timeout={REQUEST_TIMEOUT}s)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status")
def get_status():
    """
    Retorna o status atual do sistema: workers ativos, fila, e estatísticas.
    Útil para monitoramento e balanceamento de carga.
    """
    with active_tasks_lock:
        current_tasks = {}
        for wid, info in active_tasks.items():
            current_tasks[f"worker-{wid}"] = {
                "url": info["url"],
                "request_id": info["request_id"],
                "running_for_seconds": round(time.time() - info["started_at"], 1)
            }
    
    with stats_lock:
        current_stats = dict(stats)
    
    alive_workers = sum(1 for t in worker_threads if t.is_alive())
    
    return {
        "worker_pool": {
            "configured": WORKER_COUNT,
            "alive": alive_workers,
            "busy": len(current_tasks),
            "idle": alive_workers - len(current_tasks),
        },
        "queue_depth": q.qsize(),
        "active_tasks": current_tasks,
        "stats": current_stats,
    }

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
