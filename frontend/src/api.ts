const API_ROOT = '/api/v1';

type ApiProblem = {
  code?: string;
  message?: string;
  fields?: Record<string, string[]>;
  preview?: unknown;
};

let csrfToken: string | null = null;

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly fields?: Record<string, string[]>;
  readonly preview?: unknown;

  constructor(status: number, problem: ApiProblem) {
    super(problem.message || 'Não foi possível concluir a solicitação.');
    this.name = 'ApiError';
    this.status = status;
    this.code = problem.code;
    this.fields = problem.fields;
    this.preview = problem.preview;
  }
}

async function loadCsrfToken(): Promise<string> {
  const response = await fetch(`${API_ROOT}/csrf/`, { credentials: 'include' });
  if (!response.ok) throw new ApiError(response.status, await readProblem(response));
  const result = await response.json() as { csrfToken: string };
  csrfToken = result.csrfToken;
  return csrfToken;
}

export async function refreshCsrfToken(): Promise<void> {
  csrfToken = null;
  try {
    await loadCsrfToken();
  } catch {
    csrfToken = null;
  }
}

async function readProblem(response: Response): Promise<ApiProblem> {
  try {
    return await response.json() as ApiProblem;
  } catch {
    return { message: 'Não foi possível conectar ao CompraCerta.' };
  }
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const token = csrfToken || await loadCsrfToken();
  const response = await fetch(`${API_ROOT}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': token,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) throw new ApiError(response.status, await readProblem(response));
  return await response.json() as T;
}

export async function apiPostFormData<T>(path: string, body: FormData): Promise<T> {
  const token = csrfToken || await loadCsrfToken();
  const response = await fetch(`${API_ROOT}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-CSRFToken': token },
    body,
  });

  if (!response.ok) throw new ApiError(response.status, await readProblem(response));
  return await response.json() as T;
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const token = csrfToken || await loadCsrfToken();
  const response = await fetch(`${API_ROOT}${path}`, {
    method: 'PUT',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': token,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) throw new ApiError(response.status, await readProblem(response));
  return await response.json() as T;
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const token = csrfToken || await loadCsrfToken();
  const response = await fetch(`${API_ROOT}${path}`, {
    method: 'PATCH',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': token,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) throw new ApiError(response.status, await readProblem(response));
  return await response.json() as T;
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, { credentials: 'include' });
  if (!response.ok) throw new ApiError(response.status, await readProblem(response));
  return await response.json() as T;
}

export async function apiDelete(path: string): Promise<void> {
  const token = csrfToken || await loadCsrfToken();
  const response = await fetch(`${API_ROOT}${path}`, {
    method: 'DELETE',
    credentials: 'include',
    headers: { 'X-CSRFToken': token },
  });
  if (!response.ok) throw new ApiError(response.status, await readProblem(response));
}
