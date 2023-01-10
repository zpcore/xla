import argparse
from collections import OrderedDict
import json
import logging
import numpy as np
import subprocess
import sys
import time
import torch
import torch_xla.core.xla_model as xm
import types

try:
  from .benchmark_model import TorchBenchModelLoader, ModelLoader
  from .benchmark_experiment import ExperimentLoader
  from .utils import patch_torch_manual_seed, reset_rng_state, move_to_device
except ImportError:
  from benchmark_model import TorchBenchModelLoader, ModelLoader
  from benchmark_experiment import ExperimentLoader
  from utils import patch_torch_manual_seed, reset_rng_state, move_to_device

logger = logging.getLogger(__name__)


class ExperimentRunner:

  def __init__(self, args):
    self._args = args
    self.suite_name = self._args.suite_name

    self.experiment_loader = ExperimentLoader(self._args)

    if self.suite_name == "torchbench":
      self.model_loader = TorchBenchModelLoader(self._args)
    elif self.suite_name == "dummy":
      self.model_loader = ModelLoader(self._args)
    else:
      raise NotImplementedError

    # initialize output directory from args
    # self.output_dir

  def run(self):
    if self._args.experiment_config and self._args.model_config:
      experiment_config = json.loads(self._args.experiment_config)
      model_config = json.loads(self._args.model_config)
      self.run_single_experiment(experiment_config, model_config)
    else:
      assert not self._args.experiment_config and not self._args.model_config

      experiment_configs = self.experiment_loader.list_experiment_configs()
      model_configs = self.model_loader.list_model_configs()
      for model_config in model_configs:
        for experiment_config in experiment_configs:
          if self.model_loader.is_compatible(model_config, experiment_config):
            process_env = experiment_config.pop("process_env")
            experiment_config_str = json.dumps(experiment_config)
            model_config_str = json.dumps(model_config)
            try:
              subprocess.check_call(
                  [sys.executable] + sys.argv +
                  [f"--experiment-config={experiment_config_str}"] +
                  [f"--model-config={model_config_str}"],
                  timeout=60 * 20,
                  env=process_env,
              )
            except subprocess.TimeoutExpired:
              logger.error("TIMEOUT")
            except subprocess.SubprocessError:
              logger.error("ERROR")

          else:
            logger.warning("SKIP because of incompatible configs.")

  def run_single_experiment(self, experiment_config, model_config):
    benchmark_experiment = self.experiment_loader.load_experiment(
        experiment_config
    )
    benchmark_model = self.model_loader.load_model(
        model_config, benchmark_experiment
    )

    if benchmark_experiment.test == "train":
      benchmark_model.model_iter_fn = benchmark_model.train
    elif benchmark_experiment.test == "eval":
      benchmark_model.model_iter_fn = benchmark_model.eval
    else:
      raise NotImplementedError

    timings = OrderedDict()
    results = []
    for i in range(self._args.repeat):
      timing, result = self.timed_iteration(
          benchmark_experiment, benchmark_model
      )
      result = move_to_device(result, 'cpu')
      results.append(result)
      for key, val in timing.items():
        if i == 0:
          timings[key] = np.zeros(self._args.repeat, np.float64)
        timings[key][i] = val

    # save the config, timings and results to proper files in self.output_dir
    # self.save_results(timings, results)

  def _mark_step(self, benchmark_experiment):
    if benchmark_experiment.xla:
      xm.mark_step()

  def _synchronize(self, benchmark_experiment):
    if benchmark_experiment.xla:
      xm.wait_device_ops()
    elif benchmark_experiment.accelerator == "gpu":
      torch.cuda.synchronize()
    else:
      pass

  def timed_iteration(self, benchmark_experiment, benchmark_model):
    reset_rng_state()

    self._mark_step(benchmark_experiment)
    self._synchronize(benchmark_experiment)

    timing = OrderedDict()
    t_start = time.perf_counter()

    for i in range(self._args.repeat_inner):
      result = benchmark_model.model_iter_fn()

      if benchmark_experiment.xla and self._args.repeat_inner == 1:
        t_trace = time.perf_counter()

      self._mark_step(benchmark_experiment)

    self._synchronize(benchmark_experiment)

    t_end = time.perf_counter()

    timing["total"] = t_end - t_start
    timing["average"] = timing["total"] / self._args.repeat_inner
    if benchmark_experiment.xla and self._args.repeat_inner == 1:
      timing["trace"] = t_trace - t_start

    return timing, result


def parse_args(args=None):
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--suite-name",
        required=True,
        choices=["dummy", "torchbench"],
        help="Suite name for the model garden.",
    )

    parser.add_argument(
        "--repeat",
        type=int,
        default=10,
        help="Number of times to repeat the timed iteration.",
    )

    parser.add_argument(
        "--repeat-inner",
        type=int,
        default=1,
        help="Number of times to repeat the model function inside the timed iteration.",
    )

    parser.add_argument(
        "--experiment-config",
        type=str,
        help="JSON string of the experiment config dict.",
    )

    parser.add_argument(
        "--model-config",
        type=str,
        help="JSON string of the model config dict.",
    )

    return parser.parse_args(args)


def main():
  args = parse_args()
  print(args)
  runner = ExperimentRunner(args)
  runner.run()


if __name__ == "__main__":
    main()