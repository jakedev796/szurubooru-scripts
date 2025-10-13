#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Szurubooru Manager Components
Modular components for the Szurubooru Media Manager
"""

from .config import Config, load_config, create_default_config
from .api import SzurubooruAPI
from .tagger import WDTaggerManager, WD14_AVAILABLE
from .metrics import PerformanceMetrics
from .manager import MediaManager
from .utils import is_video_file, is_image_file, check_video_file, MAGIC_AVAILABLE

__all__ = [
    'Config',
    'load_config',
    'create_default_config',
    'SzurubooruAPI',
    'WDTaggerManager',
    'WD14_AVAILABLE',
    'PerformanceMetrics',
    'MediaManager',
    'is_video_file',
    'is_image_file',
    'check_video_file',
    'MAGIC_AVAILABLE',
]

