# encoding: utf-8
"""
Widget component to find/open a model.
"""

# force new style imports
from __future__ import absolute_import

# standard library
import os
from functools import partial
from glob import glob
from pkg_resources import iter_entry_points

# internal
from madgui.core import wx
from madgui.component.model import Locator as _Locator
from madgui.resource.file import FileResource
from madgui.widget.input import Widget

# exported symbols
__all__ = [
    'OpenModelWidget',
]


class Locator(_Locator):

    @classmethod
    def from_path(cls, path_name):
        """Returns a Locator that lists all models in the given directory."""
        if not os.path.isdir(path_name):
            return None
        return cls(FileResource(path_name))


class OpenModelWidget(Widget):

    """
    Open dialog for models contained in python packages.
    """

    Title = "Select model"

    def _AddCombo(self, label, combo_style):
        """Add a label + combobox to the tabular sizer."""
        ctrl_text = wx.StaticText(self.Window, label=label)
        ctrl_combo = wx.ComboBox(self.Window, style=combo_style)
        flag = wx.ALL|wx.ALIGN_CENTER_VERTICAL
        self.sizer.Add(ctrl_text, flag=flag|wx.ALIGN_LEFT, border=5)
        self.sizer.Add(ctrl_combo, flag=flag|wx.EXPAND, border=5)
        return ctrl_combo

    def CreateControls(self, window):
        """Create subcontrols and layout."""
        # Create box sizer
        controls = wx.FlexGridSizer(rows=3, cols=2)
        controls.SetFlexibleDirection(wx.HORIZONTAL)
        controls.AddGrowableCol(1, 1)
        controls.SetNonFlexibleGrowMode(wx.FLEX_GROWMODE_ALL)
        self.sizer = controls
        # Create controls
        self.ctrl_pkg = self._AddCombo('Source:', wx.CB_DROPDOWN|wx.CB_SORT)
        self.ctrl_model = self._AddCombo('Model:', wx.CB_READONLY|wx.CB_SORT)
        # register for events
        self.Window.Bind(wx.EVT_TEXT, self.OnPackageChange, self.ctrl_pkg)
        self.Window.Bind(wx.EVT_COMBOBOX, self.OnPackageChange, self.ctrl_pkg)
        self.ctrl_pkg.SetMinSize(wx.Size(200, -1))
        return controls

    def OnPackageChange(self, event):
        """Update model list when package name is changed."""
        ctrl = self.ctrl_pkg
        sel = ctrl.GetSelection()
        val = ctrl.GetValue()
        if sel == wx.NOT_FOUND and val in self.locators:
            ctrl.SetStringSelection(val)
        self.UpdateModelList()

    def GetCurrentLocator(self):
        """Get the currently selected locator."""
        ctrl = self.ctrl_pkg
        if ctrl.GetSelection() == wx.NOT_FOUND:
            source = ctrl.GetValue()
            return Locator.from_path(source)
        else:
            return self.locators[ctrl.GetStringSelection()]()

    def GetModelList(self):
        """Get list of models in the package specified by the input field."""
        return list(self.locator.list_models()) if self.locator else []

    def UpdateLocatorList(self):
        """Update the list of locators shown in the dialog."""
        # Note that entrypoints are lazy-loaded:
        load = lambda ep: ep.load()()
        self.locators = {u'<{}>'.format(ep.name): partial(load, ep)
                         for ep in iter_entry_points('madgui.models')}
        self.locators.update({
            path: partial(Locator.from_path, path)
            for path in self.model_pathes
        })
        # Format entrypoint names, so they can't be confused with package
        # names. This can be used in the EVT_TEXT handler to decide whether
        # to use the entrypoint or package:
        self.ctrl_pkg.SetItems(list(self.locators))
        self.ctrl_pkg.SetSelection(0)

    def UpdateModelList(self):
        """Update displayed model list."""
        # UpdateModelList is called on initialization and each time the
        # 'source' field changes. So this is the place to update the current
        # locator. Note that is a deliberate choice not to cache anything
        # located, so files can be changed before applying the dialog:
        self.locator = self.GetCurrentLocator()
        self.modellist = self.GetModelList()
        self.ctrl_model.SetItems(self.modellist)
        self.ctrl_model.SetSelection(0)
        self.ctrl_model.Enable(bool(self.modellist))

    def GetData(self):
        """Get (model_definition, repository)."""
        locator = self.locator
        if locator:
            mdata = locator.get_definition(self.ctrl_model.GetValue())
            repo = locator.get_repository(mdata)
        else:
            mdata = None
            repo = None
        return mdata, repo

    def SetData(self, model_pathes):
        """Update displayed package and model name."""
        self.model_pathes = [path
                             for expr in model_pathes
                             for path in glob(expr)
                             if os.path.isdir(path)]
        self.UpdateLocatorList()
        self.UpdateModelList()

    def Validate(self, parent):
        """Update the status of the OK button."""
        return bool(self.modellist)
