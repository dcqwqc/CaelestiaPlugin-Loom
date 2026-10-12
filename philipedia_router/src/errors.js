export class RouterError extends Error {
  constructor(code, message, details = undefined) {
    super(message);
    this.name = "RouterError";
    this.code = code;
    this.details = details;
  }
}

export function publicError(error) {
  if (error instanceof RouterError) {
    return { ok: false, error: error.code, message: error.message, ...(error.details ? { details: error.details } : {}) };
  }
  return { ok: false, error: "internal-error", message: "Router operation failed" };
}
