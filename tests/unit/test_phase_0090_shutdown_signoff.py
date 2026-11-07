"""PHASE 0090: shutdown area sign-off via import-safe entry module."""
import asyncio
import inspect

import run_vertical_slice as entry


def test_entry_module_import_safe_and_main_async():
    assert inspect.iscoroutinefunction(entry.main)
    assert callable(entry.create_server_config)
    assert asyncio.iscoroutinefunction(entry.main)
