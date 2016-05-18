# encoding: utf-8
"""
Session component for the MadGUI application.
"""

# force new style imports
from __future__ import absolute_import

# standard library
from collections import namedtuple
from functools import reduce
import os
import subprocess

# 3rd party
from cpymad.madx import Madx
from cpymad.util import normalize_range_name

import numpy as np
import yaml

# internal
from madgui.core.plugin import HookCollection
from madgui.util.common import temp_filename

# exported symbols
__all__ = [
    'ElementInfo',
    'Session',
    'Segment',
]


def kronecker(i, j):
    return 1 if i == j else 0


class Session(object):

    """
    Contains the whole global state of a MAD-X instance and (possibly) loaded
    metadata.

    :ivar utool: Unit conversion tool
    :ivar libmadx: Low level cpymad API
    :ivar madx: CPyMAD interpretor instance

    :ivar data: data loaded from model
    :ivar repo: resource provider

    :ivar segment: Currently active segment

    :ivar rpc_client: Low level MAD-X RPC client
    :ivar remote_process: MAD-X process
    """

    # TODO: more logging
    # TODO: saveable state

    def __init__(self, utool, repo=None):
        """Initialize with (Madx, Model)."""
        self.utool = utool
        self.data = {}
        self.repo = repo
        self.segment = None
        self.init_files = []
        # stdin=None leads to an error on windows when STDIN is broken.
        # therefore, we need set stdin=os.devnull by passing stdin=False:
        self.madx = madx = Madx(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=False,
            bufsize=0)
        self.libmadx = madx._libmadx
        self.rpc_client = madx._service
        self.remote_process = madx._process

    def close(self):
        """Close current session. Stop MAD-X interpreter."""
        if self.rpc_client:
            self.rpc_client.close()
        self.rpc_client = None
        self.remote_process = None
        self.libmadx = None
        self.madx = None
        if self.segment is not None:
            self.segment.destroy()

    def call(self, name):
        """Load a MAD-X file into the current session."""
        with self.repo.filename(name) as f:
            self.madx.call(f, True)
        self.init_files.append(name)

    #----------------------------------------
    # Serialization
    #----------------------------------------

    # current version of model API
    API_VERSION = 1

    @classmethod
    def check_compatibility(cls, data):
        """
        Check a model definition for compatibility.

        :param dict data: a model definition to be tested
        :raises ValueError: if the model definition is incompatible
        """
        model_api = data.get('api_version', 'undefined')
        if model_api != cls.API_VERSION:
            raise ValueError(("Incompatible model API version: {!r},\n"
                              "              Required version: {!r}")
                             .format(model_api, cls.API_VERSION))

    def save(self, filename):
        ext = os.path.splitext(filename)[1]
        data = self._as_model()
        text = yaml.safe_dump(data, default_flow_style=False)
        with open(filename, 'wt') as f:
            f.write(text)

    def _as_model(self):
        data = self.data.copy()
        if self.segment:
            data.update(self.segment.data)
        data.update({
            'api_version': self.API_VERSION,
            'init-files': self.init_files,
        })
        self._serialize_params(data, 'twiss')
        self._serialize_params(data, 'beam')
        data['range'] = list(data['range'])
        return data

    @classmethod
    def load(cls, utool, repo, filename):
        """Load model or plain MAD-X file."""
        ext = os.path.splitext(filename)[1]
        if ext.lower() in ('.yml', '.yaml'):
            return cls.load_model(utool, repo, filename)
        else:
            return cls.load_madx_file(utool, repo, filename)

    @classmethod
    def load_madx_file(cls, utool, repo, filename):
        """Load a plain MAD-X file."""
        session = cls(utool, repo)
        session.call(filename)
        return session

    @classmethod
    def load_model(cls, utool, repo, filename):
        """Load model data from file."""
        data = repo.yaml(filename, encoding='utf-8')
        cls.check_compatibility(data)
        cls._load_params(data, utool, repo, 'beam')
        cls._load_params(data, utool, repo, 'twiss')
        session = cls(utool, repo)
        session.data = data
        for f in data.get('init-files', []):
            session.call(f)
        return session

    def init_segment(self, data):
        """Create a segment."""
        self.segment = Segment(
            session=self,
            sequence=data['sequence'],
            range=data['range'],
            beam=data['beam'],
            twiss_args=data['twiss'],
            show_element_indicators=data.get('indicators', True),
        )

    @classmethod
    def _load_params(cls, data, utool, repo, name):
        """Load parameter dict from file if necessary and add units."""
        vals = data.get(name, {})
        if isinstance(data[name], basestring):
            vals = repo.yaml(vals, encoding='utf-8')
        data[name] = utool.dict_add_unit(vals)

    def _serialize_params(self, data, name):
        vals = data.get(name, {})
        vals = self.utool.dict_strip_unit(vals)
        data[name] = vals


    def _get_seq_model(self, sequence_name):
        """
        Return a model as good as possible from the last TWISS statement used
        for the given sequence, if available.

        Note that it seems currently not possible to reliably access prior
        TWISS statements and hence the information required to guess the
        model is extracted from the TWISS tables associated with the
        sequences. This means that

            - twiss tables may accidentally be associated with the wrong
              sequence
            - there is no reliable way to tell which parameters were set in
              the twiss command and hence deduce the correct (expected) model
            - you have to make sure the twiss range starts with a zero-width
              element (e.g. MARKER), otherwise TWISS parameters at the start
              of the range can not be reliably extrapolated

        The returned model should be seen as a first guess/approximation. Some
        fields may be empty if they cannot reliably be determined.

        :raises RuntimeError: if the sequence is undefined
        """
        try:
            sequence = self.madx.sequences[sequence_name]
        except KeyError:
            raise RuntimeError("The sequence is not defined.")
        try:
            beam = sequence.beam
        except RuntimeError:
            beam = {}
        try:
            range, twiss = self._get_twiss(sequence)
        except RuntimeError:
            range = (sequence_name+'$start', sequence_name+'$end')
            twiss = {}
        return {
            'sequence': sequence_name,
            'range': range,
            'beam': self.utool.dict_add_unit(beam),
            'twiss': self.utool.dict_add_unit(twiss),
        }

    def _get_twiss(self, sequence):
        """
        Try to determine (range, twiss) from the MAD-X state.

        :raises RuntimeError: if unable to make a useful guess
        """
        table = sequence.twiss_table        # raises RuntimeError
        try:
            first, last = table.range
        except ValueError:
            raise RuntimeError("TWISS table inaccessible or nonsensical.")
        if first not in sequence.elements or last not in sequence.elements:
            raise RuntimeError("The TWISS table appears to belong to a different sequence.")
        # TODO: this inefficiently copies over the whole table over the pipe
        # rather than just the first row.
        mandatory_fields = {'betx', 'bety', 'alfx', 'alfy'}
        twiss = {
            key: float(data[0])
            for key, data in table.items()
            if issubclass(data.dtype.type, np.number) and (
                    key in mandatory_fields or
                    data[0] != 0
            )
        }
        return (first, last), twiss


ElementInfo = namedtuple('ElementInfo', ['name', 'index', 'at'])


class Segment(object):

    """
    Simulate one fixed segment, i.e. sequence + range.

    :ivar Madx madx:
    :ivar list elements:
    :ivar dict twiss_args:
    """

    _columns = [
        'name', 'l', 'angle', 'k1l',
        's',
        'x', 'y',
        'betx','bety',
        'alfx', 'alfy',
    ]

    # TODO: extend list of merge-columns
    _mixin_columns = [
        'x', 'y',
        'betx','bety',
        'alfx', 'alfy',
    ]

    def __init__(self, session, sequence, range, beam, twiss_args,
                 show_element_indicators):
        """
        :param Session session:
        :param str sequence:
        :param tuple range:
        """
        self.hook = HookCollection(
            update=None,
            remove=None,
            show_element_indicators=None,
        )

        self.session = session
        self.sequence = session.madx.sequences[sequence]

        self.start, self.stop = self.parse_range(range)
        self.range = (normalize_range_name(self.start.name),
                      normalize_range_name(self.stop.name))

        self._beam = beam
        self._twiss_args = twiss_args
        self._show_element_indicators = show_element_indicators
        self._use_beam(beam)

        raw_elements = self.sequence.elements
        # TODO: provide uncached version of elements with units:
        self.elements = list(map(
            session.utool.dict_add_unit, raw_elements))

        # TODO: self.hook.create(self)

        self.twiss()

    @property
    def madx(self):
        return self.session.madx

    @property
    def utool(self):
        return self.session.utool

    @property
    def data(self):
        return {
            'sequence': self.sequence.name,
            'range': self.range,
            'beam': self.beam,
            'twiss': self.twiss_args,
        }

    def get_element_info(self, element):
        """Get :class:`ElementInfo` from element name or index."""
        if isinstance(element, ElementInfo):
            return element
        if isinstance(element, (basestring, dict)):
            element = self.sequence.elements.index(element)
        element_data = self.session.utool.dict_add_unit(
            self.sequence.elements[element])
        if element < 0:
            element += len(self.sequence.elements)
        return ElementInfo(element_data['name'], element, element_data['at'])

    def parse_range(self, range):
        """Convert a range str/tuple to a tuple of :class:`ElementInfo`."""
        if isinstance(range, basestring):
            range = range.split('/')
        start_name, stop_name = range
        return (self.get_element_info(start_name),
                self.get_element_info(stop_name))

    def destroy(self):
        self.session.segment = None
        self.hook.remove()

    @property
    def show_element_indicators(self):
        return self._show_element_indicators

    @show_element_indicators.setter
    def show_element_indicators(self, show):
        if show == self._show_element_indicators:
            return
        self._show_element_indicators = show
        self.hook.show_element_indicators()

    @property
    def twiss_args(self):
        return self._twiss_args

    @twiss_args.setter
    def twiss_args(self, twiss_args):
        self._twiss_args = twiss_args
        self.twiss()

    @property
    def beam(self):
        """Get the beam parameter dictionary."""
        return self._beam

    @beam.setter
    def beam(self, beam):
        """Set beam from a parameter dictionary."""
        self._beam = beam
        self._use_beam(beam)
        self.twiss()

    def _use_beam(self, beam):
        beam = self.utool.dict_strip_unit(beam)
        beam = dict(beam, sequence=self.sequence.name)
        self.madx.command.beam(**beam)

    def element_by_position(self, pos):
        """Find optics element by longitudinal position."""
        if pos is None:
            return None
        for elem in self.elements:
            at, L = elem['at'], elem['l']
            if pos >= at and pos <= at+L:
                return elem
        return None

    def get_element_index(self, elem):
        """Get element index by it name."""
        return self.sequence.elements.index(elem)

    def get_twiss(self, elem, name):
        """Return beam envelope at element."""
        element = self.get_element_info(elem)
        if not self.contains(element):
            return None
        return self.tw[name][element.index - self.start.index]

    def contains(self, element):
        return (self.start.index <= element.index and
                self.stop.index >= element.index)

    def twiss(self):
        """Recalculate TWISS parameters."""
        results = self.raw_twiss()
        # Update TWISS results
        self.tw = self.utool.dict_add_unit(results)
        self.summary = self.utool.dict_add_unit(results.summary)
        # data post processing
        self.tw['s'] += self.start.at
        self.pos = self.tw['s']
        self.tw['envx'] = (self.tw['betx'] * self.summary['ex'])**0.5
        self.tw['envy'] = (self.tw['bety'] * self.summary['ey'])**0.5
        # Create aliases for x,y that have non-empty common prefix. The goal
        # is to make the config file entries less awkward that hold this
        # prefix:
        self.tw['posx'] = self.tw['x']
        self.tw['posy'] = self.tw['y']
        self.hook.update()

    def _get_twiss_args(self, **kwargs):
        twiss_init = self.utool.dict_strip_unit(self.twiss_args)
        twiss_args = {
            'sequence': self.sequence.name,
            'range': self.range,
            'columns': self._columns,
            'twiss_init': twiss_init,
        }
        twiss_args.update(kwargs)
        return twiss_args

    def raw_twiss(self, **kwargs):
        return self.madx.twiss(**self._get_twiss_args(**kwargs))

    def get_transfer_map(self, beg_elem, end_elem):
        """
        Get the transfer matrix R(i,j) between the two elements.

        This requires a full twiss call, so don't do it too often.
        """
        info = self.get_element_info
        twiss_args = self._get_twiss_args()
        twiss_args['range_'] = (info(beg_elem).name, info(end_elem).name)
        twiss_args['tw_range'] = twiss_args.pop('range')
        return self.madx.get_transfer_map_7d(**twiss_args)
