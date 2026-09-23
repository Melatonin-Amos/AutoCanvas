"""Portable, private local credentials configuration; never exposed through APIs."""
import os
import yaml


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore:
    def __init__(self, session_file):
        self.path = session_file.with_name('credentials.yml')

    def read(self):
        if not self.path.exists():
            return {}
        try:
            value = yaml.safe_load(self.path.read_text(encoding='utf-8'))
            if value is None:
                value = {}
            if not isinstance(value, dict):
                raise ValueError()
            if 'enabled' in value and type(value['enabled']) is not bool:
                raise ValueError()
            if any(k in value and not isinstance(value[k], str) for k in ('username', 'password')):
                raise ValueError()
            return value
        except (ValueError, OSError, yaml.YAMLError):
            raise CredentialStoreError('credentials.yml 格式无效或无法读取；用户名和密码请用引号包裹') from None

    def metadata(self):
        value = self.read()
        return {'enabled': bool(value) and value.get('enabled', True),
                'configured': bool(value.get('username') and value.get('password')),
                'config_revision': self.path.stat().st_mtime_ns if self.path.exists() else 0}

    def get(self):
        value = self.read()
        if not value or not value.get('enabled', True):
            return None
        if not value.get('username') or not value.get('password'):
            return None
        return {'username': value['username'], 'password': value['password']}

    def write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write('# jAccount 自动登录；账号密码仅保存在本机。\n')
            handle.write('# 验证码图片发送至 https://geek.sjtu.edu.cn/captcha-solver/ 识别。\n')
            yaml.safe_dump(value, handle, allow_unicode=True, sort_keys=False)
        temporary.replace(self.path)
        self.path.chmod(0o600)

    def set(self, username, password):
        self.write({'enabled': True, 'username': username, 'password': password})

    def disable(self):
        self.write({**self.read(), 'enabled': False})

    def delete(self):
        self.path.unlink(missing_ok=True)
