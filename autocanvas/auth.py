"""Authentication only: Canvas sessions and course-scoped LTI credentials."""
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from urllib.parse import urljoin, urlsplit, parse_qs
from bs4 import BeautifulSoup
from requests.exceptions import RequestException
from . import _jaccount
from .automatic_login import AutomaticLogin
from .types import AuthenticationRequired, RemoteError


@dataclass
class VideoCredential:
    session: object = field(repr=False)
    token: str = field(repr=False)


class Auth:
    def __init__(self, session_file: Path):
        self.session_file = session_file
        self._lock = RLock()
        self.automatic = AutomaticLogin(session_file)

    def session(self, *, interactive=False):
        with self._lock:
            try:
                try:
                    return _jaccount.ensure_session(self.session_file, auto_prompt=False)
                except _jaccount.SessionExpired:
                    if self.automatic.state()['enabled']:
                        return self.automatic.login()
                    if interactive:
                        return _jaccount.ensure_session(self.session_file, auto_prompt=True)
                    raise AuthenticationRequired('需要配置自动登录或手动登录') from None
            except RequestException as error:
                raise RemoteError('登录网络暂不可用', stage='canvas_session', code=type(error).__name__) from None
            except _jaccount.LoginProtocolError:
                raise RemoteError('登录响应格式异常', stage='canvas_session', code='invalid_response') from None

    def video(self, course_id: str):
        session = self.session()
        try:
            response = session.get(f"https://oc.sjtu.edu.cn/courses/{int(course_id)}/external_tools/8329", timeout=30)
            for _ in range(4):
                if response.status_code in (401, 403):
                    raise AuthenticationRequired('Video launch authorization rejected')
                if response.status_code != 200:
                    raise RemoteError('Video launch HTTP error', stage='video_launch', code=response.status_code)
                url = urlsplit(response.url)
                if url.hostname == 'jaccount.sjtu.edu.cn' or (url.hostname == 'oc.sjtu.edu.cn' and url.path.startswith('/login')):
                    raise AuthenticationRequired('Video launch redirected to login')
                token = parse_qs(url.fragment.partition("?")[2]).get("jwt_token")
                if url.hostname == "v.sjtu.edu.cn" and token:
                    return VideoCredential(session, token[0])
                forms = []
                for form in BeautifulSoup(response.text, "html.parser").find_all("form"):
                    action = urljoin(response.url, form.get("action", ""))
                    target = urlsplit(action)
                    if target.scheme == "https" and target.hostname == "v.sjtu.edu.cn" and target.path.startswith("/jy-lti-adapter/lti/canvas/"):
                        forms.append((form, action))
                if len(forms) != 1:
                    raise RemoteError('No recognized video launch form', stage='video_launch', code='unexpected_form')
                form, action = forms[0]
                fields = {i['name']: i.get('value', '') for i in form.find_all('input', attrs={'name': True})}
                response = session.post(action, data=fields, timeout=30)
            raise RemoteError('Video launch redirect limit', stage='video_launch', code='redirect_limit')
        except RequestException as error:
            session.close()
            raise RemoteError('Video launch transport failed', stage='video_launch', code=type(error).__name__) from None
        except BaseException:
            session.close()
            raise
