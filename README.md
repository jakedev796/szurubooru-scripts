# Szurubooru Media Manager v3.0

A Python script for automating media upload and AI auto-tagging for Szurubooru image boards.

## Performance Highlights

- **10-20x Faster**: Process 100k images in hours, not days
- **15-25 files/sec**: Upload speeds (vs. ~1 file/sec traditional)
- **8-15 files/sec**: AI tagging with GPU batching  
- **Parallel Architecture**: True concurrent processing

*Important change logs and feature updates are documented at the bottom of this README.*

## Expected Performance

| Hardware Setup | Upload Rate | Tagging Rate | Overall Rate |
|----------------|-------------|--------------|--------------|
| **i9 + RTX 4080 Super** | 20-25 files/sec | 12-15 files/sec | **15-20 files/sec** |
| **i7 + RTX 3080** | 15-20 files/sec | 8-12 files/sec | **10-15 files/sec** |
| **i5 + RTX 3060** | 10-15 files/sec | 6-10 files/sec | **8-12 files/sec** |
| **CPU Only** | 8-12 files/sec | 2-4 files/sec | **5-8 files/sec** |

## Installation

### Prerequisites
- **Python 3.8+**
- **CUDA-compatible GPU** (recommended for maximum performance)
- **Szurubooru instance** running and accessible

### Quick Install
```bash
# Clone the repository
git clone https://github.com/jakedev796/szurubooru-scripts.git
cd szurubooru-scripts

# Install dependencies
pip install -r requirements.txt

# For maximum GPU performance (recommended)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# If you encounter PyTorch/transformers compatibility issues, try:
# pip install torch==1.13.1 torchvision==0.14.1 --index-url https://download.pytorch.org/whl/cu118
```

### Quick Start
```bash
# 1. Create optimized configuration
python szurubooru_manager.py --create-config

# 2. Edit config.json with your settings
# 3. Test connection
python szurubooru_manager.py --test-connection

# 4. Run high-performance processing
python szurubooru_manager.py --mode optimized
```

### Docker Quick Start

**For GPU users:**
```bash
docker-compose up -d
docker-compose logs -f
```

**For CPU-only users:**
```bash
docker-compose -f docker-compose.cpu.yml up -d
docker-compose -f docker-compose.cpu.yml logs -f
```

### Docker Configuration

You can customize the container behavior using environment variables in your docker-compose file:

```yaml
environment:
  - MODE=optimized                    # Mode: optimized, upload, tag, untagged, add-characters
  - SCHEDULE_ENABLED=true             # Enable/disable scheduling: true, false
  - SCHEDULE_TIME=*/30 * * * *        # Cron schedule (every 30 minutes)
```

**Examples:**
```yaml
# Run once in upload mode (no scheduling)
- MODE=upload
- SCHEDULE_ENABLED=false

# Run every hour in tag mode
- MODE=tag
- SCHEDULE_ENABLED=true
- SCHEDULE_TIME=0 * * * *

# Run daily at 2 AM in optimized mode
- MODE=optimized
- SCHEDULE_ENABLED=true
- SCHEDULE_TIME=0 2 * * *
```

## Operation Modes

### **optimized** (Default)
Full pipeline: Upload + AI tagging with maximum performance
- Uploads new files with appropriate tags (`tagme` for images, `video` for videos)
- Processes images with WD14 Tagger
- Skips AI tagging for video files

### **upload**
Upload-only mode for maximum speed
- Uploads files without AI tagging
- Videos get `video` tag, images get `tagme` tag

### **tag**
Comprehensive tagging for all posts needing tags
- Processes both `tagme` posts AND completely untagged posts
- Continuous processing until no more posts need tagging
- Video support with automatic `video` tag assignment

### **untagged**
Process posts with no tags at all
- Finds posts using `tag-count:0` API query
- Adds `video` tag to untagged videos
- AI tags untagged images

### **add-characters**
Brute-force character tagging for your collection
- Processes posts in your Szurubooru instance
- Only extracts and adds character tags from WD14 Tagger
- Preserves all existing tags - only adds missing character tags
- Range support: Use `--start-post` and `--end-post` to process specific ranges

## Configuration

### Generate Optimized Config
```bash
python szurubooru_manager.py --create-config
```

Creates an optimized `config.json` with high-performance defaults:

```json
{
  "szurubooru_url": "http://localhost:8080",
  "username": "your_username", 
  "api_token": "your_api_token_here",
  "upload_directory": "./uploads",
  "supported_extensions": ["jpg", "jpeg", "png", "gif", "webm", "mp4", "webp"],
  "tagme_tag": "tagme",
  "video_tag": "video",
  
  "max_concurrent_uploads": 12,
  "gpu_batch_size": 8,
  "upload_workers": 8,
  "tagging_workers": 2,
  "pipeline_enabled": true,
  "connection_pool_size": 20,
  "upload_timeout": 30.0,
  "tagging_timeout": 60.0,
  
  "batch_size": 0,
  "gpu_enabled": true,
  "confidence_threshold": 0.5,
  "max_tags_per_image": 20,
  "delete_after_upload": true,
  "retry_attempts": 3,
  "retry_delay": 1.0
}
```

## Usage

### Core Modes

**Optimized Mode (Recommended)**
```bash
python szurubooru_manager.py --mode optimized
```

**Upload-Only Mode (Maximum Speed)**  
```bash
python szurubooru_manager.py --mode upload
```

**Tagging-Only Mode**
```bash
python szurubooru_manager.py --mode tag  
```

**Character-Only Mode**  
```bash
# Add characters to all posts
python szurubooru_manager.py --mode add-characters

# Add characters to posts 1-70000
python szurubooru_manager.py --mode add-characters --start-post 1 --end-post 70000
```

### Advanced Usage

**Custom Configuration:**
```bash
python szurubooru_manager.py --config custom.json --mode optimized
```

**Scheduled Processing:**
```bash
# Every 30 minutes with high performance
python szurubooru_manager.py --schedule "*/30 * * * *" --mode optimized
```

**Performance Benchmarking:**
```bash
python szurubooru_manager.py --mode optimized --benchmark
```

## Tag Synchronization Manager

The `tag_sync_manager.py` script helps maintain your Szurubooru tag database by synchronizing tags with popular aliases from external sources like Danbooru.

### Features

- **CSV Import**: Import tag data from CSV files (e.g., Danbooru tag exports)
- **Category Mapping**: Automatically assign proper categories (default, copyright, character)
- **Alias Management**: Add popular aliases to existing tags
- **Smart Cleanup**: Remove incorrect suggestions and unused tags
- **Batch Processing**: Efficiently handle large tag databases
- **Dry Run Mode**: Preview changes before applying them

### Basic Usage

```bash
# Test connection and preview changes
python tag_sync_manager.py --dry-run --sample

# Sync tags from CSV file
python tag_sync_manager.py --csv danbooru_tags.csv

# Clean up suggestions and unused tags
python tag_sync_manager.py --cleanup-unused --no-create

# Update only categories, skip aliases
python tag_sync_manager.py --no-aliases
```

### CSV File Format

The script expects a CSV file with columns:
- `tag`: Tag name
- `category`: Category number (0=default, 3=copyright, 4=character)
- `count`: Usage count
- `alias`: Comma-separated list of aliases

### Command Line Options

- `--dry-run`: Preview changes without applying them
- `--sample`: Process only one batch for testing
- `--no-create`: Don't create missing tags
- `--no-categories`: Don't update categories
- `--no-aliases`: Don't update aliases
- `--cleanup-unused`: Delete tags with 0 usage
- `--batch-size N`: Set batch size for API calls

## Troubleshooting

### Performance Issues
- Check GPU utilization: `nvidia-smi`
- Increase `max_concurrent_uploads` gradually
- Monitor server response times
- Verify SSD storage (not HDD) for image files

### GPU Issues
**GPU not detected?**
```bash
python -c "import torch; print(torch.cuda.is_available())"
```

**Out of VRAM?**
- Reduce `gpu_batch_size` to 4
- Close other GPU applications
- Use CPU mode: `"gpu_enabled": false`

### Network Issues
- Increase `upload_timeout` and `tagging_timeout`
- Reduce `max_concurrent_uploads`
- Check server capacity and network stability

### Video File Issues
**"Unhandled file type: application/octet-stream" error?**
1. Check if the file is actually a valid video: `--check-video "file.mp4"`
2. The script automatically tries fallback methods for problematic videos
3. Try re-encoding the video with a different tool

## Security Best Practices

- **Use API Tokens**: More secure than passwords
- **HTTPS Only**: For production deployments  
- **File Permissions**: Restrict config file access (`chmod 600 config.json`)
- **Network Security**: Use VPN for remote servers
- **Regular Updates**: Keep dependencies current


## What's New in v3.0

[Read this commit for the full details.](https://github.com/jakedev796/szurubooru-scripts/commit/cc302b226c18713e3bc261925477568192547b75)

## What's New in v2.0

### Architectural Overhaul
- **True Parallel Processing**: Concurrent uploads instead of sequential
- **GPU Batch Processing**: Process multiple images simultaneously on GPU
- **Pipeline Architecture**: Upload and tagging phases run independently
- **Connection Pooling**: Multiple concurrent API connections
- **Async Everything**: Non-blocking I/O operations throughout

### Video File Support
- **Smart Video Detection**: Automatically identifies video files by extension
- **Video Tagging**: Adds 'video' tag to video files instead of 'tagme'
- **AI Tagging Skip**: Videos skip WD14 processing (which doesn't support videos)
- **Supported Formats**: MP4, WebM, AVI, MOV, MKV, FLV, WMV, M4V, 3GP, OGV

### Smart Tag Category Assignment
- **Automatic Categorization**: Tags are automatically assigned to appropriate categories
- **Meta Tags**: `tagme`, `video`, `animated`, `gif`, `nsfw`, etc. → `meta` category
- **Character Tags**: WD14-detected character names → `character` category  
- **General Tags**: All other AI-detected tags → `default` category

### Untagged Posts Processing
- **Find Untagged Posts**: Uses `tag-count:0` API query to find posts with no tags
- **AI Tagging**: Processes untagged images with WD14 Tagger
- **Batch Processing**: Efficiently handles large numbers of untagged posts

## License

Open source - see [LICENSE](LICENSE) file for details.