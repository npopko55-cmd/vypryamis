let token = '';
let refreshing: Promise<boolean> | null = null;
export function setToken(value: string) { token = value; }
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
async function renew() {
  const run = async () => {
    const res = await fetch('/api/auth/refresh', {method:'POST', credentials:'same-origin'});
    if (!res.ok) return false;
    const body = await res.json(); token = body.access_token; return true;
  };
  if (!refreshing) refreshing = (navigator.locks ? navigator.locks.request('vypryamis-session', run) : run()).finally(()=>{refreshing=null;});
  return refreshing;
}
export async function api<T=any>(path: string, options: RequestInit = {}, retry=true): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type','application/json');
  if (token) headers.set('Authorization','Bearer '+token);
  let res: Response;
  try { res = await fetch('/api'+path, {...options, headers, credentials:'same-origin'}); }
  catch { throw new ApiError('Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз.',0); }
  if (res.status===401 && retry && !path.startsWith('/auth/')) {
    if (await renew()) return api(path,options,false);
  }
  if (!res.ok) {
    const body = await res.json().catch(()=>({}));
    const message = typeof body.detail==='string' ? body.detail : 'Проверьте введённые данные и попробуйте ещё раз.';
    throw new ApiError(message,res.status);
  }
  return res.json();
}
export const post = <T=any>(path: string, body: unknown={}) => api<T>(path,{method:'POST',body:JSON.stringify(body)});
export const patch = <T=any>(path: string, body: unknown) => api<T>(path,{method:'PATCH',body:JSON.stringify(body)});
export const money = (value: number) => new Intl.NumberFormat('ru-RU').format(value/100)+' ₽';
export const dateLabel = (value: string, format: Intl.DateTimeFormatOptions={day:'numeric',month:'long'}) => new Intl.DateTimeFormat('ru-RU',format).format(new Date(value+'T12:00:00'));
