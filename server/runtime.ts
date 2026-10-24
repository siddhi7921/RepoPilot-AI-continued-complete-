export function setting(name: string): string | undefined {
  return typeof Netlify !== 'undefined' ? Netlify.env.get(name) : process.env[name];
}
