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

# Set default values for environment variables
MODE=${MODE:-"optimized"}
SCHEDULE_ENABLED=${SCHEDULE_ENABLED:-"true"}
SCHEDULE_TIME=${SCHEDULE_TIME:-"*/30 * * * *"}

echo "🔧 Mode: $MODE"
echo "⏰ Schedule enabled: $SCHEDULE_ENABLED"
if [ "$SCHEDULE_ENABLED" = "true" ]; then
    echo "⏰ Schedule time: $SCHEDULE_TIME"
fi

# Build the command based on environment variables
CMD_ARGS="--mode $MODE"

if [ "$SCHEDULE_ENABLED" = "true" ]; then
    CMD_ARGS="$CMD_ARGS --schedule \"$SCHEDULE_TIME\""
fi

echo "🚀 Starting with command: python szurubooru_manager.py $CMD_ARGS"

# Execute the command
exec python szurubooru_manager.py $CMD_ARGS
