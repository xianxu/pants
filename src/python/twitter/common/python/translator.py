import os

from abc import abstractmethod
from zipimport import zipimporter

from twitter.common.dirutil import safe_rmtree, safe_mkdtemp
from twitter.common.lang import AbstractClass, Compatibility

from .distiller import Distiller
from .http import SourceLink, EggLink
from .installer import Installer
from .platforms import Platform
from .tracer import TRACER

from pkg_resources import (
  Distribution,
  EggMetadata,
  PathMetadata)


if Compatibility.PY3:
  import urllib.error as urllib_error
else:
  import urllib2 as urllib_error


class TranslatorBase(AbstractClass):
  """Translate a link into a distribution."""
  @abstractmethod
  def translate(self, link):
    pass


class ChainedTranslator(TranslatorBase):
  """Glue a sequence of Translators together in priority order.

  The first Translator to resolve a requirement wins.
  """
  def __init__(self, *translators):
    self._translators = list(filter(None, translators))
    for tx in self._translators:
      if not isinstance(tx, TranslatorBase):
        raise ValueError('Expected a sequence of translators, got %s instead.' % type(tx))

  def translate(self, link):
    for tx in self._translators:
      dist = tx.translate(link)
      if dist:
        return dist


def dist_from_egg(egg_path):
  if os.path.isdir(egg_path):
    metadata = PathMetadata(egg_path, os.path.join(egg_path, 'EGG-INFO'))
  else:
    # Assume it's a file or an internal egg
    metadata = EggMetadata(zipimporter(egg_path))
  return Distribution.from_filename(egg_path, metadata=metadata)


class SourceTranslator(TranslatorBase):
  def __init__(self, install_cache=None, platform=Platform.current(), python=Platform.python(),
               conn_timeout=None):
    self._install_cache = install_cache or safe_mkdtemp()
    self._platform = platform
    self._python = python
    self._conn_timeout = conn_timeout

  def translate(self, link):
    """From a link, translate a distribution."""
    if not isinstance(link, SourceLink):
      return None
    if not Platform.compatible(Platform.current(), self._platform):
      return None
    if not Platform.version_compatible(Platform.python(), self._python):
      return None
    unpack_path, installer = None, None
    try:
      unpack_path = link.fetch(conn_timeout=self._conn_timeout)
      with TRACER.timed('Installing %s' % link.name):
        installer = Installer(unpack_path, strict=(link.name != 'distribute'))
      with TRACER.timed('Distilling %s' % link.name):
        try:
          dist = installer.distribution()
        except Installer.InstallFailure:
          return None
        return dist_from_egg(Distiller(dist).distill(into=self._install_cache))
    finally:
      if installer:
        installer.cleanup()
      if unpack_path:
        safe_rmtree(unpack_path)


class EggTranslator(TranslatorBase):
  def __init__(self, install_cache=None, platform=Platform.current(), python=Platform.python(),
              conn_timeout=None):
    self._install_cache = install_cache or safe_mkdtemp()
    self._platform = platform
    self._python = python
    self._conn_timeout = conn_timeout

  def translate(self, link):
    """From a link, translate a distribution."""
    if not isinstance(link, EggLink):
      return None
    if not Platform.distribution_compatible(link, python=self._python, platform=self._platform):
      return None
    try:
      egg = link.fetch(location=self._install_cache, conn_timeout=self._conn_timeout)
    except urllib_error.URLError as e:
      TRACER.log('Failed to fetch %s: %s' % (link, e))
      return None
    return dist_from_egg(egg)


class Translator(object):
  @staticmethod
  def default(install_cache=None, platform=Platform.current(), python=Platform.python(),
              conn_timeout=None):
    return ChainedTranslator(
      EggTranslator(install_cache=install_cache, platform=platform, python=python,
                    conn_timeout=conn_timeout),
      SourceTranslator(install_cache=install_cache, platform=platform, python=python,
                       conn_timeout=conn_timeout))
