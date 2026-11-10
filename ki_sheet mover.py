"""KiCad hierarchical schematic mover (Python 3.10+, stdlib only)."""
import os
import re
import shutil
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from datetime import datetime

# Match only the filename property inside a sheet expression; do not touch other strings.
FILENAME = re.compile(r'\(property\s+"Sheetfile"\s+"((?:\\.|[^"\\])*)"', re.I)

def blocks(text, keyword):
    """Yield (start, end) for top-level S-expression blocks named keyword, string-aware."""
    # Tokenize strings too, so text containing "(sheet ..." is never a block.
    pattern = re.compile(r'"(?:\\.|[^"\\])*"|\(' + re.escape(keyword) + r'(?=[\s()])')
    pos = 0
    while (m := pattern.search(text, pos)):
        if text[m.start()] == '"':
            pos = m.end()
            continue
        i, depth, quoted, escaped = m.start(), 0, False, False
        j = i
        while j < len(text):
            c = text[j]
            if quoted:
                if escaped: escaped = False
                elif c == '\\': escaped = True
                elif c == '"': quoted = False
            elif c == '"': quoted = True
            elif c == '(': depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    yield i, j + 1
                    pos = j + 1
                    break
            j += 1
        else:
            raise ValueError('Unbalanced S-expression near position ' + str(i))

def decode_kicad(s):
    return s.replace('\\"', '"').replace('\\\\', '\\')

def encode_kicad(s):
    return s.replace('\\', '\\\\').replace('"', '\\"')

def references(content):
    for start, end in blocks(content, 'sheet'):
        b = content[start:end]
        m = FILENAME.search(b)
        if not m:
            raise ValueError('Sheet has no Sheetfile property')
        yield start + m.start(1), start + m.end(1), decode_kicad(m.group(1))

def plan(root):
    root = root.resolve(strict=True)
    if root.suffix.lower() != '.kicad_sch':
        raise ValueError('Select a root .kicad_sch file')
    base = root.parent
    target_dir = base / 'sheet'
    if target_dir.is_symlink() or not target_dir.resolve().is_relative_to(base):
        raise ValueError('The sheet directory must be inside the project')
    seen, stack, active = {}, [(root, False)], set()
    while stack:
        src, leaving = stack.pop()
        src = src.resolve(strict=True)
        if leaving:
            active.remove(src)
            continue
        if not src.is_relative_to(base):
            raise ValueError('External schematic reference: ' + str(src))
        if src in active:
            raise ValueError('Circular schematic reference: ' + str(src))
        if src in seen: continue
        active.add(src)
        stack.append((src, True))
        content = src.read_text(encoding='utf-8-sig')
        refs = list(references(content))
        seen[src] = (content, refs)
        for _, _, name in refs:
            if not name.strip():
                raise ValueError('Empty Sheetfile property: ' + str(src))
            child = (src.parent / name.replace('\\', '/')).resolve()
            if not child.is_file():
                raise FileNotFoundError(f'Missing reference: {src} -> {name}')
            if child.suffix.lower() != '.kicad_sch':
                raise ValueError('Non schematic reference: ' + str(child))
            stack.append((child, False))
    dest = {src: (src if src == root else target_dir / src.name) for src in seen}
    if len(set(dest.values())) != len(dest):
        raise ValueError('Duplicate child schematic filenames. Rename files before moving.')
    for src, dst in dest.items():
        if src != dst and dst.exists() and dst not in seen:
            raise FileExistsError('Destination already exists: ' + str(dst))
    changed = {}
    for src, (content, refs) in seen.items():
        edits = []
        for a, b, name in refs:
            child = (src.parent / name.replace('\\', '/')).resolve()
            relative = os.path.relpath(dest[child], dest[src].parent).replace('\\', '/')
            edits.append((a, b, encode_kicad(relative)))
        new = content
        for a, b, replacement in reversed(edits):
            new = new[:a] + replacement + new[b:]
        changed[src] = new
    return root, base, dest, changed

def apply(p):
    root, base, dest, changed = p
    if plan(root) != p:
        raise RuntimeError('Files changed since preview. Analyze again.')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = base / ('.sheet_mover_backup_' + stamp)
    backup.mkdir()
    staged, installed = [], []
    modifying = False
    try:
        # Preserve all source schematics in a recoverable directory tree.
        for src in changed:
            relative = src.relative_to(base)
            copy = backup / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, copy)
        # Write all destination files to temp files, then atomically replace.
        for src, output in changed.items():
            dst = dest[src]
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix='.kicad_sheet_', dir=dst.parent)
            staged.append((Path(temp), dst))
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
                f.write(output)
        modifying = True
        for temp, dst in staged:
            os.replace(temp, dst)
            installed.append(dst)
        # Remove only old child files; never remove root or other files/folders.
        for src, dst in dest.items():
            if src != dst and src != root:
                src.unlink()
    except Exception:
        # Restore original files and remove newly created destination files.
        for src in changed if modifying else ():
            copy = backup / src.relative_to(base)
            if copy.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(copy, src)
        for dst in installed:
            if dst.exists() and dst not in changed:
                dst.unlink()
        raise
    finally:
        for temp, _ in staged:
            if temp.exists():
                temp.unlink()
    return backup

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('KiCad Sheet Mover')
        self.geometry('850x530')
        self.path = tk.StringVar()
        self.current = None
        top = ttk.Frame(self, padding=12)
        top.pack(fill='x')
        ttk.Label(top, text='Root schematic (.kicad_sch)').pack(anchor='w')
        line = ttk.Frame(top)
        line.pack(fill='x', pady=5)
        ttk.Entry(line, textvariable=self.path).pack(side='left', fill='x', expand=True)
        ttk.Button(line, text='Browse...', command=self.browse).pack(side='left', padx=5)
        ttk.Button(top, text='Analyze', command=self.analyze).pack(anchor='w', pady=5)
        self.info = ttk.Label(self, text='Select the root schematic and click Analyze.', padding=(12, 4))
        self.info.pack(anchor='w')
        frame = ttk.Frame(self, padding=(12, 0, 12, 12))
        frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(frame, columns=('from', 'to'), show='headings')
        self.tree.heading('from', text='Current path')
        self.tree.heading('to', text='New path')
        self.tree.column('from', width=380)
        self.tree.column('to', width=380)
        self.tree.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        sb.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=sb.set)
        self.go = ttk.Button(self, text='Move sheets (with backup)', command=self.move, state='disabled')
        self.go.pack(pady=(0, 14))
        self.path.trace_add('write', lambda *_: self.invalidate())
    def invalidate(self):
        self.current = None
        self.go.configure(state='disabled')
    def browse(self):
        selected = filedialog.askopenfilename(filetypes=[('KiCad schematic', '*.kicad_sch')])
        if selected: self.path.set(selected)
    def analyze(self):
        self.invalidate()
        self.tree.delete(*self.tree.get_children())
        try:
            p = plan(Path(self.path.get()))
            root, base, dest, _ = p
            for src, dst in sorted(dest.items(), key=lambda x: str(x[0])):
                if src != root:
                    self.tree.insert('', 'end', values=(str(src.relative_to(base)), str(dst.relative_to(base))))
            count = len(dest) - 1
            self.info.configure(text=f'{count} child schematic(s) found; target: {base / "sheet"}')
            self.current = p
            if count: self.go.configure(state='normal')
        except Exception as e:
            messagebox.showerror('Analysis failed', str(e))
    def move(self):
        if not self.current: return
        if not messagebox.askyesno('Confirm', 'Close KiCad before continuing. Move all listed child schematics and update references?'):
            return
        try:
            # Re-analyze immediately to detect changes since preview.
            fresh = plan(self.current[0])
            if fresh[2] != self.current[2] or fresh[3] != self.current[3]:
                raise RuntimeError('Files changed since preview. Analyze again.')
            backup = apply(fresh)
            messagebox.showinfo('Complete', f'Sheets moved successfully.\nBackup: {backup}\nOpen the project in KiCad and verify references.')
            self.analyze()
        except Exception as e:
            messagebox.showerror('Move failed', str(e))

if __name__ == '__main__':
    App().mainloop()
