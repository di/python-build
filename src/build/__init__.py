# SPDX-License-Identifier: MIT

'''
python-build - A simple, correct PEP517 package builder
'''
__version__ = '0.0.3.1'

import difflib
import importlib
import os
import sys
import warnings

from typing import Dict, List, Mapping, Optional, Set, Union

import pep517.wrappers
import toml
import toml.decoder


if sys.version_info < (3,):
    FileNotFoundError = IOError
    PermissionError = OSError


ConfigSettings = Dict[str, Union[str, List[str]]]


_DEFAULT_BACKEND = {
    'build-backend': 'setuptools.build_meta:__legacy__',
    'requires': [
        'setuptools >= 40.8.0',
        'wheel'
    ]
}


class BuildException(Exception):
    '''
    Exception raised by ProjectBuilder
    '''


class BuildBackendException(Exception):
    '''
    Exception raised when the backend fails
    '''


class TypoWarning(Warning):
    '''
    Warning raised when a potential typo is found
    '''


def check_version(requirement_string, extra=''):  # type: (str, str) -> bool
    '''
    :param requirement_string: Requirement string
    :param extra: Extra (eg. test in myproject[test])
    '''
    import packaging.requirements

    if sys.version_info >= (3, 8):
        from importlib import metadata as importlib_metadata
    else:
        import importlib_metadata

    req = packaging.requirements.Requirement(requirement_string)
    env = {
        'extra': extra
    }

    if req.marker and not req.marker.evaluate(env):
        return True

    try:
        version = importlib_metadata.version(req.name)
        metadata = importlib_metadata.metadata(req.name)
    except importlib_metadata.PackageNotFoundError:
        return False

    for extra in req.extras:
        if extra not in (metadata.get_all('Provides-Extra') or []):
            return False

    if req.specifier:
        return req.specifier.contains(version)

    return True


def _find_typo(dictionary, expected):  # type: (Mapping[str, str], str) -> None
    if expected not in dictionary:
        for obj in dictionary:
            if difflib.SequenceMatcher(None, expected, obj).ratio() >= 0.8:
                warnings.warn(
                    "Found '{}' in pyproject.toml, did you mean '{}'?".format(obj, expected),
                    TypoWarning
                )


class ProjectBuilder(object):
    def __init__(self, srcdir='.', config_settings=None):  # type: (str, Optional[ConfigSettings]) -> None
        '''
        :param srcdir: Source directory
        '''
        self.srcdir = srcdir
        self.config_settings = config_settings if config_settings else {}

        spec_file = os.path.join(srcdir, 'pyproject.toml')

        try:
            with open(spec_file) as f:
                self._spec = toml.load(f)
        except FileNotFoundError:
            self._spec = {}
        except PermissionError as e:
            raise BuildException("{}: '{}' ".format(e.strerror, e.filename))
        except toml.decoder.TomlDecodeError as e:
            raise BuildException("Failed to parse pyproject.toml: {} ".format(e))

        _find_typo(self._spec, 'build-system')
        self._build_system = self._spec.get('build-system', _DEFAULT_BACKEND)

        if 'build-backend' not in self._build_system:
            _find_typo(self._build_system, 'build-backend')
            _find_typo(self._build_system, 'requires')
            self._build_system['build-backend'] = _DEFAULT_BACKEND['build-backend']
            self._build_system['requires'] = self._build_system.get('requires', []) + _DEFAULT_BACKEND['requires']

        if 'requires' not in self._build_system:
            raise BuildException("Missing 'build-system.requires' in pyproject.yml")

        self._backend = self._build_system['build-backend']

        try:
            importlib.import_module(self._backend.split(':')[0])
        except ImportError:  # can't mock importlib.import_module  # pragma: no cover
            raise BuildException("Backend '{}' is not available".format(self._backend))

        self.hook = pep517.wrappers.Pep517HookCaller(self.srcdir, self._backend,
                                                     backend_path=self._build_system.get('backend-path'))

    @property
    def build_dependencies(self):  # type: () -> Set[str]
        return set(self._build_system['requires'])

    def get_dependencies(self, distribution):  # type: (str) -> Set[str]
        '''
        Returns a set of dependencies
        '''
        get_requires = getattr(self.hook, 'get_requires_for_build_{}'.format(distribution))

        try:
            return set(get_requires(self.config_settings))
        except Exception as e:  # noqa: E722
            raise BuildBackendException('Backend operation failed: {}'.format(e))

    def check_dependencies(self, distribution):  # type: (str) -> Set[str]
        '''
        Returns a set of the missing dependencies

        :param distribution: Distribution to build (sdist or wheel)
        '''
        dependencies = self.get_dependencies(distribution)
        dependencies.update(self.build_dependencies)

        return {dep for dep in dependencies if not check_version(dep)}

    def build(self, distribution, outdir):  # type: (str, str) -> None
        '''
        Builds a distribution

        :param distribution: Distribution to build (sdist or wheel)
        :param outdir: Outpur directory
        '''
        build = getattr(self.hook, 'build_{}'.format(distribution))

        try:
            build(outdir, self.config_settings)
        except Exception as e:  # noqa: E722
            raise BuildBackendException('Backend operation failed: {}'.format(e))
