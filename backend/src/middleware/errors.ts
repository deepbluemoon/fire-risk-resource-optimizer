import type { NextFunction, Request, Response } from 'express';

export class HttpError extends Error {
  constructor(public status: number, public code: string, message: string, public extra?: unknown) {
    super(message);
  }
}

export const notFound = (msg = '해당 지역·일자의 예측이 없습니다') =>
  new HttpError(404, 'NOT_FOUND', msg);

export const badRequest = (msg: string) => new HttpError(400, 'BAD_REQUEST', msg);

export function errorHandler(err: any, _req: Request, res: Response, _next: NextFunction) {
  if (err instanceof HttpError) {
    return res.status(err.status).json({ error: err.code, message: err.message, ...(err.extra as object ?? {}) });
  }
  console.error('[unhandled]', err);
  return res.status(500).json({ error: 'INTERNAL', message: '서버 오류가 발생했습니다' });
}

export function requestLog(req: Request, res: Response, next: NextFunction) {
  const t0 = Date.now();
  res.on('finish', () => {
    console.log(JSON.stringify({
      ts: new Date().toISOString(), level: 'INFO', method: req.method,
      path: req.originalUrl, status: res.statusCode, ms: Date.now() - t0,
    }));
  });
  next();
}
