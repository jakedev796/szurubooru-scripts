#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Performance metrics module for Szurubooru Manager
"""

from dataclasses import dataclass


@dataclass 
class PerformanceMetrics:
    """Track performance metrics"""
    files_uploaded: int = 0
    files_tagged: int = 0
    files_failed: int = 0
    upload_rate: float = 0.0
    tagging_rate: float = 0.0
    start_time: float = 0.0
    
    def calculate_rates(self, elapsed_time: float):
        if elapsed_time > 0:
            total_processed = self.files_uploaded + self.files_tagged + self.files_failed
            self.upload_rate = self.files_uploaded / elapsed_time if elapsed_time > 0 else 0
            self.tagging_rate = self.files_tagged / elapsed_time if elapsed_time > 0 else 0

