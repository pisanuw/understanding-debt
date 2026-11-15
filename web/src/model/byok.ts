/**
 * Writing and grading with the person's own Anthropic key, straight from the browser: the
 * same prompts as the gateway functions (src/core/assemble.ts), sent through llm.ts's `ask`
 * in BYOK mode (the key stays in this browser and goes only to api.anthropic.com). The code
 * excerpts still come from /api/excerpt. Loaded on demand (client.ts, LazyByokClient).
 */
import { gradeAnswers, writeQuestions, type ModelRequest, type QuestionsRequest } from '../core/assemble';
import type { Excerpt, ExcerptRef } from '../core/excerpts';
import type { ModelMessage } from '../core/prompts';
import type { Question } from '../core/quiz';
import { ask as llmAsk, type AskRequest } from '../lib/llm';
import { excerptVia, type ClientDeps, type GradeOutcome, type QuizClient } from './client';

export class ByokClient implements QuizClient {
  readonly mode = 'byok' as const;
  constructor(private readonly deps: ClientDeps) {}

  private assembleDeps() {
    const viaProxy = excerptVia(this.deps);
    return {
      excerpt: (g: { url: string }, sha: string, ref: ExcerptRef) => viaProxy(g.url, sha, ref).catch(() => null),
      ask: async (body: ModelRequest): Promise<ModelMessage> => (await llmAsk(body as unknown as AskRequest, { ...this.deps.askDeps, mode: 'byok' })) as unknown as ModelMessage,
    };
  }

  writeQuestions(req: QuestionsRequest): Promise<Question[]> {
    return writeQuestions(req, this.assembleDeps());
  }

  async grade(req: Parameters<QuizClient['grade']>[0]): Promise<GradeOutcome> {
    return { grades: await gradeAnswers(req, this.assembleDeps()), recorded: null };
  }

  excerpt(repoUrl: string, sha: string, ref: ExcerptRef): Promise<Excerpt> {
    return excerptVia(this.deps)(repoUrl, sha, ref);
  }
}

