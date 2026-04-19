FROM python:3.11-slim

# Install system dependencies required for Playwright and virtual rendering
RUN apt-get update && apt-get install -y \
    curl \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies first (for caching)
COPY requirements.txt .
RUN pip install uv && uv pip install --system --no-cache -r requirements.txt

# Install Playwright browser binaries and OS dependencies for Chromium
RUN playwright install chromium
RUN playwright install-deps chromium

# Trigger Camoufox initial fetch to guarantee the camouflage binary engines are set up
RUN python -c "from scrapling.fetchers import StealthyFetcher; StealthyFetcher.fetch('about:blank', headless=True)" || true

# Copy project files
COPY . .

# Expose API port
EXPOSE 8000

# Start FastAPI application using Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
