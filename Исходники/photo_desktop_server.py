"""Token-scoped desktop frontend and API sharing the existing transactional PDF editor."""
from http.server import ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import secrets
from threading import Lock, RLock, Thread
from urllib.parse import unquote, urlsplit

from photo_edit import EditConflict, EditError, apply_edits, rename_item
from photo_jobs import normalized_report, read_result
from photo_server import MAX_BODY, PreviewServer, handler as preview_handler

LOGGER = logging.getLogger('pdf-photos')


class DesktopServer(PreviewServer):
    def __init__(self, jobs, frontend_path):
        super().__init__()
        self.jobs = jobs
        self.frontend_path = Path(frontend_path).resolve()
        self.lock, self._lifecycle_lock = RLock(), Lock()
        self.token = secrets.token_urlsafe(24)
        self._origin, self._stopped = None, False

    @property
    def origin(self):
        if self._origin is None:
            raise RuntimeError('Локальный сервер ещё не запущен.')
        return self._origin

    @property
    def url(self):
        if self._origin is None:
            self.start()
        return f'{self.origin}/app/{self.token}/'

    def start(self):
        with self._lifecycle_lock:
            if self._stopped:
                raise RuntimeError('Локальный сервер уже остановлен.')
            if self.httpd is None:
                self.closing.clear()
                self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), desktop_handler(self))
                self.httpd.daemon_threads = True
                self._origin = f'http://127.0.0.1:{self.httpd.server_address[1]}'
                Thread(target=self.httpd.serve_forever, daemon=True, name='pdf-photos-http').start()
        return self

    def stop(self):
        with self._lifecycle_lock:
            self.closing.set()
            self._stopped = True
            if self.httpd is not None:
                httpd = self.httpd
                httpd.shutdown()
                # The same lock protects PDF transactions, their fresh wrapper/history,
                # and shutdown. No commit can outlive the native application's close.
                with self.lock:
                    httpd.server_close()
                    self.httpd = None

    def register_result(self, folder):
        self.start()
        with self.lock:
            if self.closing.is_set():
                raise RuntimeError('Программа закрывается. Открытие результата недоступно.')
            return self._register(folder)

    def _register(self, folder, report=None):
        folder = Path(folder).resolve()
        report = read_result(folder) if report is None else normalized_report(report, folder)
        key = next((key for key, existing in self.folders.items() if existing == folder), None)
        key = key or secrets.token_urlsafe(18)
        self.folders[key] = folder
        self.jobs.remember(report)
        return self.result(key, report)

    def result(self, id, report=None):
        """Call under the transaction lock so report and file paths describe one revision."""
        folder = self.folders[id]
        report = read_result(folder) if report is None else normalized_report(report, folder)
        return {'id': id, 'base_url': f'{self.origin}/{id}/',
                'title': Path(report['source']).stem, 'report': report}


def desktop_handler(server):
    class Handler(preview_handler(server)):
        def error(self, status, message):
            return self.reply(status, {'error': message, 'message': message})

        def route(self):
            path = unquote(urlsplit(self.path).path)
            prefix = f'/app/{server.token}/'
            if path == prefix[:-1]:
                return 'app', ''
            if path.startswith(prefix):
                return 'app', path[len(prefix):]
            parts = path.lstrip('/').split('/', 1)
            if parts[0] in server.folders:
                return parts[0], parts[1] if len(parts) > 1 else ''
            return None, None

        def file(self, root, relative):
            try:
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    return self.error(404, 'Файл не найден.')
                contents = path.read_bytes()
            except (OSError, ValueError):
                return self.error(404, 'Файл не найден.')
            content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            if content_type.startswith('text/') or content_type in ('application/javascript', 'application/json'):
                content_type += '; charset=utf-8'
            return self.reply(200, contents, content_type)

        def do_GET(self):
            kind, relative = self.route()
            if kind is None:
                return self.error(404, 'Не найдено.')
            if relative.startswith('api/'):
                origin = self.headers.get('Origin')
                if origin is not None and origin != server.origin:
                    return self.error(403, 'Запрос отклонён: он пришёл не из окна приложения.')
            try:
                with server.lock:
                    if kind == 'app':
                        if relative == 'api/state':
                            return self.reply(200, server.jobs.snapshot())
                        parts = relative.split('/')
                        if len(parts) == 3 and parts[:2] == ['api', 'results']:
                            if parts[2] not in server.folders:
                                return self.error(404, 'Результат не найден.')
                            return self.reply(200, server.result(parts[2]))
                        if relative.startswith('api/'):
                            return self.error(404, 'Не найдено.')
                        return self.file(server.frontend_path, relative or 'index.html')
                    if relative == 'api/state':
                        return self.reply(200, {'revision': read_result(server.folders[kind])['revision']})
                    return self.file(server.folders[kind], relative)
            except (OSError, ValueError, TypeError, KeyError) as error:
                return self.error(400, f'Не удалось открыть результат: {error}')

        do_HEAD = do_GET

        def body(self):
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = -1
            if length > MAX_BODY:
                # Discard oversized input in bounded chunks. Closing with unread input
                # loses the 400 response to a TCP reset on Windows.
                self.close_connection = True
                previous_timeout = self.connection.gettimeout()
                self.connection.settimeout(2)
                try:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(remaining, 65536))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                except OSError:
                    pass
                finally:
                    self.connection.settimeout(previous_timeout)
                return None
            if length <= 0 or self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                return None
            # Drain permitted bodies even on a rejected Origin/token. Otherwise Windows
            # may reset the socket before the caller receives the actual HTTP response.
            return self.rfile.read(length)

        def do_POST(self):
            body = self.body()
            kind, relative = self.route()
            if kind is None:
                return self.error(404, 'Не найдено.')
            if kind == 'app':
                parts = relative.split('/')
                operation = relative[4:] if relative in ('api/cancel', 'api/settings', 'api/open') else None
                result_id = None
                if (len(parts) == 4 and parts[:2] == ['api', 'results']
                        and parts[3] in ('apply', 'rename')):
                    operation, result_id = parts[3], parts[2]
            else:
                operation = relative[4:] if relative in ('api/apply', 'api/rename') else None
                result_id = kind
            if operation is None:
                return self.error(404, 'Не найдено.')
            if self.headers.get('Origin') != server.origin:
                return self.error(403, 'Запрос отклонён: он пришёл не из окна приложения.')
            if body is None or self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                return self.error(400, 'Нужен JSON-запрос размером не больше 1 МиБ.')
            try:
                payload = json.loads(body.decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('Некорректный формат запроса: нужен JSON-объект.')
                with server.lock:
                    if server.closing.is_set():
                        return self.error(503, 'Программа закрывается. Повторите действие после запуска приложения.')
                    if operation == 'cancel':
                        return self.reply(200, server.jobs.cancel())
                    if operation == 'settings':
                        if set(payload) != {'mode'} or payload['mode'] not in ('nearby', 'results'):
                            raise ValueError('Папку сохранения выбирают через диалог приложения.')
                        return self.reply(200, server.jobs.set_output(payload['mode']))
                    if operation == 'open':
                        if set(payload) != {'id'} or not isinstance(payload['id'], str):
                            raise ValueError('Укажите идентификатор результата из истории.')
                        item = next((item for item in server.jobs.snapshot()['history']
                                     if item['id'] == payload['id']), None)
                        if item is None or not item['available']:
                            return self.error(404, 'Папка результата не найдена. Выберите её через приложение.')
                        report = server.jobs.history_result(payload['id'])
                        return self.reply(200, server._register(report['output'], report))
                    if result_id not in server.folders:
                        return self.error(404, 'Результат не найден.')
                    folder = server.folders[result_id]
                    if operation == 'rename':
                        report, change = rename_item(folder, payload)
                        message = ('Имя не изменилось.' if change is None else
                                   f'Переименовано: «{change["from"]}» → «{change["to"]}».')
                        response = {'ok': True, 'revision': report['revision'], 'message': message}
                    else:
                        report, backup = apply_edits(folder, payload)
                        message = ('Изменений нет.' if backup is None else
                                   f'Сохранено. Прежние файлы — в папке «{backup}».')
                        response = {'ok': True, 'revision': report['revision'], 'message': message, 'backup': backup}
                    report = normalized_report(report, folder)
                    server.jobs.remember(report)
                    response['result'] = server.result(result_id, report)
                    return self.reply(200, response)
            except EditConflict as error:
                return self.error(409, str(error))
            except (EditError, ValueError, TypeError, KeyError, RuntimeError, UnicodeError) as error:
                return self.error(400, str(error))
            except Exception as error:
                LOGGER.exception('Desktop operation failed: %s', relative)
                return self.error(500, f'Не удалось выполнить действие: {error}. Проверьте технический журнал.')

    return Handler
