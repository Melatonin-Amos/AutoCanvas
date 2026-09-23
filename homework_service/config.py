from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class Settings:
    workspace: Path = field(default_factory=lambda: Path('~/Code/HomeworkWorkspace').expanduser())
    state_directory: str = ''
    host: str = '127.0.0.1'
    port: int = 8090
    autocanvas_url: str = 'http://127.0.0.1:8080'
    sync_seconds: int = 300
    concurrency: int = 2
    default_provider: str = 'claude'
    claude: str = 'claude'
    codex: str = 'codex'
    claude_model: str = ''
    codex_model: str = ''

    def __post_init__(self):
        self.workspace = Path(self.workspace).expanduser().resolve()

    @property
    def state(self):
        return Path(self.state_directory).expanduser().resolve() if self.state_directory else self.workspace/'.homework-manager'

    @classmethod
    def load(cls, path=None):
        values = yaml.safe_load(Path(path).read_text()) if path and Path(path).exists() else {}
        values = values or {}
        if not isinstance(values, dict):
            raise ValueError('配置必须为 YAML 对象')
        settings = cls(**values)
        settings.workspace = Path(settings.workspace).expanduser().resolve()
        if not 1 <= settings.concurrency <= 16 or not 1 <= settings.port <= 65535 or settings.sync_seconds < 10:
            raise ValueError('无效端口、并发数或同步间隔')
        if settings.default_provider not in ('claude', 'codex'):
            raise ValueError('不支持的默认 agent')
        return settings
