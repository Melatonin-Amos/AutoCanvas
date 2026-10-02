"""Local audio -> text; no networking, media decoding, scheduling or persistence."""
from threading import Lock
from pathlib import Path
from .types import AudioChunk, TranscriptSegment


class Recognizer:
    def __init__(self, model='Qwen/Qwen3-ASR-0.6B', device='mps', language='Chinese', silence=0.005):
        self.model_name, self.device, self.language, self.silence = model, device, language, silence
        self._model = None
        self._lock = Lock()
        self.batch_limit = 8 if device.startswith('cuda') else 1

    def transcribe(self, chunk: AudioChunk, *, context='', replay=False):
        return self.transcribe_batch([chunk], context=context, replay=replay)[0]

    def transcribe_batch(self, chunks, *, context='', replay=True):
        """Batch whole utterances, retaining order and skipping only negligible audio."""
        import numpy as np
        chunks = list(chunks)
        contexts = [context]*len(chunks) if isinstance(context, str) else list(context)
        if len(contexts) != len(chunks):
            raise ValueError('ASR context count must match audio count')
        output = [TranscriptSegment(c.start, c.start+c.duration, '') for c in chunks]
        active = []
        for index, chunk in enumerate(chunks):
            audio = np.frombuffer(chunk.pcm, dtype='<i2').astype(np.float32) / 32768
            if len(audio) and np.max(np.abs(audio)) >= self.silence:
                active.append((index, audio, chunk))
        if not active:
            return output
        with self._lock:
            if self._model is None:
                import torch
                from qwen_asr import Qwen3ASRModel
                dtype = torch.bfloat16 if self.device.startswith('cuda') and torch.cuda.is_bf16_supported() else torch.float32
                model_path = Path(self.model_name).expanduser()
                if not model_path.is_dir():
                    from huggingface_hub import snapshot_download
                    model_path = Path(snapshot_download(self.model_name, local_files_only=True))
                # The SDK's processor otherwise may check the Hub even with an offline model.
                self._model = Qwen3ASRModel.from_pretrained(str(model_path), device_map=self.device,
                    dtype=dtype, local_files_only=True, max_inference_batch_size=1, max_new_tokens=768)
            self._model.max_new_tokens = 768 if replay else 512
            # Similar lengths share padding; long windows use smaller batches on an 8 GB GPU.
            active.sort(key=lambda row: row[2].duration, reverse=True)
            while active:
                limit = min(self.batch_limit, max(1, int(300/max(active[0][2].duration, 1))))
                group, active = active[:limit], active[limit:]
                for row, result in zip(group, self._decode(group, contexts)):
                    index, _, chunk = row
                    output[index] = TranscriptSegment(chunk.start, chunk.start+chunk.duration, result.text.strip())
        return output

    def _decode(self, group, contexts):
        import torch
        self._model.max_inference_batch_size = len(group)
        try:
            with torch.inference_mode():
                result = self._model.transcribe(audio=[(audio, chunk.sample_rate) for _, audio, chunk in group],
                                               language=self.language, context=[contexts[index] for index, _, _ in group])
            if len(result) != len(group):
                raise RuntimeError('ASR returned an incomplete batch')
            return result
        except torch.cuda.OutOfMemoryError:
            if len(group) == 1:
                raise
            self.batch_limit = min(self.batch_limit, max(1, len(group)//2))
        # Release the failed call's traceback before retrying; never lose a window on OOM.
        torch.cuda.empty_cache()
        split = len(group)//2
        return self._decode(group[:split], contexts)+self._decode(group[split:], contexts)
