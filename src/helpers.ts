export function withBase(path: string): string {
  const base = import.meta.env.BASE_URL;
  if (path.startsWith('http') || path.startsWith('#') || path.startsWith('mailto:')) {
    return path;
  }
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const normalizedBase = base.startsWith('/') ? base.slice(base.length) : base;
  console.log(normalizedBase, normalizedPath);
  return `${normalizedBase}${normalizedPath}`;
}
