import functools
from multiprocessing import Process, Queue
import numpy as np
import queue
import random
import torch
import traceback


@functools.lru_cache(None)
def patch_torch_manual_seed():
  """Make torch manual seed deterministic. Helps with accuracy testing."""

  def deterministic_torch_manual_seed(*args, **kwargs):
    from torch._C import default_generator

    seed = 1337
    import torch.cuda

    if not torch.cuda._is_in_bad_fork():
      torch.cuda.manual_seed_all(seed)
    return default_generator.manual_seed(seed)

  torch.manual_seed = deterministic_torch_manual_seed

def reset_rng_state():
  torch.manual_seed(1337)
  random.seed(1337)
  np.random.seed(1337)

@functools.lru_cache(maxsize=1)
def is_tpu_available():

  def _check_tpu(q):
    try:
      import os
      os.environ["PJRT_DEVICE"] = "TPU"

      import torch_xla.core.xla_model as xm

      q.put((xm.xrt_world_size() > 1) or bool(xm.get_xla_supported_devices("TPU")))
    except Exception:
      traceback.print_exc()
      q.put(None)

  q = Queue()
  process = Process(target=_check_tpu, args=(q,))
  process.start()
  process.join(60)
  try:
    return q.get_nowait()
  except queue.Empty:
    traceback.print_exc()
    return False
