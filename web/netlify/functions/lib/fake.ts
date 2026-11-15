/**
 * The fake HintWise gateway for `npm run dev`, `vite preview`, the Playwright walkthrough and
 * tests: canned Messages API answers keyed on the prompt's TASK marker, as udebt's FakeLLM
 * does for the CLI. Questions follow the ladder's level and cite as evidence the excerpts the
 * prompt carried for the unit (its paths otherwise), as a model reading them would; an answer
 * containing "wrong" is graded wrong, one containing "partly" partial, any other non-blank
 * answer correct.
 */
import type { ModelRequest } from '../../../src/core/assemble';
import type { ModelMessage } from '../../../src/core/prompts';

interface FakeUnit {
  id: string;
  name: string;
  level: number;
  summary: string;
  paths: string[];
}

export function fakeUnits(prompt: string): FakeUnit[] {
  const out: FakeUnit[] = [];
  let cur: FakeUnit | null = null;
  for (const line of prompt.split('\n')) {
    const id = /^- unit_id: (.+)$/.exec(line);
    if (id) {
      cur = { id: id[1].trim(), name: id[1].trim(), level: 1, summary: '', paths: [] };
      out.push(cur);
      continue;
    }
    if (!cur) continue;
    const m = /^ {2}(name|level|summary|paths): (.*)$/.exec(line);
    if (!m) continue;
    if (m[1] === 'name') cur.name = m[2].replace(/ \(a part of .*\)$/, '').trim();
    else if (m[1] === 'level') cur.level = Number.parseInt(m[2], 10) || 1;
    else if (m[1] === 'summary') cur.summary = m[2].trim();
    else cur.paths = m[2].split(',').map((s) => s.trim()).filter(Boolean);
  }
  return out;
}

const ASK: Record<number, (n: string) => [string, string, string]> = {
  1: (n) => [`What job does ${n} do for the rest of the system?`, `Why does the rest of the system need ${n}?`, `What would a user notice first if ${n} stopped working?`],
  2: (n) => [`What was chosen for ${n}?`, `Which alternative did ${n} turn down?`, `What constraint shaped ${n}?`],
  3: (n) => [`If the decision behind ${n} were reversed, what else would have to change first?`, `What breaks elsewhere if ${n} changed its approach?`, `Which part depends most on the choice in ${n}?`],
  4: (n) => [`What happens when ${n} fails halfway through?`, `What does the user see when ${n} times out?`, `How does ${n} behave on empty input?`],
  5: (n) => [`Which code path in ${n} handles the main request?`, `What would removing the main handler of ${n} break?`, `Where does ${n} start its work?`],
};

function message(input: Record<string, unknown>, prompt: string): ModelMessage {
  return {
    content: [{ type: 'tool_use', name: 'submit', input }],
    stop_reason: 'tool_use',
    usage: { input_tokens: Math.ceil(prompt.length / 4), output_tokens: Math.ceil(JSON.stringify(input).length / 4) },
  };
}

/** The files the prompt's code excerpts come from ("--- <path> lines a-b" headers), in order. */
export function excerptPaths(prompt: string): string[] {
  return [...new Set([...prompt.matchAll(/^--- (.+) lines \d+-\d+$/gm)].map((m) => m[1]))];
}

function evidenceFor(u: FakeUnit, shown: string[]): string[] {
  const under = shown.filter((f) => u.paths.some((p) => f === p || f.startsWith(`${p.replace(/\/+$/, '')}/`)));
  return (under.length ? under : u.paths).slice(0, 2);
}

export function fakeGateway(body: ModelRequest): ModelMessage {
  const prompt = body.messages.map((m) => m.content).join('\n');
  if (prompt.includes('TASK: questions')) {
    const shown = excerptPaths(prompt);
    const questions = fakeUnits(prompt).map((u) => {
      const [q, v1, v2] = (ASK[u.level] ?? ASK[1])(u.name);
      return {
        unit_id: u.id,
        level: u.level,
        question: q,
        variants: [v1, v2],
        reference_answer: u.summary ? `It ${u.summary.charAt(0).toLowerCase()}${u.summary.slice(1)}` : `${u.name} does one job for the rest of the system.`,
        evidence: evidenceFor(u, shown),
      };
    });
    return message({ questions }, prompt);
  }
  if (prompt.includes('TASK: grade')) {
    const grades = [...prompt.matchAll(/^### (\d+)\n[\s\S]*?^ANSWER: (.*)$/gm)].map((m) => {
      const answer = m[2].toLowerCase();
      const verdict = answer.includes('wrong') ? 'wrong' : answer.includes('partly') ? 'partial' : 'correct';
      const feedback =
        verdict === 'correct'
          ? 'Right: that is the unit\'s job. Look at its entry point to see it in the code.'
          : verdict === 'partial'
            ? 'Partly: the role is right, but a consequence is missing. Look at what calls it.'
            : 'Not quite: the code does something else here. Look at the unit\'s main file.';
      return { index: Number(m[1]), verdict, feedback };
    });
    return message({ grades }, prompt);
  }
  return { content: [{ type: 'text', text: 'The fake gateway does not know this task.' }], stop_reason: 'end_turn' };
}
