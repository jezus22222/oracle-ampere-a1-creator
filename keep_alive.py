#!/usr/bin/env python3
"""
OCI Free Tier Keep-Alive Script
Holds ~6GB of RAM and ~25% CPU usage across all cores
to prevent idle reclamation on Ampere A1 instances.
"""

import time
import multiprocessing
import os
import math

# Oracle OCI idle reclamation limits:
# "CPU utilization for the 95th percentile is less than 20%"
# "Memory utilization is less than 20% (applies to A1 shapes only)"


def consume_memory():
    """Allocate roughly 6GB of memory and hold it."""
    print("Allocating memory (approx 6 GB)...")
    dummy_data = []
    # 6000 * 1MB = ~6GB
    for _ in range(6000):
        dummy_data.append(" " * (1024 * 1024))
    print("Memory allocated. Holding...")
    while True:
        time.sleep(3600)


def cpu_worker(target_percentage):
    """Consume a given percentage of a CPU core."""
    print(f"Worker {os.getpid()} starting CPU load targeting "
          f"{target_percentage * 100}% of a core.")
    while True:
        # Work for a fraction of a second, then sleep for the rest
        work_time = 0.1 * target_percentage
        sleep_time = 0.1 * (1.0 - target_percentage)

        start = time.time()
        while time.time() - start < work_time:
            # Junk math to keep CPU busy
            math.sqrt(1234567.89 * 987654.321)

        time.sleep(sleep_time)


if __name__ == "__main__":
    print("Starting OCI Free Tier Keep-Alive Script...")

    # Start memory consumer in a separate process
    mem_process = multiprocessing.Process(target=consume_memory)
    mem_process.start()

    # Target > 20% overall CPU by running a 25% load on every core
    num_cores = multiprocessing.cpu_count()
    workers = []
    for _ in range(num_cores):
        p = multiprocessing.Process(target=cpu_worker, args=(0.25,))
        p.start()
        workers.append(p)

    try:
        mem_process.join()
        for w in workers:
            w.join()
    except KeyboardInterrupt:
        print("\nStopping script...")
        mem_process.terminate()
        for w in workers:
            w.terminate()
