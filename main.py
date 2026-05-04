from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import asyncio
import threading
import queue
import concurrent.futures
import logging
import time
import os
import uuid
from pathlib import Path

# Custom components
from scrapling.fetchers import StealthySession
from scrapper import extract_product_images
import database

# Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WORKER_COUNT = int(os.getenv("WORKER_COUNT", "3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "120"))
BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() not in ("0", "false", "no", "off")
BROWSER_USER_DATA_DIR = os.getenv("BROWSER_USER_DATA_DIR", "").strip()

q = queue.Queue()
worker_threads = []
app_loop = None

# Tracking for monitoring
active_tasks = {}  # worker_id -> {url, started_at}
active_tasks_lock = threading.Lock()
stats = {"total_requests": 0, "completed": 0, "failed": 0, "timed_out": 0}
stats_lock = threading.Lock()
async_tasks = {}
async_tasks_lock = threading.Lock()
websocket_connections = {}
websocket_connections_lock = threading.Lock()
TERMINAL_STATUSES = {"completed", "failed", "timeout"}

class ExtractRequest(BaseModel):
    url: str
    escalation_level: Optional[int] = 1

def _now():
    return time.time()

def _task_event_from_record(record, event=None):
    payload = {
        "event": event or record["status"],
        "request_id": record["request_id"],
        "status": record["status"],
        "url": record["url"],
        "escalation_level": record["escalation_level"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }
    if record.get("started_at"):
        payload["started_at"] = record["started_at"]
    if record.get("completed_at"):
        payload["completed_at"] = record["completed_at"]
    if record.get("elapsed_seconds") is not None:
        payload["elapsed_seconds"] = record["elapsed_seconds"]
    if record["status"] == "completed":
        images = record.get("images") or []
        payload["images"] = images
        payload["image_count"] = len(images)
    if record["status"] in ("failed", "timeout"):
        payload["error"] = record.get("error") or "unknown error"
    if record.get("worker_id") is not None:
        payload["worker_id"] = record["worker_id"]
    return payload

async def _broadcast_task_event(request_id, payload):
    with websocket_connections_lock:
        connections = list(websocket_connections.get(request_id, set()))
    stale = []
    for websocket in connections:
        try:
            await websocket.send_json(payload)
        except Exception:
            stale.append(websocket)
    if stale:
        with websocket_connections_lock:
            current = websocket_connections.get(request_id)
            if current:
                for websocket in stale:
                    current.discard(websocket)
                if not current:
                    websocket_connections.pop(request_id, None)

def _publish_task_event(request_id, event=None):
    with async_tasks_lock:
        record = async_tasks.get(request_id)
        if not record:
            return
        payload = _task_event_from_record(record, event=event)
    if app_loop and app_loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast_task_event(request_id, payload), app_loop)

def _create_async_task_record(request_id, url, level):
    now = _now()
    with async_tasks_lock:
        async_tasks[request_id] = {
            "request_id": request_id,
            "url": url,
            "escalation_level": level,
            "status": "accepted",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "elapsed_seconds": None,
            "images": None,
            "error": None,
            "terminal": False,
        }

def _mark_async_task(request_id, status, **updates):
    with async_tasks_lock:
        record = async_tasks.get(request_id)
        if not record or record.get("terminal"):
            return False
        record.update(updates)
        record["status"] = status
        record["updated_at"] = _now()
        if status in TERMINAL_STATUSES:
            record["terminal"] = True
    _publish_task_event(request_id, event=status)
    return True

def _schedule_async_timeout(request_id):
    def on_timeout():
        timed_out = _mark_async_task(
            request_id,
            "timeout",
            completed_at=_now(),
            error=f"Tempo limite de extração esgotado (timeout={REQUEST_TIMEOUT}s)",
        )
        if timed_out:
            with stats_lock:
                stats["timed_out"] += 1

    timer = threading.Timer(REQUEST_TIMEOUT, on_timeout)
    timer.daemon = True
    timer.start()
    with async_tasks_lock:
        record = async_tasks.get(request_id)
        if record:
            record["timeout_timer"] = timer

def _cancel_async_timeout(request_id):
    with async_tasks_lock:
        record = async_tasks.get(request_id)
        timer = record.get("timeout_timer") if record else None
    if timer:
        timer.cancel()

def _async_task_exists(request_id):
    with async_tasks_lock:
        return request_id in async_tasks

def _async_task_is_terminal(request_id):
    with async_tasks_lock:
        record = async_tasks.get(request_id)
        return bool(record and record.get("terminal"))

def _enqueue_extraction(url, level, request_id=None, future=None, async_mode=False):
    request_id = request_id or uuid.uuid4().hex[:8]
    if async_mode:
        _create_async_task_record(request_id, url, level)
        _publish_task_event(request_id, event="accepted")
    q.put((future, url, level, request_id))
    if async_mode:
        _mark_async_task(request_id, "queued")
        _schedule_async_timeout(request_id)
    queue_depth = q.qsize()
    if queue_depth > WORKER_COUNT:
        logging.info(f"[{request_id}] Enfileirado (posição ~{queue_depth} na fila, {WORKER_COUNT} workers ativos)")
    return request_id

def browser_worker(worker_id: int):
    """
    Worker thread que mantém sua própria instância de browser stealth.
    Cada worker consome tarefas da fila compartilhada e processa independentemente.
    """
    logging.info(f"[Worker-{worker_id}] Iniciando navegador fantasma...")
    try:
        session_options = {"headless": BROWSER_HEADLESS}
        if BROWSER_USER_DATA_DIR:
            profile_dir = Path(BROWSER_USER_DATA_DIR) / f"worker-{worker_id}"
            profile_dir.mkdir(parents=True, exist_ok=True)
            session_options["user_data_dir"] = str(profile_dir)
            logging.info(f"[Worker-{worker_id}] Usando perfil persistente: {profile_dir}")
        if not BROWSER_HEADLESS:
            logging.info(f"[Worker-{worker_id}] Navegador visível habilitado para sessão manual.")

        session = StealthySession(**session_options)
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
            if _async_task_is_terminal(request_id):
                q.task_done()
                continue
            
            with active_tasks_lock:
                active_tasks[worker_id] = {"url": task_url, "started_at": time.time(), "request_id": request_id}
            _mark_async_task(request_id, "started", started_at=_now(), worker_id=worker_id)
            
            logging.info(f"[Worker-{worker_id}] [{request_id}] Processando URL: {task_url} (Nível {level})")
            start_time = time.time()
            
            try:
                images = extract_product_images(task_url, session=session, escalation_level=level)
                elapsed = time.time() - start_time
                logging.info(f"[Worker-{worker_id}] [{request_id}] Concluído: {task_url} ({len(images)} imagens em {elapsed:.2f}s)")
                if future and not future.done():
                    future.set_result(images)
                async_done = False
                if _async_task_exists(request_id):
                    _cancel_async_timeout(request_id)
                    async_done = _mark_async_task(
                        request_id,
                        "completed",
                        images=images,
                        completed_at=_now(),
                        elapsed_seconds=round(elapsed, 2),
                        error=None,
                    )
                if not _async_task_exists(request_id) or async_done:
                    with stats_lock:
                        stats["completed"] += 1
            except Exception as e:
                logging.error(f"[Worker-{worker_id}] [{request_id}] Erro extraindo {task_url}: {e}")
                if future and not future.done():
                    future.set_exception(e)
                async_failed = False
                if _async_task_exists(request_id):
                    _cancel_async_timeout(request_id)
                    async_failed = _mark_async_task(
                        request_id,
                        "failed",
                        completed_at=_now(),
                        elapsed_seconds=round(time.time() - start_time, 2),
                        error=str(e),
                    )
                if not _async_task_exists(request_id) or async_failed:
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
async def startup_event():
    global app_loop
    app_loop = asyncio.get_running_loop()

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
        await asyncio.sleep(2)
    logging.info(f"Pool de {WORKER_COUNT} workers iniciado com sucesso!")

@app.post("/api/extract", response_model=List[str])
def extract(req: ExtractRequest):
    """
    Extrai imagens do produto da URL.
    - Requests são distribuídos entre múltiplos workers (browsers stealth) em paralelo.
    - O `escalation_level` padrão é 1 (rápido). Valores maiores forçam SPA dinâmico e anulação de filtros de ruído.
    """
    with stats_lock:
        stats["total_requests"] += 1
    
    future = concurrent.futures.Future()
    _enqueue_extraction(req.url, req.escalation_level, future=future)
    
    try:
        result = future.result(timeout=REQUEST_TIMEOUT) 
        return result
    except concurrent.futures.TimeoutError:
        with stats_lock:
            stats["timed_out"] += 1
        raise HTTPException(status_code=504, detail=f"Tempo limite de extração esgotado (timeout={REQUEST_TIMEOUT}s)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/extract/async")
def extract_async(req: ExtractRequest):
    """
    Cria uma tarefa de extração assíncrona.
    O resultado deve ser acompanhado via WebSocket em /ws/extract/{request_id}.
    """
    with stats_lock:
        stats["total_requests"] += 1

    request_id = _enqueue_extraction(
        req.url,
        req.escalation_level,
        async_mode=True,
    )
    return {"request_id": request_id, "status": "queued"}

@app.websocket("/ws/extract/{request_id}")
async def websocket_extract(websocket: WebSocket, request_id: str):
    await websocket.accept()

    with async_tasks_lock:
        record = async_tasks.get(request_id)
        initial_payload = _task_event_from_record(record) if record else None

    if not initial_payload:
        await websocket.send_json({
            "event": "not_found",
            "request_id": request_id,
            "status": "not_found",
            "error": "request_id não encontrado",
        })
        await websocket.close(code=1008)
        return

    with websocket_connections_lock:
        websocket_connections.setdefault(request_id, set()).add(websocket)

    try:
        await websocket.send_json(initial_payload)
        if initial_payload["status"] in TERMINAL_STATUSES:
            await websocket.close()
            return
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        with websocket_connections_lock:
            connections = websocket_connections.get(request_id)
            if connections:
                connections.discard(websocket)
                if not connections:
                    websocket_connections.pop(request_id, None)

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
