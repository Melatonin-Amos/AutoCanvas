"""Application-level inference arbitration and cancellable blocking calls."""
import asyncio
import itertools
import math


async def blocking(function, *args, **kwargs):
    """Do not leave a thread mutating files after its async owner has exited."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


class Inference:
    """One model owner; queued replay utterances share a batch, live jobs stay single."""
    def __init__(self, recognize, recognize_batch=None, batch_limit=None):
        self.recognize = recognize
        self.recognize_batch = recognize_batch
        self.batch_limit = batch_limit or (lambda: 8)
        self.queue = asyncio.PriorityQueue()
        self.sequence = itertools.count()
        self.worker = None
        self.busy = False

    async def transcribe(self, chunk, *, live=False, context='', replay=False):
        if self.worker is None:
            self.worker = asyncio.create_task(self._consume())
        future = asyncio.get_running_loop().create_future()
        options = {'context': context, 'replay': True} if replay else {}
        await self.queue.put((0 if live else 1, next(self.sequence), chunk, options, future))
        return await future

    async def _consume(self):
        while True:
            job = await self.queue.get()
            jobs = [job]
            try:
                _, _, chunk, options, future = job
                if future.cancelled():
                    continue
                self.busy = True
                batch = self.recognize_batch
                limit = self.batch_limit()
                if batch is not None and options.get('replay') and limit > 1:
                    candidates = []
                    # Keep the oldest job first, then reduce padding within bounded read-ahead.
                    for _ in range(min(32, self.queue.qsize())):
                        candidate = self.queue.get_nowait()
                        if candidate[-1].cancelled():
                            self.queue.task_done()
                        elif candidate[0] != 1 or not candidate[3].get('replay'):
                            self.queue.put_nowait(candidate)
                            self.queue.task_done()
                            break
                        else:
                            candidates.append(candidate)
                    candidates.sort(key=lambda row: abs(math.log(max(row[2].duration, .001)/max(chunk.duration, .001)))
                                    + .2*(row[3].get('context') != options.get('context')))
                    jobs.extend(candidates[:limit-1])
                    for deferred in candidates[limit-1:]:
                        self.queue.put_nowait(deferred)
                        self.queue.task_done()
                if len(jobs) > 1:
                    results = await blocking(batch, [j[2] for j in jobs],
                                             **{**options, 'context': [j[3].get('context', '') for j in jobs]})
                    if len(results) != len(jobs):
                        raise RuntimeError('ASR returned an incomplete batch')
                else:
                    results = [await blocking(self.recognize, chunk, **options)]
                for queued, result in zip(jobs, results):
                    if not queued[-1].done():
                        queued[-1].set_result(result)
            except asyncio.CancelledError:
                for queued in jobs:
                    queued[-1].cancel()
                raise
            except Exception as error:
                for queued in jobs:
                    if not queued[-1].done():
                        queued[-1].set_exception(error)
            finally:
                self.busy = False
                for _ in jobs:
                    self.queue.task_done()

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        while not self.queue.empty():
            *_, future = self.queue.get_nowait()
            future.cancel()
            self.queue.task_done()
