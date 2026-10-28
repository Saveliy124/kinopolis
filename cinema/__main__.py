from __future__ import annotations

import argparse
import logging
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from .config import Config
from .security import verify_password
from .web import create_application


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    request_queue_size = 128
    allow_reuse_address = True


def has_default_admin_password(app) -> bool:
    with app.service.database.connection() as connection:
        hashes = connection.execute(
            "SELECT password_hash FROM users WHERE role = 'admin'"
        ).fetchall()
    return any(verify_password("Admin123!", row["password_hash"]) for row in hashes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Веб-приложение кинотеатра")
    parser.add_argument("--host", help="Адрес сервера")
    parser.add_argument("--port", type=int, help="Порт сервера")
    parser.add_argument(
        "--init-only", action="store_true", help="Создать БД и завершить работу"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = Config.from_env()
    host = args.host or config.host
    network_accessible = host not in {"127.0.0.1", "localhost", "::1"}
    if network_accessible and config.admin_password == "Admin123!":
        parser.error("Для сетевого запуска задайте собственный ADMIN_PASSWORD")
    app = create_application(config)
    if network_accessible and has_default_admin_password(app):
        parser.error(
            "В базе остался администратор с учебным паролем Admin123!: "
            "смените его перед сетевым запуском"
        )
    if args.init_only:
        print(f"База данных инициализирована: {config.database_path}")
        return

    port = args.port or config.port
    with make_server(host, port, app, server_class=ThreadingWSGIServer) as server:
        print(f"Кинотеатр запущен: http://{host}:{port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nКинотеатр остановлен")


if __name__ == "__main__":
    main()
