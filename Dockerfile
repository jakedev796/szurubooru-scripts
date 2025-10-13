# Use CUDA-enabled PyTorch base image for GPU support (uncomment if you have NVIDIA GPU)
FROM pytorch/pytorch:1.13.1-cuda11.6-cudnn8-runtime

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    wget \
    curl \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application files
COPY szurubooru_manager.py .
COPY components/ ./components/
COPY config.json .
COPY docker-entrypoint.sh .

# Create directories for uploads and logs
RUN mkdir -p /app/uploads /app/logs && \
    chmod +x /app/docker-entrypoint.sh

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Create a non-root user for security
RUN useradd -m -u 1000 szurubooru && \
    chown -R szurubooru:szurubooru /app
USER szurubooru

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import os; exit(0 if os.path.exists('/app/szurubooru_manager.py') else 1)"

# Set entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default command - can be overridden by environment variables
CMD ["/app/docker-entrypoint.sh"]
