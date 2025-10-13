#!/bin/bash
set -e

# ANSI color codes
RED='\033[91m'
GREEN='\033[92m'
YELLOW='\033[93m'
BLUE='\033[94m'
CYAN='\033[96m'
RESET='\033[0m'

echo -e "${CYAN}[DOCKER]${RESET} Starting Szurubooru Manager in Docker..."

# Check if config file exists
if [ ! -f "/app/config.json" ]; then
    echo -e "${RED}[ERROR]${RESET} config.json not found in /app/"
    echo "Please mount your config.json file to /app/config.json"
    exit 1
fi

# Check if uploads directory exists
if [ ! -d "/app/uploads" ]; then
    echo -e "${YELLOW}[WARNING]${RESET} uploads directory not found, creating it..."
    mkdir -p /app/uploads
fi

# Check if logs directory exists
if [ ! -d "/app/logs" ]; then
    echo -e "${BLUE}[INFO]${RESET} Creating logs directory..."
    mkdir -p /app/logs
fi

echo -e "${GREEN}[SUCCESS]${RESET} Environment ready"
echo -e "${BLUE}[INFO]${RESET} Configuration: /app/config.json"
echo -e "${BLUE}[INFO]${RESET} Uploads: /app/uploads"
echo -e "${BLUE}[INFO]${RESET} Logs: /app/logs"

# Set default values for environment variables
MODE=${MODE:-"optimized"}
SCHEDULE_ENABLED=${SCHEDULE_ENABLED:-"true"}
SCHEDULE_TIME=${SCHEDULE_TIME:-"*/30 * * * *"}

echo -e "${BLUE}[CONFIG]${RESET} Mode: $MODE"
echo -e "${BLUE}[CONFIG]${RESET} Schedule enabled: $SCHEDULE_ENABLED"
if [ "$SCHEDULE_ENABLED" = "true" ]; then
    echo -e "${BLUE}[CONFIG]${RESET} Schedule time: $SCHEDULE_TIME"
fi

# Build the command array to avoid glob expansion
CMD_ARGS=("--mode" "$MODE")

if [ "$SCHEDULE_ENABLED" = "true" ]; then
    CMD_ARGS+=("--schedule" "$SCHEDULE_TIME")
fi

echo -e "${CYAN}[DOCKER]${RESET} Starting with command: python szurubooru_manager.py ${CMD_ARGS[*]}"

# Execute the command with proper quoting
exec python szurubooru_manager.py "${CMD_ARGS[@]}"
