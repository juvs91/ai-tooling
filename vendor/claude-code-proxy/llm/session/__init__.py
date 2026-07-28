# llm/session/ — session-scoped state, split out of the former llm/compressor.py
# monolith (ADR-0032). Each module owns one concern; all share the single
# _session_cache/_state_lock defined in store.py.
