# Changelog

Todas as mudanças notáveis neste projeto serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e este projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

---

## [0.2.2] - 2026-04-29

### Adicionado
- **Resolução de Short URLs**: links encurtados (`br.shp.ee`, `sho.pe`, etc.) são resolvidos automaticamente via HTTP redirect antes do scraping
- **Fallback Shopee API**: quando Googlebot SSR retorna 403, tenta extrair imagens via API interna da Shopee usando `shop_id`/`item_id` da URL

### Corrigido
- Links da Shopee compartilhados via app (`br.shp.ee/xxx`) não eram reconhecidos como `shopee.com.br`, impedindo a aplicação do perfil de escalação correto
- Fallback Googlebot SSR falhava silenciosamente (403) sem tentar estratégias alternativas

---

## [0.2.1] - 2026-04-27

### Adicionado
- **Pool de Workers Concorrentes**: múltiplos browsers stealth em paralelo (configurável via `WORKER_COUNT`, default 3), substituindo o worker único serial
- **Endpoint `GET /api/status`**: monitoramento em tempo real de workers ativos/ociosos, profundidade da fila e estatísticas (total, concluídos, falhas, timeouts)
- **Request IDs únicos**: cada requisição recebe um UUID curto para rastreabilidade nos logs
- **Variáveis de ambiente**: `WORKER_COUNT` e `REQUEST_TIMEOUT` configuráveis via docker-compose
- **Arquivo `.env.example`**: template com todas as variáveis documentadas e dicas de dimensionamento
- **`.env` no `.gitignore`**: proteção contra vazamento de credenciais

### Corrigido
- Import do `camoufox` no Dockerfile — módulo é interno ao `scrapling[fetchers]`, não standalone

---

## [0.2.0] - 2026-04-19

### Adicionado
- **Dashboard Web**: interface visual embarcada com estética dev-tool minimalista para extração manual de imagens
- **PostgreSQL como backend**: migração do sistema de memória de JSON local para banco de dados persistente
- **Migração automática**: dados do `site_profiles.json` são migrados para o PostgreSQL no startup
- **Documentação completa**: README reescrito com instruções de instalação, API reference e arquitetura

### Alterado
- Dockerfile otimizado com `uv` para instalação de dependências mais rápida
- Build do Dockerfile corrigido: download de binários sem lançar browser

### Removido
- Interface desktop CustomTkinter (`app.py`) — substituída pelo dashboard web
- Arquivo `site_profiles.json` — novos usuários usam PostgreSQL diretamente

---

## [0.1.0] - 2026-04-19

### Adicionado
- **Scraper stealth**: extração de imagens de produto usando Camoufox + Playwright via `scrapling`
- **API FastAPI**: endpoint `POST /api/extract` com fila thread-safe e worker background
- **Heurísticas inteligentes**: OpenGraph, gallery CSS, CDN dominance filter, smart similarity filter
- **Sistema de escalada**: 4 níveis de agressividade com memória por domínio
- **Fallback Googlebot SSR**: extração via cache de SEO para sites com Login/Captcha Wall
- **Extração de URL params**: detecção de imagens codificadas em query strings (ex: Temu)
- **Dockerfile**: imagem de produção com Playwright + Camoufox pré-instalados
- **Docker Compose**: orquestração simplificada com variável `DATABASE_URL`
