/**
 * The analysis service on Render (udebt.hosted.worker_service, at udebt-api.hintwise.org):
 * "analyse this public repository at this commit" with the person's session token and their
 * own Anthropic key, which is used for that job only and never stored. The job writes its
 * progress and result to Supabase with the same token, so the page follows it by reading
 * the person's own app_udebt.jobs rows.
 */
export interface StartRequest {
  url: string;
  rev?: string;
  model?: string;
}

export interface WorkerClient {
  readonly base: string;
  start(req: StartRequest, apiKey: string, token: string): Promise<{ job: number | null; status: string }>;
}

export class HttpWorkerClient implements WorkerClient {
  constructor(
    readonly base: string,
    private readonly doFetch: typeof fetch = (...a) => fetch(...a),
  ) {}

  async start(req: StartRequest, apiKey: string, token: string): Promise<{ job: number | null; status: string }> {
    let res: Response;
    try {
      res = await this.doFetch(`${this.base.replace(/\/+$/, '')}/analyses`, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, 'x-anthropic-key': apiKey, 'content-type': 'application/json' },
        body: JSON.stringify(req),
      });
    } catch {
      throw new Error('Could not reach the analysis service. It sleeps when idle; try again in a minute.');
    }
    const data: unknown = await res.json().catch(() => ({}));
    const d = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
    if (!res.ok) throw new Error(typeof d.error === 'string' && d.error ? d.error : `The analysis service answered ${res.status}.`);
    return { job: typeof d.job === 'number' ? d.job : null, status: String(d.status ?? 'queued') };
  }
}

/** The service URL from the build (VITE_UDEBT_WORKER_URL), or null. */
export function workerUrl(env: Record<string, unknown> = import.meta.env): string | null {
  const v = env.VITE_UDEBT_WORKER_URL;
  return typeof v === 'string' && /^https:\/\/[a-z0-9.-]+(:\d+)?\/?$/i.test(v) ? v : null;
}
