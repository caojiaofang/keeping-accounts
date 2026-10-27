import { mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const icons = {
  leaf: '<path d="M19 4C9 3 3 9 6 16c6 5 14-1 13-12Z"/><path d="m4 21 11-12M8 15l-1-5m5 1 5 1"/>',
  list: '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  chart: '<path d="M4 4v16h17M8 15v-4m5 4V7m5 8v-6"/>',
  book: '<path d="M6 3h13v18H6a3 3 0 0 1 0-6h13M6 3a3 3 0 0 0-3 3v12M8 7h7m-7 3h5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  back: '<path d="m14 5-7 7 7 7"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-13 4h1m5 0h1"/>',
  filter: '<path d="M4 6h16M7 12h10m-7 6h4"/>',
  food: '<path d="M5 3v7m4-7v7M3 3v5a4 4 0 0 0 8 0V3M7 12v9M18 21V3c-4 4-4 10 0 10"/>',
  coffee: '<path d="M4 8h13v7a6 6 0 0 1-6 6H9a5 5 0 0 1-5-5V8Zm13 1h1a3 3 0 0 1 0 6h-1M7 3v2m6-2v2"/>',
  bus: '<rect x="4" y="3" width="16" height="16" rx="3"/><path d="M4 11h16M8 19v2m8-2v2M8 15h1m6 0h1M9 6h6"/>',
  bag: '<path d="M5 8h14l2 13H3L5 8Z"/><path d="M8 9V6a4 4 0 0 1 8 0v3"/>',
  basket: '<path d="m3 9 2 12h14l2-12H3Zm4 0 4-6m6 6-4-6m-5 10v4m4-4v4m4-4v4"/>',
  home: '<path d="m3 11 9-8 9 8M5 10v11h14V10M10 21v-7h4v7"/>',
  game: '<path d="M7 7h10c4 0 6 12 3 13-2 1-4-4-6-4h-4c-2 0-4 5-6 4C1 19 3 7 7 7Z"/><path d="M7 10v5m-2-2h4m6-2h.1m3 3h.1"/>',
  health: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 5V3h8v2m-4 5v7m-3-3h6"/>',
  study: '<path d="m2 8 10-5 10 5-10 5L2 8Zm4 3v6c4 3 8 3 12 0v-6m4-3v9"/>',
  gift: '<rect x="3" y="8" width="18" height="5" rx="1"/><path d="M5 13v8h14v-8M12 8v13m0-13C4 8 5 1 9 3l3 5Zm0 0c8 0 7-7 3-5l-3 5Z"/>',
  plane: '<path d="m3 10 7 1 7-7c3-3 6 0 3 3l-7 7 1 7-3-1-2-5-5-2-1-3Z"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  wallet: '<path d="M20 7H6a3 3 0 0 1 0-6h12v6M3 4v15a2 2 0 0 0 2 2h15V7m0 5h-6v5h6m-3-2.5h.1"/>',
  work: '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V3h8v4M3 12c6 4 12 4 18 0m-9 1v3"/>',
  return: '<path d="m8 3-5 5 5 5M3 8h10a7 7 0 0 1 0 14h-3"/>',
  settings: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2" fill="#f6f8f7"/><circle cx="16" cy="12" r="2" fill="#f6f8f7"/><circle cx="10" cy="18" r="2" fill="#f6f8f7"/>',
  shield: '<path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6l8-4Z"/><path d="m8 12 3 3 5-6"/>',
  export: '<path d="M12 16V3m-5 5 5-5 5 5M4 14v7h16v-7"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9 8a3 3 0 0 1 6 1c0 2-3 2-3 5m0 3h.1"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.1"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  edit: '<path d="m14 5 5 5M4 20l5-1L21 7l-5-5L4 14v6ZM3 22h19"/>',
  tag: '<path d="M3 3h9l10 10-9 9L3 12V3Z"/><circle cx="8" cy="8" r="1"/>',
  note: '<path d="M4 3h16v14l-5 5H4V3Zm11 19v-5h5M8 8h8m-8 4h6"/>',
  star: '<path d="m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1 3-6Z"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
}
const directory = fileURLToPath(new URL('../static/icons/', import.meta.url))
mkdirSync(directory, { recursive: true })
for (const [name, paths] of Object.entries(icons)) {
  writeFileSync(`${directory}/${name}.svg`, `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#284b40" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`, 'utf8')
}
console.log(`已生成 ${Object.keys(icons).length} 个本地图标`)
