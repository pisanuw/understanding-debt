// GET /api/tree: the top files under directories of a public GitHub repository at a commit.
import { handleTree } from './lib/handlers';

export default (req: Request): Promise<Response> => handleTree(req);

export const config = { path: '/api/tree' };
