const paths = {
  file: 'M14 3H5v18h14V8z M14 3v5h5 M8 12h8 M8 16h5',
  link: 'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2 M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2',
  batch: 'M8 3h12v14H8z M4 7v14h12 M11 7h6 M11 11h6',
  text: 'M4 5h16 M12 5v15 M8 20h8 M4 9V5 M20 9V5',
  folder: 'M3 6h7l2 2h9v12H3z',
  book: 'M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1z M12 5v15',
  arrow: 'M5 12h14 M14 7l5 5-5 5',
} as const

export type WorkspaceIconName = keyof typeof paths

export function WorkspaceIcon({ name, size = 20 }: { name: WorkspaceIconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
    aria-hidden="true" focusable="false"><path d={paths[name]} /></svg>
}
