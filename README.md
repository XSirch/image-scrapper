# Fashion Bot Scrapper API 🛍️🕷️

Um Web Scraper stealth e escalável construído em **Python** e **FastAPI**. Projetado para superar barreiras Anti-Bot agressivas, Captchas e *Login Walls* de gigantes do E-commerce como Temu, Dafiti e Shopee.

![Version](https://img.shields.io/badge/version-0.2.5-blue?style=for-the-badge)
![Dashboard Preview](https://img.shields.io/badge/Dashboard-Dark_Mode-6c5ce7?style=for-the-badge)
![API](https://img.shields.io/badge/API-FastAPI-009688?style=for-the-badge)
![DB](https://img.shields.io/badge/Database-PostgreSQL-336791?style=for-the-badge)

## Recursos Principais

- **Stealth Embutido**: Utiliza o `Camoufox` + Playwright para se camuflar e anular JS challenges (Cloudflare/Datadome).
- **Memória Inteligente (PostgreSQL)**: O bot aprende em tempo real qual nível de agressividade cada domínio exige, persistindo essas regras no banco para otimizar buscas futuras.
- **Auto Retry com Aprendizado**: Quando uma URL retorna 0 imagens, o scraper tenta ate 3 vezes, escala automaticamente a estrategia e grava o menor nivel que venceu o dominio.
- **API RESTful**: Gateway HTTP para integração com qualquer serviço externo.
- **Dashboard Web**: Interface visual embarcada para colar URLs e visualizar imagens extraídas com um clique.
- **Fallback Googlebot SSR**: Quando um site bloqueia o browser (Login Wall), o bot faz uma requisição como Googlebot para extrair imagens do cache de SEO.
- **Fallback API Gerenciado**: Se o motor local retornar 0 imagens ou encontrar bloqueio anti-bot terminal, chama Scrape.do (default) ou ScrapingBee apenas como fallback.
- **Fallback SHEIN**: Detecta paginas de risco (`/risk/...`), ignora assets de layout e tenta extrair imagens por metadados, JSON publico e API `quickView` quando disponivel.
- **Sessao Manual Persistente**: Pode rodar o browser visivel com perfil em disco para resolver challenges manualmente e reutilizar cookies em novas extrações.
- **Extração de URL Params**: Detecta imagens codificadas diretamente nos parâmetros da URL (ex: Temu `top_gallery_url`).
- **Pool de Workers Concorrentes**: Múltiplos browsers stealth em paralelo (configurável via `WORKER_COUNT`), cada um com sua própria sessão isolada. Suporta dezenas de clientes simultâneos.
- **Callback WebSocket**: Fluxo assíncrono com `request_id` e eventos em tempo real, sem polling do requisitante.
- **Monitoramento em Tempo Real**: Endpoint `/api/status` para acompanhar workers ativos, fila de espera e estatísticas de performance.

---

## 🚀 Instalação e Execução

### Opção 1: Docker (Recomendado)

Você precisa de uma URL de conexão PostgreSQL (Supabase, Neon, Render, ou seu próprio servidor).

```bash
# 1. Clone o projeto
git clone https://github.com/XSirch/image-scrapper.git
cd image-scrapper

# 2. Exporte a URL do seu PostgreSQL
export DATABASE_URL="postgresql://usuario:senha@seu-host.com:5432/nomedobanco"
# Windows PowerShell: $env:DATABASE_URL="postgresql://..."

# 3. Suba o container
docker-compose up -d --build
```

> O build usa **uv** para instalar dependências Python, o que é significativamente mais rápido que pip.

Após finalizar, acesse: `http://localhost:8000`

### Opção 2: Localmente (Desenvolvimento)

```bash
# 1. Crie e ative o ambiente virtual
python -m venv venv
# Windows: .\venv\Scripts\activate
# Linux:   source venv/bin/activate

# 2. Instale dependências
pip install -r requirements.txt

# 3. Instale os binários do Playwright
playwright install chromium
playwright install-deps chromium

# 4. Configure a variável de ambiente do banco
export DATABASE_URL="postgresql://usuario:senha@localhost:5432/botdb"

# 5. Inicie o servidor
uvicorn main:app --port 8000
```

---

## 🖥️ Dashboard Web

Ao acessar `http://localhost:8000`, você será redirecionado automaticamente para o painel visual.

**Funcionalidades do Dashboard:**
- Campo de input para colar a URL do produto
- Seletor de nível de escalada (1 a 4)
- Grid de imagens extraídas com preview e link direto
- Indicador de status em tempo real (aguardando / processando / sucesso / erro)
- Painel de "Memória de Domínios" mostrando o nível aprendido de cada site

---

## 🛠️ API REST

Documentação interativa (Swagger) disponível em: `http://localhost:8000/docs`

### `POST /api/extract`

Extrai imagens de produto de uma URL. **Suporta múltiplos requests simultâneos** — cada um é distribuído automaticamente para um worker disponível no pool.

Se a primeira tentativa retornar 0 imagens, o scraper faz retry automatico ate 3 tentativas totais. A cada retry ele sobe o nivel de agressividade e, quando encontra imagens em uma tentativa posterior, salva esse nivel no perfil do dominio.

**Request:**
```json
{
  "url": "https://www.dafiti.com.br/algum-produto",
  "escalation_level": 1
}
```

| Nível | Comportamento |
|-------|--------------|
| 1 | Extração rápida padrão |
| 2 | Força `network_idle` para SPAs pesados |
| 3 | Desativa filtros de similaridade |
| 4 | Remove todos os filtros de ruído (banners, tabelas, ícones) |

O valor enviado em `escalation_level` e tratado como piso. Se o dominio ja tiver um nivel maior memorizado, o scraper usa o perfil salvo e nunca rebaixa a estrategia automaticamente.

**Response (200):**
```json
[
  "https://static.dafiti.com.br/p/foto-1.jpg",
  "https://static.dafiti.com.br/p/foto-2.jpg"
]
```

### `POST /api/extract/async`

Cria uma tarefa de extração e retorna imediatamente um `request_id`. Use o WebSocket `/ws/extract/{request_id}` para receber os eventos sem polling.

**Response (200):**
```json
{
  "request_id": "a1b2c3d4",
  "status": "queued"
}
```

### `WS /ws/extract/{request_id}`

Envia o estado atual ao conectar e depois publica eventos da tarefa:

```json
{
  "event": "completed",
  "request_id": "a1b2c3d4",
  "status": "completed",
  "url": "https://www.dafiti.com.br/algum-produto",
  "image_count": 2,
  "images": [
    "https://static.dafiti.com.br/p/foto-1.jpg",
    "https://static.dafiti.com.br/p/foto-2.jpg"
  ],
  "elapsed_seconds": 8.4
}
```

Eventos possíveis: `accepted`, `queued`, `started`, `completed`, `failed`, `timeout`, `not_found`.

### `GET /api/status`

Retorna o estado atual do sistema: pool de workers, fila e estatísticas.

```json
{
  "worker_pool": {
    "configured": 3,
    "alive": 3,
    "busy": 2,
    "idle": 1
  },
  "queue_depth": 0,
  "active_tasks": {
    "worker-0": { "url": "https://...", "request_id": "a1b2c3d4", "running_for_seconds": 5.3 }
  },
  "stats": {
    "total_requests": 42,
    "completed": 38,
    "failed": 2,
    "timed_out": 2
  }
}
```

### `GET /api/profiles`

Retorna o banco de aprendizado de domínios.

```json
{
  "shopee.com.br": { "escalation_level": 4, "wait_idle": true },
  "dafiti.com.br": { "escalation_level": 2, "wait_idle": true }
}
```

---

## 📁 Estrutura do Projeto

```
├── main.py              # Servidor FastAPI + pool de workers concorrentes
├── scrapper.py           # Lógica de extração e heurísticas
├── database.py           # Driver PostgreSQL (CRUD + migração)
├── static/index.html     # Dashboard web
├── Dockerfile            # Imagem de produção (uv + Playwright)
├── docker-compose.yml    # Orquestração do container
├── requirements.txt      # Dependências Python
└── app.py                # (Legacy) Interface desktop CustomTkinter
```

---

## ⚙️ Variáveis de Ambiente

| Variável | Descrição | Default |
|----------|-----------|---------|
| `DATABASE_URL` | URL de conexão PostgreSQL | `postgresql://user:pass@host:5432/db` |
| `WORKER_COUNT` | Número de browsers stealth simultâneos | `3` |
| `REQUEST_TIMEOUT` | Timeout máximo por request (segundos) | `120` |
| `BROWSER_HEADLESS` | Define se os workers rodam sem janela. Use `false` para resolver challenges manualmente | `true` |
| `BROWSER_USER_DATA_DIR` | Diretório base para perfis persistentes por worker (`worker-0`, `worker-1`, etc.) | vazio |
| `SHEIN_MANUAL_WAIT_SECONDS` | Tempo que o scraper espera na página SHEIN antes de analisar, permitindo resolver o challenge no browser visível | `0` |
| `SCRAPING_API_FALLBACK` | Provider de fallback quando o motor local falha (`scrapedo`, `scrapingbee`, `none`) | `scrapedo` |
| `SCRAPEDO_TOKEN` | Token da API Scrape.do usado somente no fallback | vazio |
| `SCRAPINGBEE_API_KEY` | Chave ScrapingBee usada somente se `SCRAPING_API_FALLBACK=scrapingbee` | vazio |
| `SCRAPING_API_RENDER` | Ativa browser/render no provider gerenciado | `true` |
| `SCRAPING_API_SUPER` | Ativa proxy premium/residencial/mobile no provider gerenciado | `true` |
| `SCRAPING_API_COUNTRY_CODE` | País do proxy nos providers gerenciados | `br` |
| `SCRAPING_API_MAX_IMAGES` | Limite de imagens retornadas por fallback gerenciado | `10` |
| `SCRAPINGBEE_LIMITED_EXTRACT_RULES` | Usa `extract_rules` na ScrapingBee para pedir no máximo `SCRAPING_API_MAX_IMAGES` imagens no provider | `true` |
| `SCRAPINGBEE_STEALTH_ENABLED` | Ativa ScrapingBee `stealth_proxy=true` com proxy BR para domínios difíceis antes do provider padrão | `true` |
| `SCRAPINGBEE_STEALTH_DOMAINS` | Marcadores de domínio que usam stealth caro (`shein`, `shopee`, `temu`) | `shein,shopee,temu` |

> **💡 Dimensionamento:** Cada worker consome ~300-500MB de RAM. Para uma VPS com 4GB, use `WORKER_COUNT=3`. Para 8GB, pode subir para `WORKER_COUNT=6`.

### Sessão manual para SHEIN

Quando a SHEIN retorna apenas `/risk/challenge` ou `/risk/action/limit`, retries automáticos não resolvem. Para reaproveitar uma sessão validada:

```bash
$env:WORKER_COUNT="1"
$env:BROWSER_HEADLESS="false"
$env:BROWSER_USER_DATA_DIR=".browser-profiles"
$env:SHEIN_MANUAL_WAIT_SECONDS="90"
uvicorn main:app --port 8000
```

Na primeira requisição da SHEIN, resolva o challenge na janela aberta durante o tempo configurado. O perfil fica salvo em `.browser-profiles/worker-0` e será reutilizado nas próximas execuções enquanto o diretório for preservado.
