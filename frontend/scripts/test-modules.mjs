import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

export const root = fileURLToPath(new URL('../', import.meta.url))
const compiled = new Map()
export async function moduleUrl(filename) {
  if (compiled.has(filename)) return compiled.get(filename)
  let source = ts.transpileModule(
    await readFile(filename, 'utf8'),
    { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } },
  ).outputText
  for (const match of [...source.matchAll(/from\s+(['"])([^'"]+)\1/g)]) {
    const specifier = match[2]
    const local = specifier.startsWith('@/') ? path.join(root, specifier.slice(2)) : specifier.startsWith('.') ? path.resolve(path.dirname(filename), specifier) : null
    const resolved = local ? await moduleUrl(local.endsWith('.ts') ? local : `${local}.ts`) : import.meta.resolve(specifier)
    source = source.replace(match[0], `from '${resolved}'`)
  }
  const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
  compiled.set(filename, url)
  return url
}
