// POST /api/allowance: the signed-in person's HintWise allowance (the gateway's check mode, no model call).
import { handleAllowance } from './lib/handlers';

export default (req: Request): Promise<Response> => handleAllowance(req);

export const config = { path: '/api/allowance' };
