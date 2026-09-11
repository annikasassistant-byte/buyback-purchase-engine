"""HTTP front door onto the engine - see ``purchase_engine.api.app``.

Only entry point that imports from ``adapters.query`` (the API-only read
side) as well as the engine's own ``pipeline``/``adapters.store``. Nothing
under ``pipeline`` or ``domain`` imports anything from this package - the
dependency runs one way, same discipline as ``cli.py``.
"""
