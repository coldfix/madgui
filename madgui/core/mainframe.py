# encoding: utf-8
"""
Notebook window component for MadGUI (main window).
"""

# force new style imports
from __future__ import absolute_import

# standard library
import os
import sys
import logging
import threading

# GUI components
from madgui.core import wx
import wx.aui
from wx.py.shell import Shell

# internal
from madgui.core.plugin import HookCollection
from madgui.component.about import show_about_dialog
from madgui.component.beamdialog import BeamWidget
from madgui.component.lineview import TwissView
from madgui.component.modeldialog import ModelWidget
from madgui.component.session import Session, Segment
from madgui.component.twissdialog import TwissWidget
from madgui.resource.file import FileResource
from madgui.util import unit
from madgui.widget.figure import FigurePanel
from madgui.widget import menu
from madgui.widget.input import ShowModal, Cancellable, Dialog, CancelAction
from madgui.widget.filedialog import OpenDialog, SaveDialog

# exported symbols
__all__ = [
    'MainFrame',
]


if sys.platform == 'win32':
    MDIParentFrame = wx.MDIParentFrame
    MDIChildFrame = wx.MDIChildFrame

    def ShowMDIChildFrame(frame):
        frame.Show()

    def GetMDIChildFrames(parent):
        return [window for window in parent.GetChildren()
                if isinstance(window, MDIChildFrame)]

else:
    MDIParentFrame = wx.aui.AuiMDIParentFrame
    MDIChildFrame = wx.aui.AuiMDIChildFrame

    def ShowMDIChildFrame(frame):
        frame.Layout()
        frame.Fit()
        frame.Activate()

    def GetMDIChildFrames(parent):
        return [window for window in parent.GetClientWindow().GetChildren()
                if isinstance(window, MDIChildFrame)]


def CloseMDIChildren(parent):
    """Close all child frames to prevent a core dump on wxGTK."""
    for window in GetMDIChildFrames(parent):
        window.Destroy()


def monospace(pt_size):
    """Return a monospace font."""
    return wx.Font(pt_size,
                   wx.FONTFAMILY_MODERN,
                   wx.FONTSTYLE_NORMAL,
                   wx.FONTWEIGHT_NORMAL)


class MainFrame(MDIParentFrame):

    """
    Notebook window class for MadGUI (main window).
    """

    def __init__(self, app, show=True):

        """
        Create notebook frame.

        Extends wx.Frame.__init__.
        """

        self.hook = HookCollection(
            init='madgui.core.mainframe.init',
            menu='madgui.core.mainframe.menu',
            reset=None,
        )

        super(MainFrame, self).__init__(
            None, -1,
            title='MadGUI',
            size=wx.Size(800, 600))

        self.views = []
        self.app = app
        self.env = {
            'frame': self,
            'views': self.views,
            'session': None,
        }

        self.madx_units = unit.UnitConverter(
            unit.from_config_dict(self.app.conf['madx_units']))

        self.CreateControls()
        self.Show(show)

    def CreateControls(self):
        # create notebook
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        statusbar = self.CreateStatusBar()
        statusbar.SetFont(monospace(10))

        # create menubar and listen to events:
        self.SetMenuBar(self._CreateMenu())
        self.session = None
        self._ResetSession()

    def _ResetSession(self, session=None):
        """Associate a new Session with this frame."""
        # start new session if necessary
        if session is None:
            session = Session(self.madx_units)
        # remove existing associations
        if self.session:
            self.session.close()
        CloseMDIChildren(self)
        # add new associations
        self._NewLogTab()
        self.session = session
        self.env['session'] = session
        self.env['madx'] = session.madx
        self.env['libmadx'] = session.libmadx
        self.env.pop('segment', None)
        self.env.pop('sequence', None)
        self.env.pop('elements', None)
        self.env.pop('twiss', None)
        threading.Thread(target=self._read_stream,
                         args=(session.remote_process.stdout,)).start()
        self.hook.reset()

    @Cancellable
    def _LoadFile(self, event=None):
        self._ConfirmResetSession()
        wildcards = [("Model files", "*.cpymad.yml"),
                     ("MAD-X files", "*.madx", "*.str", "*.seq"),
                     ("All files", "*")]
        with OpenDialog(self, "Open model", wildcards) as dlg:
            dlg.Directory = self.app.conf.get('model_path', '.')
            ShowModal(dlg)
            name = dlg.Filename
            repo = FileResource(dlg.Directory)
        session = Session.load(self.madx_units, repo, name)
        self._ResetSession(session)
        if not session.madx.sequences:
            return
        with Dialog(self) as dialog:
            widget = ModelWidget(dialog, session)
            data = widget.Query(session.data)
        session.init_segment(data)
        segment = session.segment
        self.env['segment'] = segment
        self.env['sequence'] = segment.sequence
        self.env['elements'] = segment.elements
        self.env['twiss'] = segment.sequence.twiss_table
        TwissView.create(session, self, basename='env')

    @Cancellable
    def _SaveModel(self, event=None):
        data = self.session.data
        pathes = data.get('init-files', [])
        if pathes:
            folder = os.path.dirname(pathes[0])
        else:
            folder = self.app.conf.get('model_path', '.')
        wildcards = [("cpymad model files", "*.cpymad.yml"),
                     ("All files", "*")]
        with SaveDialog(self, 'Save model', wildcards) as dlg:
            dlg.Directory = folder
            ShowModal(dlg)
            path = dlg.Path
        self.session.save(path)

    def _CanSaveModel(self, event):
        event.Enable(bool(self.session.segment))

    @Cancellable
    def _EditTwiss(self, event=None):
        segment = self.GetActiveFigurePanel().view.segment
        utool = self.madx_units
        with Dialog(self) as dialog:
            widget = TwissWidget(dialog, session=self.session)
            segment.twiss_args = widget.Query(segment.twiss_args)

    @Cancellable
    def _SetBeam(self, event=None):
        segment = self.GetActiveFigurePanel().view.segment
        with Dialog(self) as dialog:
            widget = BeamWidget(dialog, session=self.session)
            segment.beam = widget.Query(segment.beam)

    def _ShowIndicators(self, event):
        panel = self.GetActiveFigurePanel()
        segment = panel.view.segment
        segment.show_element_indicators = event.Checked()

    def _UpdateShowIndicators(self, event):
        segment = self.GetActiveFigurePanel().view.segment
        event.Check(bool(segment.show_element_indicators))

    def _ConfirmResetSession(self):
        """Prompt the user to confirm resetting the current session."""
        if not self.session.segment:
            return False
        question = 'Open new MAD-X session? Unsaved changes will be lost.'
        answer = wx.MessageBox(
            question, 'Reset session',
            wx.OK | wx.CANCEL | wx.ICON_QUESTION,
            parent=self)
        if answer == wx.OK:
            return True
        raise CancelAction

    def _CreateMenu(self):
        """Create a menubar."""
        # TODO: this needs to be done more dynamically. E.g. use resource
        # files and/or a plugin system to add/enable/disable menu elements.
        MenuItem = menu.Item
        Menu = menu.Menu
        Separator = menu.Separator

        menubar = self.menubar = wx.MenuBar()
        menu.extend(self, menubar, [
            Menu('&Session', [
                MenuItem('&New session window\tCtrl+N',
                         'Open a new session window',
                         self.OnNewWindow),
                MenuItem('&Python shell\tCtrl+P',
                         'Open a tab with a python shell',
                         self._NewCommandTab),
                Separator,
                MenuItem('&Open model\tCtrl+O',
                         'Load model or open new model from a MAD-X file.',
                         self._LoadFile),
                MenuItem('&Save model as..\tCtrl+S',
                         'Save the current model (beam + twiss) to a file',
                         self._SaveModel,
                         self._CanSaveModel),
                Separator,
                MenuItem('&Reset session',
                         'Clear the MAD-X session state.',
                         lambda _: self._ResetSession()),
                Separator,
                MenuItem('&Close',
                         'Close window',
                         self.OnQuit,
                         id=wx.ID_CLOSE),
            ]),
            Menu('&View', [
                MenuItem('&Envelope',
                         'Open new tab with beam envelopes.',
                         lambda _: TwissView.create(self.session,
                                                    self, basename='env')),
                MenuItem('&Position',
                         'Open new tab with beam position.',
                         lambda _: TwissView.create(self.session,
                                                    self, basename='pos')),
            ]),
            Menu('&Manage', [
                MenuItem('&Initial conditions',
                         'Add/remove/edit TWISS initial conditions.',
                         self._EditTwiss),
                MenuItem('&Beam',
                         'Set beam.',
                         self._SetBeam),
                Separator,
                MenuItem('Show &element indicators',
                         'Show indicators for beam line elements.',
                         self._ShowIndicators,
                         self._UpdateShowIndicators,
                         wx.ITEM_CHECK),
            ]),
            Menu('&Help', [
                MenuItem('&About',
                         'Show about dialog.',
                         lambda _: show_about_dialog(self)),
            ]),
        ])

        # Create menu items
        self.hook.menu(self, menubar)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateMenu, menubar)
        self._IsEnabledTop = {self.ViewMenuIndex: True,
                              self.TabMenuIndex: True}
        return menubar

    @property
    def ViewMenuIndex(self):
        return 1

    @property
    def TabMenuIndex(self):
        return 2

    def OnNewWindow(self, event):
        """Open a new frame."""
        self.__class__(self.app)

    def AddView(self, view, title):
        """Add new notebook tab for the view."""
        # TODO: remove this method in favor of a event based approach?
        child = MDIChildFrame(self, -1, title)
        panel = FigurePanel(child, view)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(panel, 1, wx.EXPAND)
        child.SetSizer(sizer)
        def OnPageClose(event):
            self.views.remove(view)
            event.Skip()
        child.Bind(wx.EVT_CLOSE, OnPageClose)
        ShowMDIChildFrame(child)

        view.plot()
        self.views.append(view)
        return panel

    def GetActivePanel(self):
        """Return the Panel which is currently active."""
        if self.GetActiveChild():
            return self.GetActiveChild().GetChildren()[0]

    def GetActiveFigurePanel(self):
        """Return the FigurePanel which is currently active or None."""
        panel = self.GetActivePanel()
        if isinstance(panel, FigurePanel):
            return panel
        return None

    def OnClose(self, event):
        # We want to terminate the remote session, otherwise _read_stream
        # may hang:
        try:
            self.session.close()
        except IOError:
            # The connection may already be terminated in case MAD-X crashed.
            pass
        CloseMDIChildren(self)
        event.Skip()

    def OnLogTabClose(self, event):
        """Prevent the command tab from closing, if other tabs are open."""
        if self.views:
            event.Veto()
        else:
            self.Close()

    def OnQuit(self, event):
        """Close the window."""
        self.Close()

    def OnUpdateMenu(self, event):
        if not self.session.madx:
            return
        enable_view = bool(self.session.madx.sequences)
        # we only want to call EnableTop() if the state is actually
        # different from before, since otherwise this will cause very
        # irritating flickering on windows. Because menubar.IsEnabledTop is
        # bugged on windows, we need to keep track ourself:
        # if enable != self.menubar.IsEnabledTop(idx):
        view_menu_index = self.ViewMenuIndex
        if enable_view != self._IsEnabledTop[view_menu_index]:
            self.menubar.EnableTop(view_menu_index, enable_view)
            self._IsEnabledTop[view_menu_index] = enable_view
        # Enable/Disable &Tab menu
        enable_tab = bool(self.GetActiveFigurePanel())
        tab_menu_index = self.TabMenuIndex
        if enable_tab != self._IsEnabledTop[tab_menu_index]:
            self.menubar.EnableTop(tab_menu_index, enable_tab)
            self._IsEnabledTop[tab_menu_index] = enable_tab
        event.Skip()

    def _NewCommandTab(self, event=None):
        """Open a new command tab."""
        child = MDIChildFrame(self, -1, "Command")
        crust = Shell(child, locals=self.env)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(crust, 1, wx.EXPAND)
        child.SetSizer(sizer)
        ShowMDIChildFrame(child)

    def _NewLogTab(self):
        child = MDIChildFrame(self, -1, "Log")
        # Create a tab for logging
        textctrl = wx.TextCtrl(child, wx.ID_ANY,
                               style=wx.TE_MULTILINE|wx.TE_READONLY)
        textctrl.SetFont(monospace(10))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(textctrl, 1, wx.EXPAND)
        child.SetSizer(sizer)
        child.Bind(wx.EVT_CLOSE, self.OnLogTabClose)
        ShowMDIChildFrame(child)
        self._log_ctrl = textctrl
        self._basicConfig(logging.INFO,
                          '%(asctime)s %(levelname)s %(name)s: %(message)s',
                          '%H:%M:%S')

    def _basicConfig(self, level, fmt, datefmt=None):
        """Configure logging."""
        stream = TextCtrlStream(self._log_ctrl)
        root = logging.RootLogger(level)
        manager = logging.Manager(root)
        formatter = logging.Formatter(fmt, datefmt)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        root.addHandler(handler)
        # store member variables:
        self._log_stream = stream
        self._log_manager = manager

    def getLogger(self, name='root'):
        return self._log_manager.getLogger(name)

    def _read_stream(self, stream):
        # The file iterator seems to be buffered:
        for line in iter(stream.readline, b''):
            try:
                self._log_stream.write(line)
            except:
                break


class TextCtrlStream(object):

    """
    Write to a text control.
    """

    def __init__(self, ctrl):
        """Set text control."""
        self._ctrl = ctrl

    def write(self, text):
        """Append text."""
        wx.CallAfter(self._ctrl.WriteText, text)
