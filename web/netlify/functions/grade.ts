// POST /api/grade: grades a sitting's answers in one gateway call and records them.
import { handleGrade } from './lib/handlers';

export default (req: Request): Promise<Response> => handleGrade(req);

export const config = { path: '/api/grade' };
