# Fashion Bot Scrapper API 🛍️🕷️

Um Web Scraper stealth e escalável construído em **Python** e **FastAPI**. Projetado para superar barreiras Anti-Bot agressivas, Captchas e *Login Walls* de gigantes do E-commerce como Temu, Dafiti e Shopee.

![Dashboard Preview](https://img.shields.io/badge/Dashboard-Dark_Mode-6c5ce7?style=for-the-badge)
![API](https://img.shields.io/badge/API-FastAPI-009688?style=for-the-badge)
![DB](https://img.shields.io/badge/Database-PostgreSQL-336791?style=for-the-badge)

## Recursos Principais

- **Stealth Embutido**: Utiliza o `Camoufox` + Playwright para se camuflar e anular JS challenges (Cloudflare/Datadome).
- **Memória Inteligente (PostgreSQL)**: O bot aprende em tempo real qual nível de agressividade cada domínio exige, persistindo essas regras no banco para otimizar buscas futuras.
- **API RESTful**: Gateway HTTP para integração com qualquer serviço externo.
- **Dashboard Web**: Interface visual embarcada para colar URLs e visualizar imagens extraídas com um clique.
- **Fallback Googlebot SSR**: Quando um site bloqueia o browser (Login Wall), o bot faz uma requisição como Googlebot para extrair imagens do cache de SEO.
- **Extração de URL Params**: Detecta imagens codificadas diretamente nos parâmetros da URL (ex: Temu `top_gallery_url`).
- **Worker Queue Thread-Safe**: Fila única com uma aba stealth isolada, evitando detecção por concorrência.

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

Extrai imagens de produto de uma URL.

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

**Response (200):**
```json
[
  "https://static.dafiti.com.br/p/foto-1.jpg",
  "https://static.dafiti.com.br/p/foto-2.jpg"
]
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
├── main.py              # Servidor FastAPI + worker thread
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

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `DATABASE_URL` | URL de conexão PostgreSQL | `postgresql://user:pass@host:5432/db` |
