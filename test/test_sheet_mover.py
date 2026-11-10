import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('mover', Path(__file__).resolve().parent.parent / 'ki_sheet mover.py')
mover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mover)


class MoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def schematic(self, name, *refs):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        sheets = ''.join('(sheet (property "Sheetfile" "' + r + '"))' for r in refs)
        path.write_text('(kicad_sch ' + sheets + ')', encoding='utf-8')
        return path

    def test_nested_and_shared_sheet_backup_and_repeat(self):
        root = self.schematic('root.kicad_sch', 'a.kicad_sch', 'sub/b.kicad_sch')
        self.schematic('a.kicad_sch', 'sub/b.kicad_sch')
        self.schematic('sub/b.kicad_sch')
        originals = {p: p.read_bytes() for p in self.base.rglob('*.kicad_sch')}
        backup = mover.apply(mover.plan(root))
        for path, data in originals.items():
            self.assertEqual((backup / path.relative_to(self.base)).read_bytes(), data)
        self.assertEqual([r[2] for r in mover.references(root.read_text())],
                         ['sheet/a.kicad_sch', 'sheet/b.kicad_sch'])
        self.assertEqual([r[2] for r in mover.references((self.base / 'sheet/a.kicad_sch').read_text())],
                         ['b.kicad_sch'])
        self.assertFalse((self.base / 'a.kicad_sch').exists())
        mover.plan(root)

    def test_cycle(self):
        root = self.schematic('root.kicad_sch', 'a.kicad_sch')
        self.schematic('a.kicad_sch', 'root.kicad_sch')
        with self.assertRaisesRegex(ValueError, 'Circular'):
            mover.plan(root)

    def test_duplicate_and_missing(self):
        root = self.schematic('root.kicad_sch', 'x/a.kicad_sch', 'y/a.kicad_sch')
        self.schematic('x/a.kicad_sch')
        with self.assertRaises(FileNotFoundError):
            mover.plan(root)
        self.schematic('y/a.kicad_sch')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            mover.plan(root)

    def test_quoted_sheet_text_is_ignored(self):
        self.assertEqual(list(mover.references('(text "(sheet fake)")')), [])

    def test_replace_failure_restores_originals_and_cleans_temps(self):
        root = self.schematic('root.kicad_sch', 'a.kicad_sch', 'b.kicad_sch')
        self.schematic('a.kicad_sch')
        self.schematic('b.kicad_sch')
        originals = {p: p.read_bytes() for p in self.base.rglob('*.kicad_sch')}
        replace = mover.os.replace
        calls = 0

        def fail_third(src, dst):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError('injected failure')
            replace(src, dst)

        with patch.object(mover.os, 'replace', side_effect=fail_third):
            with self.assertRaises(OSError):
                mover.apply(mover.plan(root))
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(list((self.base / 'sheet').iterdir()), [])
        self.assertEqual(list(self.base.rglob('.kicad_sheet_*')), [])

    def test_changed_preview_is_rejected(self):
        root = self.schematic('root.kicad_sch')
        planned = mover.plan(root)
        root.write_text('(kicad_sch (version 1))', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            mover.apply(planned)


if __name__ == '__main__':
    unittest.main()
