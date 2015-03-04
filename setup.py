#!/usr/bin/env python
# encoding: utf-8
"""
Installation script for MadGUI.

Usage:
    python setup.py install
"""

# Make sure setuptools is available. NOTE: the try/except hack is required to
# make installation work with pip: If an older version of setuptools is
# already imported, `use_setuptools()` will just exit the current process.
try:
    import pkg_resources
except ImportError:
    from ez_setup import use_setuptools
    use_setuptools()

from setuptools import setup
from distutils.util import convert_path


def exec_file(path):
    """Execute a python file and return the `globals` dictionary."""
    namespace = {}
    with open(convert_path(path)) as f:
        exec(f.read(), namespace, namespace)
    return namespace


meta = exec_file('madgui/__init__.py')


setup(
    name='madgui',
    version=meta['__version__'],
    description=meta['__summary__'],
    long_description=open('README.rst').read(),
    author=meta['__author__'],
    author_email=meta['__email__'],
    url=meta['__uri__'],
    license=meta['__license__'],
    classifiers=meta['__classifiers__'],
    packages=[
        'madgui',
        'madgui.component',
        'madgui.core',
        'madgui.resource',
        'madgui.util',
        'madgui.widget',
    ],
    test_suite='nose.collector',
    install_requires=[
        'cpymad>=0.10.1',
        'docopt',
        'matplotlib',
        'numpy',
        'pydicti>=0.0.4',
        'PyYAML',
        'Unum>=4.0',
        # wxPython is a dependency, but we do not require it here, since this
        # will cause the 'pkg_resources.require' runtime check to fail on the
        # control system PCs:
        # 'wxPython>=2.8',
    ],
    entry_points="""
        [gui_scripts]
        madgui = madgui.core.app:App.main
    """,
    package_data={
        'madgui': [
            'config.yml',
            'resource/*.xpm',
            'LICENSE',
        ]
    }
)
