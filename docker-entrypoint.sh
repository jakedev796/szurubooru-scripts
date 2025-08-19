#!/bin/bash
set -e

echo "🚀 Starting Szurubooru Manager in Docker..."

# Check if config file exists
if [ ! -f "/app/config.json" ]; then
    echo "❌ Error: config.json not found in /app/"
    echo "Please mount your config.json file to /app/config.json"
    exit 1
fi

# Check if uploads directory exists
if [ ! -d "/app/uploads" ]; then
    echo "⚠️  Warning: uploads directory not found, creating it..."
    mkdir -p /app/uploads
fi

# Check if logs directory exists
if [ ! -d "/app/logs" ]; then
    echo "📁 Creating logs directory..."
    mkdir -p /app/logs
fi

echo "✅ Environment ready"
echo "📋 Configuration: /app/config.json"
echo "📁 Uploads: /app/uploads"
echo "📝 Logs: /app/logs"

# Execute the main command
exec "$@"
