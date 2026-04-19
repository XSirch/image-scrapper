# Fashion Bot Scrapper API 🛍️🕷️

Um Web Scraper super furtivo (Stealth) e escalável construído em **Python** e **FastAPI**. Projetado meticulosamente para superar barreiras Anti-Bot agressivas, Captchas e *Login Walls* de gigantes do E-commerce de moda como Temu, Dafiti e Shopee.

## Recursos Principais

- **Stealth Embutido**: Utiliza internamente o `Camoufox` e rotinas assíncronas do Playwright para se camuflar perfeitamente, anulando "Cloudflare/Datadome JS challenges".
- **Banco de Dados Resiliente (Memória)**: Usando **PostgreSQL**, o bot aprende em tempo-real por domínio qual nível de latência e força-bruta precisa usar para varrer uma foto. Se um site precisa de *network_idle* de SPA, ele vai persistir essa regra globalmente para poupar carregamento futuro no banco.
- **RESTful API Native**: Funciona como um gateway de microserviço HTTP.
- **Fila Única com Multithreading**: Você pode bater 10.000 URLs na API. O bot enfileirará todas mantendo 1 única aba furtiva isolada renderizando-as no background (Single-Worker Thread), driblando qualquer bloqueio de IP por enxurrada.

---

## 🚀 Como Rodar o Projeto

Você tem duas formas de iniciar o Fashion Bot: de forma isolada no seu sistema ou inteiramente utilizando o Docker (Recomendado).

### Opção 1: Via Docker (Mais Fácil)

Não gosta de configurar o Python local? Ótimo. A arquitetura em Docker é super enxuta.
Como a memória reside agora num banco PostgreSQL, você precisa ter uma URL de conexão apontando pra um banco (Ex: Render, Supabase, ou outro servidor externo). 

```bash
# 1. Clone o projeto e entre na pasta
git clone https://github.com/XSirch/image-scrapper.git
cd image-scrapper

# 2. Exporte a URL do seu PostgreSQL
export DATABASE_URL="postgresql://usuario:senha@seu-host.com:5432/nomedobanco"
# (No Windows Powershell use $env:DATABASE_URL="...")

# 3. Suba o container 
docker-compose up -d --build
```
Após o build finalizar, a API estará pronta recebendo em: `http://localhost:8000`

### Opção 2: Localmente (Desenvolvedor)

Caso prefira rodar ou debugar o ambiente pela sua máquina.

**1. Instale o Ambiente**
```bash
python -m venv venv
# ative (Windows: .\venv\Scripts\activate | Linux: source venv/bin/activate)

pip install -r requirements.txt
```

**2. Instale o Motor Javascript do Playwright C++**
O scrapper demanda os browsers furtivos, que são pesados na primeira vez (cerca de 200MB):
```bash
playwright install chromium
playwright install-deps chromium
```

**3. Inicie**
Não esqueça o link do banco e o inicie no servidor ASGI (Uvicorn):
```bash
uvicorn main:app --port 8000
```

---

## 🛠 Entendendo a API REST

Uma vez rodando, você terá uma documentação nativa em Interface Visual no link:
👉 [http://localhost:8000/docs](http://localhost:8000/docs)

Lá, você pode simular os endpoints na hora clicando em **Try it out**.

### `POST /api/extract`
Busca as fotos "vivas" de um produto.

**Payload Request:**
```json
{
  "url": "https://www.dafiti.com.br/algum-produto",
  "escalation_level": 1
}
```
*`escalation_level`: (1 a 4). 1 é o básico. 4 destrói banners da tela, ignora tabelas de tamanho e carrega pesadamente Javascripts escondidos.*

**Response (200 OK):**
Retorna uma lista JSON pura super limpa contendo apenas imagens que se encaixaram na heurística (são fotos altas e em CDNs).
```json
[
  "https://static.dafiti.com.br/p/alguma-foto-1.jpg",
  "https://static.dafiti.com.br/p/alguma-foto-2.jpg"
]
```

### `GET /api/profiles`
Sobe todo o raciocínio construído pela inteligência de evasão em JSON tirado da sua base Postgres. Bom para monitorar quais sites estão pedindo timeout extremo.
