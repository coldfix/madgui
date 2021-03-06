# encoding: utf-8
"""
Core components for a very simple plugin system.

Short class overview:

- :func:`Hook` should be used for all named events (plugins).

- :class:`HookCollection` gives attribute access to a set of hooks.

- :class:`Multicast` is an abstract base class. It only contains mixins and
  is missing the vital attribute ``slots``

- :class:`List` represents a set of event handlers that are manageable from
  within your application.

- :class:`EntryPoint` multicasts events to setuptools entry points.
"""

# force new style imports
from __future__ import absolute_import

# standard library
from pkg_resources import iter_entry_points

# exported symbols
__all__ = [
    'Hook',
    'HookCollection',
    'Multicast',
    'List',
    'EntryPoint',
]


def Hook(name):
    """
    Return a plugin hook with the given name.

    Currently, a plugin hook is basically a globally named signal, i.e. a
    :class:`List` object with an :class:`EntryPoint` object as its first
    client. In the future, a folder based detection mechanism may be added.

    This is the conventional function to use in MadGUI for plugins.

    Note, that the list of dynamically connected clients is not and mustn't
    be a global state! There may be multiple instances being able to add or
    remove clients independently.
    """
    if name:
        return List([EntryPoint(name)])
    else:
        return List()


class HookCollection(object):

    """Manages a collection of Hooks."""

    def __init__(self, **hooks):
        """Initialize empty collection for the given event names."""
        self._hooks = hooks
        self._cache = {}

    def __getattr__(self, name):
        """Access the hook with the specified name."""
        try:
            return self._cache[name]
        except KeyError:
            hook = self._cache[name] = Hook(self._hooks[name])
            return hook


class Multicast(object):

    """
    Signal base class.

    Signals are responsible for multiplexing events to several clients.
    Events can simply be understood as multicast function calls.

    This is an abstract base class. Concretizations need to supply the
    attribute :ivar:`slots`.
    """

    def __call__(*self__args, **kwargs):
        """Call all slots and return reduced result."""
        self = self__args[0]
        args = self__args[1:]
        for slot in list(self.slots):
            slot(*args, **kwargs)


class List(Multicast):

    """
    Signal connected to dynamic list of event handlers.

    Use this class if the list of event handlers needs to be dynamically
    managed from within the application.
    """

    def __init__(self, slots=None):
        """Initialize with an externally created list of slots."""
        if slots is None:
            self.slots = []
        else:
            self.slots = slots

    def connect(self, slot):
        """Register an event handler."""
        return self.slots.append(slot)

    def disconnect(self, slot):
        """Remove an event handler."""
        self.slots.remove(slot)


class EntryPoint(Multicast):

    """
    Signal connected to setuptools entry points.

    Instances of this class are used to dynamically discover and invoke
    event handlers that are installed as setuptools entry points.
    """

    def __init__(self, group, name=None):
        """Set the entry point group and, optionally, implementation name."""
        self._group = group
        self._name = name

    @property
    def slots(self):
        """Return an iterable over all relevant entry points."""
        return (ep.load() for ep in iter_entry_points(self._group, self._name))
