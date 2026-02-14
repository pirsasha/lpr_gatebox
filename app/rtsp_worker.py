# =========================================================
# Файл: app/rtsp_worker.py
# Проект: LPR GateBox
# Версия: v0.3.x
# Изменено: 2026-02-11
# Автор: Александр
# Что сделано:
# - CHG: тонкий лаунчер для модульного rtsp_worker (логика в app/worker/runner_impl.py)
# =========================================================

from __future__ import annotations


def main() -> None:
    from app.worker.runner_impl import main as _main
    _main()


if __name__ == "__main__":
    main()
