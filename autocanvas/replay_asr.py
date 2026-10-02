"""Bounded offline speech windows. No acquisition, persistence or course state."""
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import json
import math
import asyncio
from .types import AudioChunk


@dataclass(frozen=True)
class ReplayPolicy:
    target_seconds: float = 30
    max_seconds: float = 45
    min_seconds: float = 24
    silence_seconds: float = .6
    vad_model: str = ''
    context: str = ''
    model_identity: str = ''
    version: int = 3

    def validate(self):
        values = (self.min_seconds, self.target_seconds, self.max_seconds, self.silence_seconds)
        if any(not math.isfinite(v) for v in values) or not 1 <= self.min_seconds <= self.target_seconds <= self.max_seconds <= 120:
            raise ValueError('Invalid replay window bounds')
        if not .1 <= self.silence_seconds <= 5 or len(self.context) > 4000:
            raise ValueError('Invalid replay silence or context')
        return self

    def metadata(self):
        data = asdict(self)
        path = Path(self.vad_model).expanduser() if self.vad_model else None
        data['vad_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
        return data

    def fingerprint(self, **identity):
        payload = {**self.metadata(), **identity}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    @classmethod
    def from_settings(cls, settings, context=''):
        return cls(target_seconds=settings.replay_chunk_seconds,
                   min_seconds=min(24, settings.replay_chunk_seconds*.8),
                   max_seconds=settings.replay_max_seconds,
                   silence_seconds=settings.replay_silence_seconds,
                   vad_model=settings.replay_vad_model, context=context, model_identity=settings.model).validate()


class VoiceDetector:
    """One independent stateful VAD per reader, loaded only for offline processing."""
    def __init__(self, model_path=''):
        self.model = None
        if model_path:
            import torch
            self.model = torch.jit.load(str(Path(model_path).expanduser()), map_location='cpu').eval()
            self.model.reset_states()
        self.backend = 'silero' if self.model is not None else 'energy'

    def score(self, pcm):
        import numpy as np
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        if self.model is None:
            # Conservative fallback; confidence is only about voice activity, not word accuracy.
            if np.sqrt(np.mean(audio * audio)) > .003:
                return 1.0
            return .2 if np.max(np.abs(audio), initial=0) >= .005 else 0.0
        import torch
        with torch.inference_mode():
            return float(self.model(torch.from_numpy(audio), 16000).item())


@dataclass(frozen=True)
class SpeechWindow:
    chunk: AudioChunk
    speech_seconds: float
    uncertain_seconds: float
    boundary: str

    @property
    def silent(self):
        return self.speech_seconds == 0 and self.uncertain_seconds == 0


class WindowBuilder:
    """No overlap or dropped samples. Cuts near pauses, bounded by maximum length."""
    def __init__(self, policy, detector=None):
        self.policy = policy.validate()
        self.detector = detector or VoiceDetector(policy.vad_model)
        self.pending = bytearray()
        self.buffer = bytearray()
        self.start = None
        self.received_end = None
        self.speech = self.uncertain = self.quiet = 0.0

    def _emit(self, boundary):
        chunk = AudioChunk(bytes(self.buffer), 16000, self.start)
        result = SpeechWindow(chunk, self.speech, self.uncertain, boundary)
        self.start += chunk.duration
        self.buffer.clear()
        self.speech = self.uncertain = self.quiet = 0.0
        return result

    def _frame(self, frame):
        self.buffer.extend(frame)
        seconds = len(frame) / 32000
        padded = frame + b'\0' * (1024 - len(frame))
        score = self.detector.score(padded)
        if score >= .5:
            self.speech += seconds
            self.quiet = 0
        elif score >= .15:
            self.uncertain += seconds
            self.quiet = 0
        else:
            self.quiet += seconds
        duration = len(self.buffer) / 32000
        if duration >= self.policy.max_seconds:
            return self._emit('limit')
        if duration >= self.policy.min_seconds and self.quiet >= self.policy.silence_seconds:
            return self._emit('pause')
        if duration >= self.policy.target_seconds and self.quiet >= .16:
            return self._emit('near_target')
        return None

    def feed(self, chunk):
        if chunk.sample_rate != 16000 or len(chunk.pcm) % 2:
            raise ValueError('Replay expects 16 kHz signed mono PCM')
        if self.start is None:
            self.start = chunk.start
        if self.received_end is not None and abs(chunk.start - self.received_end) > .002:
            raise ValueError('Non-contiguous audio cannot share a speech window')
        self.received_end = chunk.start + chunk.duration
        self.pending.extend(chunk.pcm)
        rows = []
        while len(self.pending) >= 1024:
            frame = bytes(self.pending[:1024])
            del self.pending[:1024]
            result = self._frame(frame)
            if result is not None:
                rows.append(result)
        return rows

    def finish(self):
        rows = []
        if self.pending:
            result = self._frame(bytes(self.pending))
            self.pending.clear()
            if result is not None:
                rows.append(result)
        if self.buffer:
            rows.append(self._emit('end'))
        return rows


async def speech_windows(reader, policy, detector=None, *, call=asyncio.to_thread):
    builder = await call(WindowBuilder, policy, detector)
    try:
        async for chunk in reader:
            for window in await call(builder.feed, chunk):
                yield window
    except Exception:
        # Preserve already delivered samples; resume starts at the last committed window.
        for window in await call(builder.finish):
            yield window
        raise
    for window in await call(builder.finish):
        yield window
