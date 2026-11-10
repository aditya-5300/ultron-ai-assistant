export interface SupportedApp {
  name: string;
  aliases: string[];
  startNames: string[];
  processes: string[];
  executable?: string;
  web?: string;
}

export const APP_CATALOG: Record<string, SupportedApp> = {
  notepad: { name: 'Notepad', aliases: ['notepad'], startNames: ['Notepad', 'Windows Notepad'], processes: ['notepad'], executable: 'notepad.exe' },
  calculator: { name: 'Calculator', aliases: ['calculator', 'calc'], startNames: ['Calculator', 'Windows Calculator'], processes: ['CalculatorApp', 'Calculator'], executable: 'calc.exe' },
  paint: { name: 'Paint', aliases: ['paint', 'microsoft paint'], startNames: ['Paint'], processes: ['mspaint'], executable: 'mspaint.exe' },
  explorer: { name: 'File Explorer', aliases: ['explorer', 'file explorer', 'file manager', 'windows explorer', 'windows file manager'], startNames: ['File Explorer'], processes: [], executable: 'explorer.exe' },
  taskmanager: { name: 'Task Manager', aliases: ['task manager', 'taskmanager', 'task mgr'], startNames: ['Task Manager'], processes: ['Taskmgr'], executable: 'Taskmgr.exe' },
  vscode: { name: 'Visual Studio Code', aliases: ['vs code', 'visual studio code', 'vscode'], startNames: ['Visual Studio Code', 'Visual Studio Code (User)'], processes: ['Code'] },
  whatsapp: { name: 'WhatsApp', aliases: ['whatsapp', 'whats app'], startNames: ['WhatsApp'], processes: ['WhatsApp'], web: 'https://web.whatsapp.com/' },
  spotify: { name: 'Spotify', aliases: ['spotify'], startNames: ['Spotify', 'Spotify Music'], processes: ['Spotify'], web: 'https://open.spotify.com/' },
  telegram: { name: 'Telegram', aliases: ['telegram'], startNames: ['Telegram', 'Telegram Desktop'], processes: ['Telegram'], web: 'https://web.telegram.org/' },
  instagram: { name: 'Instagram', aliases: ['instagram'], startNames: ['Instagram'], processes: ['Instagram'], web: 'https://www.instagram.com/' },
  youtube: { name: 'YouTube', aliases: ['youtube', 'you tube'], startNames: ['YouTube'], processes: [], web: 'https://www.youtube.com/' },
  gmail: { name: 'Gmail', aliases: ['gmail', 'g mail'], startNames: ['Gmail'], processes: [], web: 'https://mail.google.com/' },
  google: { name: 'Google', aliases: ['google'], startNames: ['Google'], processes: [], web: 'https://www.google.com/' },
  github: { name: 'GitHub', aliases: ['github', 'git hub'], startNames: [], processes: [], web: 'https://github.com/' },
};

export function appIdForAlias(value: string) {
  const normalized = value.toLowerCase().trim().replace(/^the\s+/, '').replace(/\s+/g, ' ');
  return Object.keys(APP_CATALOG).find(id => id === normalized || APP_CATALOG[id].aliases.includes(normalized));
}
