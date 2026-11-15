/**
 * The hosted question-writing and grading prompts. The rules are the CLI's own
 * (udebt/prompts.py, exported to prompt-parts.json by udebt.hosted.prompt_parts); what
 * differs is that the model has no read tools here: the code reaches it as excerpts fetched
 * from GitHub at the analysis's pinned commit, and it answers by calling one `submit` tool
 * whose input schema is the CLI's (the gateway passes `tools` but not `tool_choice`, so the
 * prompt asks for the call and `submitted` also accepts JSON in a text answer).
 */
import parts from './prompt-parts.json';
import { LEVEL_NAMES, levelOf } from './ladder';
import { formatExcerpts, type Excerpt } from './excerpts';
import type { HistoryEntry } from './specs';

export interface PromptContext {
  repo: string;
  sha: string;
  excerpts: Excerpt[];
}

/** A unit as the question writer sees it (prompts._unit_lines). */
export interface QuestionUnit {
  id: string;
  name: string;
  parent_name?: string;
  level: number;
  summary?: string | null;
  decision?: string | null;
  alternatives?: string | null;
  why?: string | null;
  paths?: string[];
  history?: HistoryEntry[];
  shown_orientation?: string;
  revealed?: string[];
}

export interface GradeItem {
  unit_id: string;
  level: number;
  question: string;
  reference_answer: string;
  evidence: string[];
  answer: string;
}

export interface Tool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export const SUBMIT_QUESTIONS: Tool = {
  name: 'submit',
  description: 'Submit every question of the quiz at once.',
  input_schema: parts.quiz_questions_schema as Record<string, unknown>,
};

export const SUBMIT_GRADES: Tool = {
  name: 'submit',
  description: 'Submit every grade at once.',
  input_schema: parts.grade_schema as Record<string, unknown>,
};

function historyLine(h: HistoryEntry): string {
  return `${h.sha} ${h.date ?? ''} [${h.klass ?? '?'}] ${String(h.subject ?? '').slice(0, 70)}`;
}

export function unitLines(u: QuestionUnit): string {
  const parent = u.parent_name ? ` (a part of ${u.parent_name})` : '';
  const n = levelOf({ level: u.level });
  const lines = [
    `- unit_id: ${u.id}`,
    `  name: ${u.name}${parent}`,
    `  level: ${n} (${LEVEL_NAMES[n]})`,
    `  summary: ${u.summary ?? ''}`,
    `  decision: ${u.decision ?? ''}`,
    `  alternatives: ${u.alternatives ?? ''}`,
    `  why: ${u.why ?? ''}`,
    `  paths: ${(u.paths ?? []).join(', ')}`,
  ];
  const shaped = (u.history ?? []).slice(0, 5).map(historyLine).join('; ');
  lines.push(`  shaped_by: ${shaped || '(unknown)'}`);
  if (u.shown_orientation) lines.push(`  orientation the developer has just read: ${u.shown_orientation}`);
  for (const q of u.revealed ?? []) lines.push(`  revealed after a miss (ask the same fact in other words): ${q}`);
  return lines.join('\n');
}

function codeBlock(ctx: PromptContext): string {
  if (!ctx.excerpts.length) return `No code excerpts could be fetched at ${ctx.sha.slice(0, 12)}; rely on the unit descriptions.`;
  return `Code excerpts from ${ctx.repo} at ${ctx.sha.slice(0, 12)} (line numbers on the left):\n\n${formatExcerpts(ctx.excerpts)}`;
}

/** One call for every question of a quiz sitting (prompts.questions_prompt without read tools). */
export function questionsPrompt(units: QuestionUnit[], perUnit: number, ctx: PromptContext): string {
  const oriented = units.some((u) => u.shown_orientation);
  const orientationRule = oriented ? `\n${parts.orientation_rule}\n` : '';
  const revealedRule = units.some((u) => u.revealed?.length) ? `\n${parts.revealed_rule}\n` : '';
  return `TASK: questions

You are testing whether a developer understands the ARCHITECTURE of the repository ${ctx.repo}
at commit ${ctx.sha.slice(0, 12)}: what each unit is for, the decisions that shaped it, and what
those decisions mean for the rest of the system. Questions climb a ladder of five levels, and a
developer is asked a level only once they have shown the levels above it. Each unit below
names the level to ask it at. Write ${perUnit} question(s) for each unit, every one at exactly
that unit's level, and return that level with each question.

The ladder:
${parts.ladder_block}

${parts.one_fact}

${parts.no_identifiers}
${orientationRule}${revealedRule}
${parts.objective_rule} You cannot open files: the code reaches you as the excerpts at the end,
taken from the repository at that commit, with the unit descriptions and the commit subjects
given; verify every reference answer against them. Cite the files you relied on in
\`evidence\`, each as \`path\` or \`path:start-end\`. ${parts.reference_rule}

Answer by calling the tool \`submit\` once, with every question.

Units:
${units.map(unitLines).join('\n')}

${codeBlock(ctx)}
`;
}

/** One call for every answer of a sitting (prompts.grade_prompt without read tools). */
export function gradePrompt(items: GradeItem[], ctx: PromptContext): string {
  const blocks = items.map((it, i) => {
    const n = levelOf({ level: it.level });
    return (
      `### ${i}\nUNIT: ${it.unit_id}\nLEVEL: ${n} (${LEVEL_NAMES[n]})\nQUESTION: ${it.question}\n` +
      `REFERENCE: ${it.reference_answer}\nEVIDENCE: ${it.evidence.join(', ')}\nANSWER: ${it.answer}\n`
    );
  });
  return `TASK: grade

Grade each developer ANSWER against the code of ${ctx.repo} at commit ${ctx.sha.slice(0, 12)}. The
REFERENCE was written earlier by another model and may itself be wrong; if the answer and
reference disagree, check the EVIDENCE excerpts at the end and trust the code.

${parts.grade_rules}
Return \`index\` matching the ### number. Answer by calling the tool \`submit\` once, with every
grade.

${blocks.join('\n')}
${codeBlock(ctx)}
`;
}

/** The shape of a Messages API answer this code reads (the gateway returns it unchanged). */
export interface ModelMessage {
  content: { type: string; text?: string; name?: string; input?: unknown }[];
  stop_reason?: string | null;
  usage?: Record<string, number>;
}

/** The `submit` tool's input, or a JSON object found in a text answer; null when neither. */
export function submitted(message: ModelMessage): Record<string, unknown> | null {
  for (const b of message.content ?? []) {
    if (b.type === 'tool_use' && b.name === 'submit' && b.input && typeof b.input === 'object') return b.input as Record<string, unknown>;
  }
  const text = (message.content ?? [])
    .filter((b) => b.type === 'text')
    .map((b) => b.text ?? '')
    .join('');
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try {
    const v: unknown = JSON.parse(text.slice(start, end + 1));
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
