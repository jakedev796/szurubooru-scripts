#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Szurubooru Media Manager
A comprehensive script for uploading media and auto-tagging with WD14 Tagger

Features:
- Upload media from a configured directory to Szurubooru
- Auto-tag images using WD14 Tagger with GPU acceleration
- Remove 'tagme' tags after successful tagging
- Delete original files after successful upload
- Configurable scheduling support
- Efficient batch processing
"""

import sys
import asyncio
import argparse
import logging
import traceback
from datetime import datetime

# Import components
from components import (
    load_config,
    create_default_config,
    check_video_file,
    SzurubooruAPI,
    MediaManager,
    MAGIC_AVAILABLE
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('szurubooru_manager.log')
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Main function with optimized processing modes"""
    parser = argparse.ArgumentParser(description="Szurubooru High-Performance Media Manager")
    parser.add_argument("--config", "-c", default="config.json", help="Configuration file path")
    parser.add_argument("--mode", "-m", 
                       choices=["optimized", "upload", "tag", "untagged", "add-characters", "full", "legacy"], 
                       default="optimized", 
                       help="Operation mode: optimized (recommended), upload, tag (comprehensive), untagged, add-characters (add characters to posts, optionally with --start-post and --end-post), full, or legacy")
    parser.add_argument("--schedule", "-s", help="Schedule in cron format (e.g., '*/30 * * * *' for every 30 minutes)")
    parser.add_argument("--create-config", action="store_true", help="Create an optimized configuration file")
    parser.add_argument("--test-connection", action="store_true", help="Test connection to Szurubooru")
    parser.add_argument("--benchmark", action="store_true", help="Run performance benchmark")
    parser.add_argument("--check-video", help="Check if a video file is valid and get its details")
    parser.add_argument("--start-post", type=int, help="Starting post ID for add-characters mode (inclusive)")
    parser.add_argument("--end-post", type=int, help="Ending post ID for add-characters mode (inclusive)")
    
    args = parser.parse_args()
    
    # Print header
    print("Szurubooru High-Performance Media Manager v2.0")
    print("="*60)
    
    # Show python-magic status
    if MAGIC_AVAILABLE:
        print("File type detection: python-magic (content-based)")
    else:
        print("File type detection: extension-based (fallback)")
        print("  Note: Install python-magic for more robust detection")
    
    # Create config if requested
    if args.create_config:
        create_default_config(args.config)
        return
    
    # Check video file if requested
    if args.check_video:
        check_video_file(args.check_video)
        return
    
    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Failed to load configuration: {e}")
        logger.error(f"Failed to load configuration: {e}")
        return
    
    # Test connection if requested
    if args.test_connection:
        print("Testing API connection...")
        print(f"Server URL: {config.szurubooru_url}")
        print(f"Username: {config.username}")
        print(f"Auth Method: {'API Token' if config.api_token else 'Password'}")
        
        try:
            print("Creating API client...")
            api_client = SzurubooruAPI(config)
            print("Opening session...")
            
            async with api_client as api:
                print("Session established")
                print("Testing connection...")
                connection_result = await api.test_connection()
                
                if connection_result:
                    print("API connection successful!")
                    print("Your Szurubooru server is reachable and authentication works")
                else:
                    print("API connection failed")
                    print("Check your server URL, username, and credentials")
                    
        except Exception as e:
            print(f"Connection test failed with error: {e}")
            print(f"Error type: {type(e).__name__}")
            print("Common fixes:")
            print("   - Verify server URL is correct and accessible")
            print("   - Check if Szurubooru server is running")
            print("   - Verify API token/credentials are valid")
            print("   - Check network connectivity")
            print(f"Full traceback: {traceback.format_exc()}")
        
        return
    
    # Create media manager
    manager = MediaManager(config)
    
    # Display configuration info
    print(f"Configuration loaded from: {args.config}")
    print(f"Processing mode: {args.mode}")
    if hasattr(config, 'max_concurrent_uploads'):
        print(f"Max concurrent uploads: {config.max_concurrent_uploads}")
        print(f"GPU batch size: {config.gpu_batch_size}")
    
    async def run_selected_mode():
        """Run the selected mode based on args.mode"""
        if args.mode == "optimized":
            await manager.run_optimized_cycle()
        elif args.mode == "upload":
            await manager.run_upload_only_cycle()
        elif args.mode == "tag":
            tagged_count = await manager.auto_tag_posts()
            print(f"Tagged {tagged_count} posts")
        elif args.mode == "untagged":
            processed_count = await manager.process_untagged_posts()
            print(f"Processed {processed_count} untagged posts")
        elif args.mode == "add-characters":
            processed_count = await manager.add_characters_to_all_posts(
                start_post_id=args.start_post,
                end_post_id=args.end_post
            )
            print(f"Added character tags to {processed_count} posts")
        elif args.mode == "full":
            # Legacy full cycle for compatibility
            await manager.run_optimized_cycle()
        elif args.mode == "legacy":
            print("Running in legacy mode (slower performance)")
            # Keep old method names for backward compatibility
            files = await manager.scan_upload_directory()
            if files:
                print("Processing files sequentially...")
    
    # Run based on mode
    if args.schedule:
        # Scheduled mode
        print(f"Starting scheduled mode: {args.schedule}")
        print(f"Mode: {args.mode} (will run on schedule)")
        
        # Parse cron expression
        import croniter
        from datetime import datetime
        
        # Create cron iterator
        cron = croniter.croniter(args.schedule, datetime.now())
        
        # Run initial cycle
        print("Running initial cycle...")
        await run_selected_mode()
        
        # Keep running with scheduled tasks
        while True:
            # Get next run time
            next_run = cron.get_next(datetime)
            now = datetime.now()
            
            # Calculate seconds until next run
            seconds_until_next = (next_run - now).total_seconds()
            
            print(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S')} (in {seconds_until_next:.0f} seconds)")
            
            # Sleep until next run time
            if seconds_until_next > 0:
                await asyncio.sleep(seconds_until_next)
            
            # Run the scheduled task
            print(f"Running scheduled task at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...")
            await run_selected_mode()
    else:
        # Single run mode
        await run_selected_mode()
        
        if args.benchmark:
            print("\nPerformance benchmark completed!")
            print("Try running with different --mode options to compare performance.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)
