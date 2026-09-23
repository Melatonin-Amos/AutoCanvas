"""Noninteractive jAccount login challenges. No web framework dependency."""
import base64
import secrets
import time
from threading import RLock
from . import _jaccount
from .types import AuthenticationRequired, RemoteError


class BrowserAuth:
    def __init__(self, auth):
        self.auth = auth
        self.pending = {}
        self.lock = RLock()

    def status(self):
        try:
            session = self.auth.session()
            session.close()
            return {'authenticated': True}
        except AuthenticationRequired:
            return {'authenticated': False}
        except RemoteError:
            return {'authenticated': None, 'error': '登录服务暂不可用，等待重试'}

    def automatic_status(self):
        with self.auth._lock:
            return self.auth.automatic.state()

    def configure_automatic(self, username, password):
        with self.auth._lock:
            return self.auth.automatic.configure(username, password)

    def test_automatic(self):
        with self.auth._lock:
            session = self.auth.automatic.login()
            session.close()
            return {'authenticated': True, 'automatic': self.auth.automatic.state()}

    def disable_automatic(self, *, forget=False):
        with self.auth._lock:
            return self.auth.automatic.disable(forget=forget)

    def begin(self):
        with self.lock:
            self._clear()
            session = _jaccount.create_session()
            try:
                url, html = _jaccount._get_jaccount_login_page(session)
                params, uuid, has_captcha = _jaccount._parse_login_form(html, url)
                image = None
                if has_captcha:
                    if not uuid:
                        raise AuthenticationRequired('无法获取验证码，请重试')
                    response = session.get('https://jaccount.sjtu.edu.cn/jaccount/captcha',
                                           params={'uuid': uuid, 't': int(time.time()*1000)}, headers={'Referer': url}, timeout=15)
                    response.raise_for_status()
                    if not response.headers.get('Content-Type', '').startswith('image/'):
                        raise AuthenticationRequired('验证码返回格式异常')
                    image = 'data:image/jpeg;base64,' + base64.b64encode(response.content).decode()
                key = secrets.token_urlsafe(24)
                self.pending[key] = (time.monotonic()+300, session, url, params, uuid)
                return {'challenge_id': key, 'captcha': image, 'expires_in': 300}
            except BaseException:
                session.close()
                raise

    def submit(self, key, username, password, captcha=''):
        with self.lock:
            state = self.pending.pop(key, None)
            if state is None:
                raise AuthenticationRequired('登录请求已失效，请刷新验证码')
            deadline, session, url, params, uuid = state
            try:
                if time.monotonic() > deadline:
                    raise AuthenticationRequired('验证码已过期，请重新获取')
                if not username or not password:
                    raise ValueError('请输入用户名和密码')
                _jaccount._submit_login(session, params, uuid, username, password, captcha, url)
                if not _jaccount.is_session_valid(session):
                    raise AuthenticationRequired('登录失败，请检查账号或验证码并重新获取验证码')
                with self.auth._lock:
                    _jaccount.save_session(session, self.auth.session_file)
                return {'authenticated': True}
            finally:
                session.close()

    def import_session(self, cookies):
        if not isinstance(cookies, list) or not 0 < len(cookies) <= 200:
            raise ValueError('请选择有效的 Canvas 会话 JSON 文件')
        session = _jaccount.create_session()
        try:
            for cookie in cookies:
                if not isinstance(cookie, dict):
                    raise ValueError('会话格式无效')
                domain = cookie.get('domain', '')
                if not isinstance(domain, str) or not (domain == 'sjtu.edu.cn' or domain.endswith('.sjtu.edu.cn')):
                    raise ValueError('会话包含不支持的域名')
                if not isinstance(cookie.get('name'), str) or not isinstance(cookie.get('value'), str):
                    raise ValueError('会话格式无效')
                session.cookies.set(cookie['name'], cookie['value'], domain=domain, path=cookie.get('path','/'))
            if not _jaccount.is_session_valid(session):
                raise AuthenticationRequired('导入的会话已失效')
            with self.auth._lock:
                _jaccount.save_session(session, self.auth.session_file)
            return {'authenticated': True}
        finally:
            session.close()

    def logout(self):
        with self.lock, self.auth._lock:
            self._clear()
            self.auth.automatic.disable()
            self.auth.session_file.unlink(missing_ok=True)
        return {'authenticated': False}

    def _clear(self):
        for _, session, *_ in self.pending.values():
            session.close()
        self.pending.clear()

    def close(self):
        with self.lock:
            self._clear()
