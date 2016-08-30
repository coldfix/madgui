# encoding: utf-8
"""
Matching tool for a :class:`LineView` instance.
"""

# force new style imports
from __future__ import absolute_import

# internal
from madgui.core import wx
from madgui.core.plugin import HookCollection
from madgui.resource.package import PackageResource
from madgui.util.unit import strip_unit

# exported symbols
__all__ = [
    'MatchTool',
]


class MatchTool(object):

    """
    Controller that performs matching when clicking on an element.
    """

    def __init__(self, panel):
        """Add toolbar tool to panel and subscribe to capture events."""
        self.hook = HookCollection(
            start='madgui.component.matching.start')
        self.cid = None
        self.segment = panel.view.segment
        self.panel = panel
        self.view = panel.view
        self.matcher = None
        # toolbar tool
        res = PackageResource('madgui.data')
        with res.open('cursor.xpm') as xpm:
            img = wx.ImageFromStream(xpm, wx.BITMAP_TYPE_XPM)
        bmp = wx.BitmapFromImage(img)
        self.toolbar = panel.toolbar
        self.tool = panel.toolbar.AddCheckTool(
                wx.ID_ANY,
                bitmap=bmp,
                shortHelp='Beam matching',
                longHelp='Match by specifying constraints for envelope x(s), y(s).')
        panel.Bind(wx.EVT_TOOL, self.OnMatchClick, self.tool)
        # setup mouse capture
        panel.hook.capture_mouse.connect(self.stop_match)

    def OnMatchClick(self, event):
        """Invoked when user clicks Match-Button"""
        if event.IsChecked():
            self.start_match()
        else:
            self.stop_match()

    def start_match(self):
        """Start matching mode."""
        self.panel.hook.capture_mouse()
        self.cid = self.view.figure.canvas.mpl_connect(
                'button_press_event',
                self.on_match)
        app = self.panel.GetTopLevelParent().app
        self.matcher = Matching(self.segment, app.conf['matching'])
        self.hook.start(self.matcher, self.view)

    def stop_match(self):
        """Stop matching mode."""
        if self.cid is not None:
            self.view.figure.canvas.mpl_disconnect(self.cid)
            self.cid = None
            self.toolbar.ToggleTool(self.tool.Id, False)
            self.matcher.stop()

    def on_match(self, event):

        """
        Draw new constraint and perform matching.

        Invoked after the user clicks in matching mode.
        """

        axes = event.inaxes
        if axes is None:
            return
        name = axes.twiss_name
        conj = axes.twiss_conj

        elem = self.segment.element_by_position(
            event.xdata * self.view.unit['s'])
        if elem is None or 'name' not in elem:
            return

        if event.button == 2:
            self.matcher.remove_constraint(name, elem)
            self.matcher.remove_constraint(conj, elem)
            return
        elif event.button != 1:
            return

        orig_cursor = self.panel.GetCursor()
        wait_cursor = wx.StockCursor(wx.CURSOR_WAIT)
        self.panel.SetCursor(wait_cursor)

        # By default, the list of constraints will be reset. The shift/alt
        # keys are used to add more constraints.
        pressed_keys = event.key or ''
        add_keys = ['shift', 'control']
        if not any(add_key in pressed_keys for add_key in add_keys):
            self.matcher.clear_constraints()

        # add the clicked constraint
        envelope = event.ydata * self.view.unit[name]
        self.matcher.add_constraint(name, elem, envelope)

        # add another constraint to hold the orthogonal axis constant
        orth_env = self.segment.get_twiss(elem, conj)
        self.matcher.add_constraint(conj, elem, orth_env)

        self.matcher.match()
        self.panel.SetCursor(orig_cursor)


class MatchTransform(object):

    def __init__(self, segment):
        self._ex = segment.summary['ex']
        self._ey = segment.summary['ey']

    def envx(self, val):
        return 'betx', val*val/self._ex

    def envy(self, val):
        return 'bety', val*val/self._ey

    def x(self, val):
        return 'x', val

    posx = x

    def y(self, val):
        return 'y', val

    posy = y


def _get_any_elem_param(elem, params):
    for param in params:
        try:
            return elem[param]._expression
        except KeyError:
            pass
        except AttributeError:
            if strip_unit(elem[param]) != 0.0:
                return elem['name'] + '->' + param
    raise ValueError()


class Matching(object):


    def __init__(self, segment, rules):
        self.hook = HookCollection(
            stop=None,
            add_constraint=None,
            remove_constraint=None,
            clear_constraints=None)
        self.segment = segment
        self.constraints = {}
        self._elements = segment.elements
        self._rules = rules
        self._variable_parameters = {}

    def stop(self):
        self.clear_constraints()
        self.hook.stop()

    def _allvars(self, axis):
        try:
            allvars = self._variable_parameters[axis]
        except KeyError:
            # filter element list for usable types:
            param_spec = self._rules.get(axis, {})
            allvars = [(elem, param_spec[elem['type']])
                       for elem in self._elements
                       if elem['type'] in param_spec]
            self._variable_parameters[axis] = allvars
        return allvars

    def match(self):

        """Perform matching according to current constraints."""

        segment = self.segment
        simul = self.segment.session
        trans = MatchTransform(segment)

        # transform constraints (envx => betx, etc)
        trans_constr = {}
        for axis, constr in self.constraints.items():
            for elem, value in constr:
                trans_name, trans_value = getattr(trans, axis)(value)
                this_constr = trans_constr.setdefault(trans_name, [])
                this_constr.append((elem, trans_value))

        # The following uses a greedy algorithm to select all elements that
        # can be used for varying. This means that for advanced matching it
        # will most probably not work.
        # Copy all needed variable lists (for later modification):
        allvars = {axis: self._allvars(axis)[:]
                   for axis in trans_constr}
        vary = []
        for axis, constr in trans_constr.items():
            for elem, envelope in constr:
                at = elem['at']
                allowed = [v for v in allvars[axis] if v[0]['at'] < at]
                if not allowed:
                    # No variable in range found! Ok.
                    continue
                v = max(allowed, key=lambda v: v[0]['at'])
                expr = _get_any_elem_param(v[0], v[1])
                if expr is None:
                    allvars[axis].remove(v)
                else:
                    vary.append(expr)
                    for c in allvars.values():
                        try:
                            c.remove(v)
                        except ValueError:
                            pass

        # create constraints list to be passed to Madx.match
        constraints = []
        for name, constr in trans_constr.items():
            for elem, val in constr:
                constraints.append({
                    'range': elem['name'],
                    name: simul.utool.strip_unit(name, val)})

        twiss_args = simul.utool.dict_strip_unit(segment.twiss_args)
        simul.madx.match(sequence=segment.sequence.name,
                         vary=vary,
                         constraints=constraints,
                         twiss_init=twiss_args)
        segment.twiss()

    def _gconstr(self, axis):
        return self.constraints.get(axis, [])

    def _sconstr(self, axis):
        return self.constraints.setdefault(axis, [])

    def find_constraint(self, axis, elem):
        """Find and return the constraint for the specified element."""
        return [c for c in self._gconstr(axis) if c[0] == elem]

    def add_constraint(self, axis, elem, envelope):
        """Add constraint and perform matching."""
        self.remove_constraint(axis, elem)
        self._sconstr(axis).append( (elem, envelope) )
        self.hook.add_constraint()

    def remove_constraint(self, axis, elem):
        """Remove the constraint for elem."""
        try:
            orig = self.constraints[axis]
        except KeyError:
            return
        filtered = [c for c in orig if c[0]['name'] != elem['name']]
        if filtered:
            self.constraints[axis] = filtered
        else:
            del self.constraints[axis]
        if len(filtered) < len(orig):
            self.hook.remove_constraint()

    def clear_constraints(self):
        """Remove all constraints."""
        self.constraints = {}
        self.hook.clear_constraints()
