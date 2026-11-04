/** Polling owns its retries and cancellation, so timer rejections never escape. */
export function watchSync<T extends { status: string }>(options: {
  request: () => Promise<T>;
  update: (job: T) => void;
  finish: (job: T) => void;
  fail: (error: unknown) => void;
  schedule?: (callback: () => void, delay: number) => () => void;
  now?: () => number;
}) {
  const schedule = options.schedule ?? ((callback, delay) => {
    const timer = setTimeout(callback, delay);
    return () => clearTimeout(timer);
  });
  const now = options.now ?? Date.now;
  const deadline = now() + 15 * 60_000;
  let cancelled = false;
  let cancelTimer: (() => void) | undefined;
  let failures = 0;
  const poll = async () => {
    if (cancelled) return;
    try {
      if (now() >= deadline) throw new Error("Sync status checking timed out. Try Sync inbox again.");
      const job = await options.request();
      if (cancelled) return;
      failures = 0;
      options.update(job);
      if (job.status === "running") {
        cancelTimer = schedule(() => { void poll(); }, 1200);
      } else {
        options.finish(job);
      }
    } catch (error) {
      if (cancelled) return;
      failures += 1;
      if (failures >= 4 || now() >= deadline) options.fail(error);
      else cancelTimer = schedule(() => { void poll(); }, 1200 * 2 ** (failures - 1));
    }
  };
  void poll();
  return () => { cancelled = true; cancelTimer?.(); };
}
