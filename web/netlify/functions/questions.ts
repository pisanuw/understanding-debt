// POST /api/questions: writes a quiz sitting's questions through the HintWise gateway.
import { handleQuestions } from './lib/handlers';

export default (req: Request): Promise<Response> => handleQuestions(req);

export const config = { path: '/api/questions' };
