#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration module for Szurubooru Manager
Handles configuration loading, creation, and validation
"""

import json
import logging
from pathlib import Path
from typing import List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Configuration class for Szurubooru manager"""
    szurubooru_url: str
    username: str
    upload_directory: str
    supported_extensions: List[str]
    api_token: str = None
    password: str = None  # Fallback for backward compatibility
    tagme_tag: str = "tagme"
    video_tag: str = "video"
    skip_problematic_videos: bool = False
    batch_size: int = 10
    max_workers: int = 4
    gpu_enabled: bool = True
    confidence_threshold: float = 0.5
    max_tags_per_image: int = 20
    delete_after_upload: bool = True
    retry_attempts: int = 3
    retry_delay: float = 1.0
    # Performance optimization settings
    max_concurrent_uploads: int = 12
    gpu_batch_size: int = 8
    upload_workers: int = 8
    tagging_workers: int = 2
    pipeline_enabled: bool = True
    connection_pool_size: int = 20
    upload_timeout: float = 30.0
    tagging_timeout: float = 60.0
    # Batched file discovery settings
    batch_discovery_size: int = 1000
    skip_processed_files: bool = True
    # Debug settings
    debug_api_errors: bool = False
    # Processed file tracking
    track_processed_files: bool = True


def load_config(config_path: str) -> Config:
    """Load configuration from JSON file"""
    try:
        with open(config_path, 'r') as f:
            config_data = json.load(f)
        
        # Filter out comment fields that start with underscore
        filtered_config = {k: v for k, v in config_data.items() if not k.startswith('_')}
        
        return Config(**filtered_config)
    except Exception as e:
        logger.error(f"Failed to load config from {config_path}: {e}")
        raise


def create_default_config(config_path: str):
    """Create a default optimized configuration file"""
    default_config = {
        "szurubooru_url": "http://localhost:8080",
        "username": "your_username",
        "api_token": "your_api_token_here",
        "upload_directory": "./uploads",
        "supported_extensions": ["jpg", "jpeg", "png", "gif", "webm", "mp4", "webp"],
        "tagme_tag": "tagme",
        "video_tag": "video",
        "skip_problematic_videos": True,
        "batch_size": 0,
        "max_workers": 4,
        "gpu_enabled": True,
        "confidence_threshold": 0.5,
        "max_tags_per_image": 20,
        "delete_after_upload": True,
        "retry_attempts": 3,
        "retry_delay": 1.0,
        # Performance optimization settings
        "max_concurrent_uploads": 12,
        "gpu_batch_size": 8,
        "upload_workers": 8,
        "tagging_workers": 2,
        "pipeline_enabled": True,
        "connection_pool_size": 20,
        "upload_timeout": 30.0,
        "tagging_timeout": 60.0,
        # Batched file discovery settings
        "batch_discovery_size": 1000,
        "skip_processed_files": True,
        # Debug settings
        "debug_api_errors": False,
        # Processed file tracking
        "track_processed_files": True
    }
    
    with open(config_path, 'w') as f:
        json.dump(default_config, f, indent=2)
    
    logger.info(f"Created optimized config file: {config_path}")
    print(f"Created optimized configuration file: {config_path}")
    print("Key performance settings:")
    print(f"   - Max concurrent uploads: {default_config['max_concurrent_uploads']}")
    print(f"   - GPU batch size: {default_config['gpu_batch_size']}")
    print(f"   - Pipeline enabled: {default_config['pipeline_enabled']}")

