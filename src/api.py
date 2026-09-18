"""Compatibility import for the application model workflows.

Alias the module object so private helpers, caches and monkeypatches are shared.
Product code imports application.model_workflows directly.
"""
import sys
from application import model_workflows as _implementation
sys.modules[__name__] = _implementation
