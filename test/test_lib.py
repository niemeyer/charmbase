#!/usr/bin/env python3
# Copyright 2020 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys

from collections import namedtuple
from importlib.machinery import ModuleSpec
from pathlib import Path
from tempfile import mkdtemp, mkstemp
from unittest import TestCase
from unittest.mock import patch
from random import shuffle
from shutil import rmtree
from textwrap import dedent

import ops.lib


def _mklib(topdir: str, pkgname: str, libname: str) -> Path:
    """Make a for-testing library.

    Args:
        topdir: the toplevel directory in which the package will be created.
             This directory must already exist.
        pkgname: the name of the package to create in the toplevel directory.
             this package will have an empty __init__.py.
        libname: the name of the library directory to create under the package.

    Returns:
        a :class:`Path` to the ``__init__.py`` of the created library.
        This file will not have been created yet.
    """
    pkg = Path(topdir) / pkgname
    try:
        pkg.mkdir()
    except FileExistsError:
        pass
    else:
        (pkg / '__init__.py').write_text('')

    lib = pkg / 'opslib' / libname
    lib.mkdir(parents=True)

    return lib / '__init__.py'


def _flatten(specgen):
    return [os.path.dirname(spec.origin) for spec in specgen]

class TestLibFinder(TestCase):
    def _mkdtemp(self) -> str:
        tmpdir = mkdtemp()
        self.addCleanup(rmtree, tmpdir)
        return tmpdir

    def test_single(self):
        tmpdir = self._mkdtemp()

        self.assertEqual(list(ops.lib._find_all_specs([tmpdir])), [])

        _mklib(tmpdir, "foo", "bar").write_text("")

        self.assertEqual(
            _flatten(ops.lib._find_all_specs([tmpdir])),
            [tmpdir + '/foo/opslib/bar'])

    def test_multi(self):
        tmpdirA = self._mkdtemp()
        tmpdirB = self._mkdtemp()

        dirs = [tmpdirA, tmpdirB]

        expected = []
        for top in [tmpdirA, tmpdirB]:
            for pkg in ["bar", "baz"]:
                for lib in ["quux", "meep"]:
                    _mklib(top, pkg, lib).write_text("")
                    expected.append(os.path.join(top, pkg, "opslib", lib))

        self.assertEqual(
            sorted(_flatten(ops.lib._find_all_specs(dirs))),
            sorted(expected))

    def test_cwd(self):
        tmpcwd = self._mkdtemp()
        os.chdir(tmpcwd)
        self.addCleanup(os.chdir, tmpcwd)

        dirs = [""]

        self.assertEqual(list(ops.lib._find_all_specs(dirs)), [])

        _mklib(tmpcwd, "foo", "bar").write_text("")

        self.assertEqual(
            _flatten(ops.lib._find_all_specs(dirs)),
            ['./foo/opslib/bar'])

    def test_bogus_topdir(self):
        tmpdir = self._mkdtemp()

        dirs = [tmpdir, "/bogus"]

        self.assertEqual(list(ops.lib._find_all_specs(dirs)), [])

        _mklib(tmpdir, "foo", "bar").write_text("")

        self.assertEqual(
            _flatten(ops.lib._find_all_specs(dirs)),
            [tmpdir + '/foo/opslib/bar'])

    def test_bogus_opsdir(self):
        tmpdir = self._mkdtemp()

        self.assertEqual(list(ops.lib._find_all_specs([tmpdir])), [])

        _mklib(tmpdir, "foo", "bar").write_text('')

        path = Path(tmpdir) / 'baz'
        path.mkdir()
        (path / 'opslib').write_text('')

        self.assertEqual(
            _flatten(ops.lib._find_all_specs([tmpdir])),
            [tmpdir + '/foo/opslib/bar'])


class TestLibParser(TestCase):
    def _mkmod(self, name: str, content: str = None) -> ModuleSpec:
        fd, fname = mkstemp(text=True)
        self.addCleanup(os.unlink, fname)
        if content is not None:
            with os.fdopen(fd, mode='wt', closefd=False) as f:
                f.write(dedent(content))
        os.close(fd)
        return ModuleSpec(name=name, loader=None, origin=fname)

    def test_simple(self):
        """Check that we can load a reasonably straightforward lib"""
        m = self._mkmod('foo', '''
        LIBNAME = "foo"
        LIBAPI = 2
        LIBPATCH = 42
        LIBAUTHOR = "alice@example.com"
        LIBANANA = True
        ''')
        lib = ops.lib._parse_lib(m)
        self.assertEqual(lib, ops.lib._Lib(None, "foo", "alice@example.com", 2, 42))
        # also check the repr while we're at it
        self.assertEqual(repr(lib), '_Lib(foo by alice@example.com, API 2, patch 42)')

    def test_incomplete(self):
        """Check that if anything is missing, nothing is returned"""
        m = self._mkmod('foo', '''
        LIBNAME = "foo"
        LIBAPI = 2
        LIBPATCH = 42
        ''')
        self.assertIsNone(ops.lib._parse_lib(m))

    def test_no_origin(self):
        """Check that _parse_lib doesn't choke when given a spec with no origin"""
        # 'just don't crash'
        lib = ops.lib._parse_lib(ModuleSpec(name='hi', loader=None, origin=None))
        self.assertIsNone(lib)

    def test_bogus_origin(self):
        """Check that if the origin is messed up, we don't crash"""
        # 'just don't crash'
        lib = ops.lib._parse_lib(ModuleSpec(name='hi', loader=None, origin='/'))
        self.assertIsNone(lib)

    def test_bogus_lib(self):
        """Check our behaviour when the lib is messed up"""
        # note the syntax error
        m = self._mkmod('foo', '''
        LIBNAME = "1'
        LIBAPI = 2
        LIBPATCH = 42
        LIBAUTHOR = "alice@example.com"
        LIBANANA = True
        ''')
        self.assertIsNone(ops.lib._parse_lib(m))

    def test_name_is_number(self):
        """Check our behaviour when the name in the lib is a number"""
        m = self._mkmod('foo', '''
        LIBNAME = 1
        LIBAPI = 2
        LIBPATCH = 42
        LIBAUTHOR = "alice@example.com"
        ''')
        self.assertIsNone(ops.lib._parse_lib(m))

    def test_api_is_string(self):
        """Check our behaviour when the api in the lib is a string"""
        m = self._mkmod('foo', '''
        LIBNAME = 'foo'
        LIBAPI = '2'
        LIBPATCH = 42
        LIBAUTHOR = "alice@example.com"
        ''')
        self.assertIsNone(ops.lib._parse_lib(m))

    def test_patch_is_string(self):
        """Check our behaviour when the patch in the lib is a string"""
        m = self._mkmod('foo', '''
        LIBNAME = 'foo'
        LIBAPI = 2
        LIBPATCH = '42'
        LIBAUTHOR = "alice@example.com"
        ''')
        self.assertIsNone(ops.lib._parse_lib(m))

    def test_author_is_number(self):
        """Check our behaviour when the author in the lib is a number"""
        m = self._mkmod('foo', '''
        LIBNAME = 'foo'
        LIBAPI = 2
        LIBPATCH = 42
        LIBAUTHOR = 43
        ''')
        self.assertIsNone(ops.lib._parse_lib(m))

class TestLib(TestCase):

    def test_lib_comparison(self):
        self.assertNotEqual(
            ops.lib._Lib(None, "foo", "alice@example.com", 1, 0),
            ops.lib._Lib(None, "bar", "bob@example.com", 0, 1))
        self.assertEqual(
            ops.lib._Lib(None, "foo", "alice@example.com", 1, 1),
            ops.lib._Lib(None, "foo", "alice@example.com", 1, 1))

        self.assertLess(
            ops.lib._Lib(None, "foo", "alice@example.com", 1, 0),
            ops.lib._Lib(None, "foo", "alice@example.com", 1, 1))
        self.assertLess(
            ops.lib._Lib(None, "foo", "alice@example.com", 0, 1),
            ops.lib._Lib(None, "foo", "alice@example.com", 1, 1))
        self.assertLess(
            ops.lib._Lib(None, "foo", "alice@example.com", 1, 1),
            ops.lib._Lib(None, "foo", "bob@example.com", 1, 1))
        self.assertLess(
            ops.lib._Lib(None, "bar", "alice@example.com", 1, 1),
            ops.lib._Lib(None, "foo", "alice@example.com", 1, 1))

        with self.assertRaises(TypeError):
            42 < ops.lib._Lib(None, "bar", "alice@example.com", 1, 1)
        with self.assertRaises(TypeError):
            ops.lib._Lib(None, "bar", "alice@example.com", 1, 1) < 42

        # these two might be surprising in that they don't raise an exception,
        # but they are correct: our __eq__ bailing means Python falls back to
        # its default of checking object identity.
        self.assertNotEqual(ops.lib._Lib(None, "bar", "alice@example.com", 1, 1), 42)
        self.assertNotEqual(42, ops.lib._Lib(None, "bar", "alice@example.com", 1, 1))

    def test_lib_order(self):
        a = ops.lib._Lib(None, "bar", "alice@example.com", 1, 0)
        b = ops.lib._Lib(None, "bar", "alice@example.com", 1, 1)
        c = ops.lib._Lib(None, "foo", "alice@example.com", 1, 0)
        d = ops.lib._Lib(None, "foo", "alice@example.com", 1, 1)
        e = ops.lib._Lib(None, "foo", "bob@example.com", 1, 1)

        for i in range(20):
            with self.subTest(i):
                l = [a, b, c, d, e]
                shuffle(l)
                self.assertEqual(sorted(l), [a, b, c, d, e])

    def test_use_bad_args_types(self):
        with self.assertRaises(TypeError):
            ops.lib.use(1, 2, 'bob@example.com')
        with self.assertRaises(TypeError):
            ops.lib.use('foo', '2', 'bob@example.com')
        with self.assertRaises(TypeError):
            ops.lib.use('foo', 2, ops.lib.use)

    def test_use_bad_args_values(self):
        with self.assertRaises(ValueError):
            ops.lib.use('--help', 2, 'alice@example.com')
        with self.assertRaises(ValueError):
            ops.lib.use('foo', -2, 'alice@example.com')
        with self.assertRaises(ValueError):
            ops.lib.use('foo', 1, 'example.com')


@patch('sys.path', new=())
class TestLibFunctional(TestCase):

    def _mkdtemp(self) -> str:
        tmpdir = mkdtemp()
        self.addCleanup(rmtree, tmpdir)
        return tmpdir

    def test_use_finds_subs(self):
        """Test that ops.lib.use("baz") works when baz is inside a package in the python path."""
        tmpdir = self._mkdtemp()
        sys.path = [tmpdir]

        _mklib(tmpdir, "foo", "bar").write_text(dedent("""
        LIBNAME = "baz"
        LIBAPI = 2
        LIBPATCH = 42
        LIBAUTHOR = "alice@example.com"
        """))

        # autoimport would be done in main
        ops.lib.autoimport()

        # ops.lib.use done by charm author
        baz = ops.lib.use('baz', 2, 'alice@example.com')
        self.assertEqual(baz.LIBNAME, 'baz')
        self.assertEqual(baz.LIBAPI, 2)
        self.assertEqual(baz.LIBPATCH, 42)
        self.assertEqual(baz.LIBAUTHOR, 'alice@example.com')

    def test_use_finds_best_same_toplevel(self):
        """Test that ops.lib.use("baz") works when there are two baz."""

        T = namedtuple("T", "desc sameTop pkgA libA patchA pkgB libB patchB")

        for t in [
            T("same toplevel, different package, same lib, AB",
              True, "fooA", "bar", 40, "fooB", "bar", 42),
            T("same toplevel, different package, same lib, BA",
              True, "fooA", "bar", 42, "fooB", "bar", 40),
            T("same toplevel, same package, different lib, AB",
              True, "foo", "barA", 40, "foo", "barB", 42),
            T("same toplevel, same package, different lib, BA",
              True, "foo", "barA", 42, "foo", "barB", 40),

            T("different toplevel, same package, same lib, AB",
              False, "foo", "bar", 40, "foo", "bar", 42),
            T("different toplevel, same package, same lib, BA",
              False, "foo", "bar", 42, "foo", "bar", 40),

            T("different toplevel, different package, same lib, AB",
              False, "fooA", "bar", 40, "fooB", "bar", 42),
            T("different toplevel, different package, same lib, BA",
              False, "fooA", "bar", 42, "fooB", "bar", 40),
            T("different toplevel, same package, different lib, AB",
              False, "foo", "barA", 40, "foo", "barB", 42),
            T("different toplevel, same package, different lib, BA",
              False, "foo", "barA", 42, "foo", "barB", 40),
        ]:
            with self.subTest(t.desc):
                tmpdirA = self._mkdtemp()
                sys.path = [tmpdirA]
                if t.sameTop:
                    tmpdirB = tmpdirA
                else:
                    tmpdirB = self._mkdtemp()
                    sys.path.append(tmpdirB)

                _mklib(tmpdirA, t.pkgA, t.libA).write_text(dedent("""
                LIBNAME = "baz"
                LIBAPI = 2
                LIBPATCH = {}
                LIBAUTHOR = "alice@example.com"
                """).format(t.patchA))

                _mklib(tmpdirB, t.pkgB, t.libB).write_text(dedent("""
                LIBNAME = "baz"
                LIBAPI = 2
                LIBPATCH = {}
                LIBAUTHOR = "alice@example.com"
                """).format(t.patchB))


                # autoimport would be done in main
                ops.lib.autoimport()

                # ops.lib.use done by charm author
                baz = ops.lib.use('baz', 2, 'alice@example.com')
                self.assertEqual(baz.LIBNAME, 'baz')
                self.assertEqual(baz.LIBAPI, 2)
                self.assertEqual(baz.LIBPATCH, 42)
                self.assertEqual(baz.LIBAUTHOR, 'alice@example.com')

    def test_none_found(self):
        with self.assertRaises(ImportError):
            ops.lib.use('foo', 1, 'alice@example.com')

    def test_others_found(self):
        tmpdir = self._mkdtemp()
        sys.path = [tmpdir]

        _mklib(tmpdir, "foo", "bar").write_text(dedent("""
        LIBNAME = "baz"
        LIBAPI = 2
        LIBPATCH = 42
        LIBAUTHOR = "alice@example.com"
        """))

        ops.lib.autoimport()

        # sanity check that ops.lib.use works
        baz = ops.lib.use('baz', 2, 'alice@example.com')
        self.assertEqual(baz.LIBAPI, 2)

        with self.assertRaises(ImportError):
            ops.lib.use('baz', 1, 'alice@example.com')

        with self.assertRaises(ImportError):
            ops.lib.use('baz', 2, 'bob@example.com')
