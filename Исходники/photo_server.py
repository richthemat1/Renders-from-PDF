"""Local preview server: serves result folders on 127.0.0.1 and applies manual edits from the browser."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import secrets
from threading import Event, Lock, Thread
from urllib.parse import quote, unquote, urlsplit

from photo_edit import EditConflict, EditError, apply_edits, load_report
from photo_export import VERSION, write_gallery

LOGGER = logging.getLogger('pdf-photos')
MAX_BODY = 1 << 20


class PreviewServer:
    def __init__(self):
        self.folders, self.lock, self.httpd = {}, Lock(), None
        self.closing = Event()

    @property
    def origin(self):
        return f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def start(self):
        if self.httpd is None:
            self.closing.clear()
            self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler(self))
            self.httpd.daemon_threads = True
            Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.httpd is not None:
            self.closing.set()
            httpd = self.httpd
            httpd.shutdown()
            # Idle browser connections remain daemon threads, but a disk transaction
            # must finish (or roll back) before the application's main thread exits.
            with self.lock:
                httpd.server_close()
                self.httpd = None

    def register(self, folder):
        """Open a result folder for this session; the key in the URL keeps other folders and sites out."""
        folder = Path(folder).resolve()
        report = load_report(folder)
        gallery = folder/'Просмотр.html'
        if not gallery.is_file() or f'data-gallery-version="{VERSION}"' not in gallery.read_text(encoding='utf-8', errors='replace'):
            write_gallery(folder, report)
        key = next((k for k, f in self.folders.items() if f == folder), None) or secrets.token_urlsafe(18)
        self.folders[key] = folder
        return f'{self.start().origin}/{key}/{quote("Просмотр.html")}'


def handler(server):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, format, *args):
            LOGGER.info('preview: '+format, *args)

        def target(self):
            parts = unquote(urlsplit(self.path).path).lstrip('/').split('/', 1)
            folder = server.folders.get(parts[0])
            return (folder, parts[1] if len(parts) > 1 else '') if folder else (None, None)

        def reply(self, status, body, content_type='application/json; charset=utf-8'):
            if isinstance(body, dict):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def do_GET(self):
            folder, relative = self.target()
            if folder is None:
                return self.reply(404, {'error': 'Не найдено.'})
            if relative == 'api/state':
                return self.reply(200, {'revision': load_report(folder)['revision']})
            path = (folder/relative).resolve()
            if not path.is_relative_to(folder) or not path.is_file():
                return self.reply(404, {'error': 'Файл не найден.'})
            content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            if content_type.startswith('text/'):
                content_type += '; charset=utf-8'
            self.reply(200, path.read_bytes(), content_type)

        do_HEAD = do_GET

        def do_POST(self):
            folder, relative = self.target()
            if folder is None or relative != 'api/apply':
                return self.reply(404, {'error': 'Не найдено.'})
            if self.headers.get('Origin') != server.origin:
                return self.reply(403, {'error': 'Запрос отклонён: он пришёл не со страницы просмотра.'})
            length = int(self.headers.get('Content-Length') or 0)
            if not 0 < length <= MAX_BODY or not self.headers.get('Content-Type', '').startswith('application/json'):
                return self.reply(400, {'error': 'Некорректный запрос.'})
            try:
                payload = json.loads(self.rfile.read(length))
                with server.lock:
                    if server.closing.is_set():
                        return self.reply(503, {'error': 'Программа закрывается. Откройте её и повторите сохранение.'})
                    report, backup = apply_edits(folder, payload)
            except EditConflict as error:
                return self.reply(409, {'error': str(error)})
            except (EditError, json.JSONDecodeError) as error:
                return self.reply(400, {'error': str(error)})
            except Exception as error:
                LOGGER.exception('Edit failed: %s', folder)
                return self.reply(500, {'error': f'Не удалось сохранить: {error}. Файлы восстановлены.'})
            LOGGER.info('Edited %s: %s', folder, report.get('edits', [{}])[-1])
            message = ('Изменений нет.' if backup is None else
                       f'Сохранено. Прежние файлы — в папке «{backup}».')
            self.reply(200, {'ok': True, 'revision': report['revision'], 'backup': backup, 'message': message})

    return Handler
