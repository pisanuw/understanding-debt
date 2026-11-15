// GET /api/excerpt: a line range of one file of a public GitHub repository at a commit.
import { handleExcerpt } from './lib/handlers';

export default (req: Request): Promise<Response> => handleExcerpt(req);

export const config = { path: '/api/excerpt' };
