"""Phase 6 groundwork: a local FastAPI service that launches and reports on qa/feed_verify.py
runs, plus a static single-page UI. Nothing in qa/ is imported for its side effects here -
runs are launched as a subprocess (python -m qa.feed_verify), exactly as from the terminal, so
that AJIO_USER_GROUPS (read once from the environment at import time by qa/feed_client.py) can be
set differently per run without touching that module. The only qa/ functions called in-process
are pure readers (load_banners/select_banners) used to show the banner list before/while a run
is still writing results.
"""
