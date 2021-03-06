#!/usr/bin/env python

#C: THIS FILE IS PART OF THE CYLC SUITE ENGINE.
#C: Copyright (C) 2008-2012 Hilary Oliver, NIWA
#C:
#C: This program is free software: you can redistribute it and/or modify
#C: it under the terms of the GNU General Public License as published by
#C: the Free Software Foundation, either version 3 of the License, or
#C: (at your option) any later version.
#C:
#C: This program is distributed in the hope that it will be useful,
#C: but WITHOUT ANY WARRANTY; without even the implied warranty of
#C: MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#C: GNU General Public License for more details.
#C:
#C: You should have received a copy of the GNU General Public License
#C: along with this program.  If not, see <http://www.gnu.org/licenses/>.

import gtk
#import pygtk
#pygtk.require('2.0')
import gobject
import pango
import os, re, sys
import subprocess
import helpwindow
from cylc.hostname import is_remote_host
from cylc.owner import is_remote_user
from combo_logviewer import combo_logviewer
from warning_dialog import warning_dialog, info_dialog
from cylc.gui.SuiteControlGraph import ControlGraph
from cylc.gui.SuiteControlLED import ControlLED
from cylc.gui.SuiteControlTree import ControlTree
from cylc.gui.graph import graph_suite_popup
from cylc.gui.stateview import DotGetter
from cylc.gui.util import get_icon, get_image_dir, get_logo
from cylc import cylc_pyro_client
from cylc.port_scan import SuiteIdentificationError
from cylc.state_summary import extract_group_state
from cylc.cycle_time import ct, CycleTimeError
from cylc.TaskID import TaskID, TaskIDError
from cylc.version import cylc_version
from option_group import controlled_option_group
from color_rotator import rotator
from cylc_logviewer import cylc_logviewer
from textload import textload
from datetime import datetime
from gcapture import gcapture_tmpfile

def run_get_stdout( command, filter=False ):
    try:
        popen = subprocess.Popen( command, shell=True,
                stderr=subprocess.PIPE, stdout=subprocess.PIPE )
        out = popen.stdout.read()
        err = popen.stderr.read()
        res = popen.wait()
        if res < 0:
            warning_dialog( "ERROR: command terminated by signal %d\n%s" % (res, err) ).warn()
            return (False, [])
        elif res > 0:
            warning_dialog("ERROR: command failed %d\n%s" % (res,err)).warn()
            return (False, [])
    except OSError, e:
        warning_dialog("ERROR: command invocation failed %s\n%s" % (str(e),err)).warn()
        return (False, [])
    else:
        # output is a single string with newlines; but we return a list of
        # lines filtered (optionally) for a special '!cylc!' prefix.
        res = []
        for line in out.split():
            line.strip()
            if filter:
                if line.startswith( '!cylc!' ):
                    res.append(line[6:])
            else:
                res.append(line)
        return ( True, res )
    return (False, [])


class InitData(object):
    """
Class to hold initialisation data.
    """
    def __init__( self, suite, pphrase, owner, host, port, cylc_tmpdir, pyro_timeout ):
        self.suite = suite
        self.pphrase = pphrase
        self.host = host
        self.port = port
        if pyro_timeout:
            self.pyro_timeout = float(pyro_timeout)
        else:
            self.pyro_timeout = None

        self.owner = owner
        self.cylc_tmpdir = cylc_tmpdir

        self.imagedir = get_image_dir()


class InfoBar(gtk.VBox):
    """
Class to create an information bar.
    """

    def __init__( self, suite, imagedir, 
                  status_changed_hook=lambda s: False ):
        super(InfoBar, self).__init__()

        self.dots = DotGetter( imagedir )
        self._suite_states = ["empty"]
        self.state_widget = gtk.HBox()
        self._set_tooltip( self.state_widget, "states" )  

        self._status = "status..."
        self.notify_status_changed = status_changed_hook
        self.status_widget = gtk.Label()
        self._set_tooltip( self.status_widget, "status" )

        self._mode = "mode..."
        self.mode_widget = gtk.Label()
        self._set_tooltip( self.mode_widget, "mode" )

        self._time = "time..."
        self.time_widget = gtk.Label()
        self._set_tooltip( self.time_widget, "last update time" )

        self._block = "access..."
        self.block_widget = gtk.image_new_from_stock( gtk.STOCK_DIALOG_QUESTION,
                                                      gtk.ICON_SIZE_SMALL_TOOLBAR )

        self.pack_start( gtk.HSeparator(), False, False )
        
        hbox = gtk.HBox()
        self.pack_start( hbox, False, True )

        eb = gtk.EventBox()
        eb.add( self.status_widget )
        #eb.modify_bg( gtk.STATE_NORMAL, gtk.gdk.color_parse( '#fff' ) )
        hbox.pack_start( eb, False )
        
        eb = gtk.EventBox()
        hbox.pack_start( eb, True )

        eb = gtk.EventBox()
        eb.add( self.state_widget )
        hbox.pack_start( eb, False )

        eb = gtk.EventBox()
        eb.add( self.mode_widget )
        #eb.modify_bg( gtk.STATE_NORMAL, gtk.gdk.color_parse( '#fff' ) )
        hbox.pack_start( eb, False )

        eb = gtk.EventBox()
        eb.add( self.time_widget )
        #eb.modify_bg( gtk.STATE_NORMAL, gtk.gdk.color_parse( '#fff' ) ) 
        hbox.pack_start( eb, False )

        eb = gtk.EventBox()
        eb.add( self.block_widget )
        hbox.pack_end( eb, False )

    def set_block( self, block ):
        """Set block or access icon."""
        if block == self._block:
            return False
        self._block = block
        if "unblocked" in block:
            self.block_widget.set_from_stock( gtk.STOCK_DIALOG_AUTHENTICATION,
                                              gtk.ICON_SIZE_SMALL_TOOLBAR )
        elif "blocked" in block:
            self.block_widget.set_from_stock( gtk.STOCK_DIALOG_ERROR,
                                              gtk.ICON_SIZE_SMALL_TOOLBAR )
        elif "waiting" in block:
            self.block_widget.set_from_stock( gtk.STOCK_DIALOG_QUESTION,
                                              gtk.ICON_SIZE_SMALL_TOOLBAR )
        self._set_tooltip( self.block_widget, "access: " + block )

    def set_mode(self, mode):
        """Set mode text."""
        if mode == self._mode:
            return False
        self._mode = mode
        self.mode_widget.set_markup( "  " + self._mode )

    def set_state(self, suite_states):
        """Set state text."""
        if suite_states == self._suite_states:
            return False
        self._suite_states = suite_states
        state_info = {}
        for state in self._suite_states:
            state_info.setdefault(state, 0)
            state_info[state] += 1
        for child in self.state_widget.get_children():
            self.state_widget.remove(child)
        items = state_info.items()
        items.sort()
        items.sort( lambda x, y: cmp(y[1], x[1]) )
        for state, num in items:
            icon = self.dots.get_image( state )
            icon.show()
            self.state_widget.pack_start( icon, False, False )
            self._set_tooltip( icon, str(num) + " " + state )

    def set_status(self, status):
        """Set status text."""
        if status == self._status:
            return False
        self._status = status
        self.status_widget.set_text( " " + self._status )
        self.notify_status_changed( self._status )

    def set_stop_summary(self, summary_maps):
        """Set various summary info."""
        # new string format() introduced in Python 2.6
        #o>summary = "stopped with '{0}'"
        summary = "stopped with '%s'"
        glob, task, fam = summary_maps
        states = [t["state"] for t in task.values() if "state" in t]
        suite_state = "?"
        if states:
            suite_state = extract_group_state(states)
        #o>summary = summary.format(suite_state)
        summary = summary % suite_state
        num_failed = 0
        for task_id in task:
            if task[task_id].get("state") == "failed":
                num_failed += 1
        if num_failed:
            #o> summary += ": {0} failed tasks".format(num_failed)
            summary += ": %s failed tasks" % num_failed
        self.set_status(summary)
        self.set_time(glob["last_updated"].strftime("%Y/%m/%d %H:%M:%S"))

    def set_time(self, time):
        """Set last update text."""
        if time == self._time:
            return False
        self._time = time
        self.time_widget.set_text( " " + self._time + " " )

    def _set_tooltip(self, widget, text):
        tooltip = gtk.Tooltips()
        tooltip.enable()
        tooltip.set_tip(widget, text)


class ControlApp(object):
    """
Main Control GUI that displays one or more views or interfaces to the suite.
    """

    DEFAULT_VIEW = "graph"
    VIEWS_ORDERED = [ "graph", "dot", "text" ]
    VIEWS = { "graph": ControlGraph,
              "dot": ControlLED,
              "text": ControlTree }
    VIEW_DESC = { "graph": "Dependency graph view",
                  "dot": "Dot summary view",
                  "text": "Detailed list view" }
    VIEW_ICON_PATHS = { "graph": "/icons/tab-graph.xpm",
                        "dot": "/icons/tab-led.xpm",
                        "text": "/icons/tab-tree.xpm" }
                       

    def __init__( self, suite, pphrase, owner, host, port, cylc_tmpdir,
            startup_views, pyro_timeout ):

        gobject.threads_init()
        
        self.cfg = InitData( suite, pphrase, owner, host, port, cylc_tmpdir, pyro_timeout )

        self.setup_icons()

        self.view_layout_horizontal = False

        #self.connection_lost = False # (not used)
        self.quitters = []
        self.gcapture_windows = []

        self.log_colors = rotator()

        self.window = gtk.Window(gtk.WINDOW_TOPLEVEL)
        self.window.set_title( self.cfg.suite + " - gcylc" )
        self.window.set_icon(get_icon())
        self.window.modify_bg( gtk.STATE_NORMAL, gtk.gdk.color_parse( "#ddd" ))
        self.window.set_size_request(800, 500)
        self.window.connect("delete_event", self.delete_event)

        self._prev_status = None
        self.create_main_menu()

        bigbox = gtk.VBox()
        bigbox.pack_start( self.menu_bar, False )

        self.create_tool_bar()
        bigbox.pack_start( self.tool_bar_box, False, False )
        self.create_info_bar()

        self.views_parent = gtk.VBox()
        bigbox.pack_start( self.views_parent, True )
        self.setup_views(startup_views)

        hbox = gtk.HBox()
        hbox.pack_start( self.info_bar, True )
        bigbox.pack_start( hbox, False )

        self.window.add( bigbox )
        self.window.show_all()

    def setup_icons( self ):
        """Set up some extra stock icons for better PyGTK compatibility."""
        # create a new stock icon for the 'group' action
        root_img_dir = os.environ[ 'CYLC_DIR' ] + '/images/icons'
        pixbuf = gtk.gdk.pixbuf_new_from_file( root_img_dir + '/group.png' )
        grp_iconset = gtk.IconSet(pixbuf)
        pixbuf = gtk.gdk.pixbuf_new_from_file( root_img_dir + '/ungroup.png' )
        ungrp_iconset = gtk.IconSet(pixbuf)
        factory = gtk.IconFactory()
        factory.add( 'group', grp_iconset )
        factory.add( 'ungroup', ungrp_iconset )
        factory.add_default()

    def setup_views( self, startup_views=[] ):
        """Create our view containers and the startup views."""
        num_views = 2
        if not startup_views:
            startup_views = [ self.DEFAULT_VIEW ]
        self.view_containers = []
        self.current_views = []
        self.current_view_toolitems = []
        for i in range(num_views):
            self.view_containers.append(gtk.HBox())
            self.current_views.append(None)
            self.current_view_toolitems.append([])
        self.views_parent.pack_start( self.view_containers[0],
                                      expand=True, fill=True )
        for i, view in enumerate(startup_views):
            self.create_view(view, i)
            if i == 0:
                self._set_menu_view0( view )
                self._set_tool_bar_view0 ( view )
            elif i == 1:
                self._set_menu_view1( view )
                self._set_tool_bar_view1( view )

    def change_view_layout( self, horizontal=False ):
        """Switch between horizontal or vertical positioning of views."""
        self.view_layout_horizontal = horizontal
        old_pane = self.view_containers[0].get_parent()
        if not isinstance(old_pane, gtk.Paned):
            return False
        old_pane.remove( self.view_containers[0] )
        old_pane.remove( self.view_containers[1] )
        top_parent = old_pane.get_parent()
        top_parent.remove( old_pane )
        if self.view_layout_horizontal:
           new_pane = gtk.HPaned()
           extent = top_parent.get_allocation().width
        else:
           new_pane = gtk.VPaned()
           extent = top_parent.get_allocation().height
        new_pane.pack1( self.view_containers[0], resize=True, shrink=True )
        new_pane.pack2( self.view_containers[1], resize=True, shrink=True )
        new_pane.set_position( extent / 2 )
        top_parent.pack_start( new_pane, expand=True, fill=True )
        self.window.show_all()

    def _cb_change_view0_menu( self, item ):
        # This is the view menu callback for the primary view.
        if not item.get_active():
            return False
        if self.current_views[0].name == item._viewname:
            return False
        self.switch_view( item._viewname )
        self._set_tool_bar_view0( item._viewname )
        return False

    def _set_tool_bar_view0( self, viewname ):
        # Set the tool bar state for the primary view.
        model = self.tool_bar_view0.get_model()
        c_iter = model.get_iter_first()
        while c_iter is not None:
            if model.get_value(c_iter, 1) == viewname:
                index = model.get_path( c_iter )[0]
                self.tool_bar_view0.set_active( index )
                break
            c_iter = model.iter_next( c_iter )

    def _cb_change_view0_tool( self, widget ):
        # This is the tool bar callback for the primary view.
        viewname = widget.get_model().get_value(widget.get_active_iter(), 1)
        if self.current_views[0].name == viewname:
            return False
        self.switch_view( viewname)
        self._set_menu_view0( viewname )
        return False

    def _set_menu_view0( self, viewname ):
        # Set the view menu state for the primary view.
        for view_item in self.view_menu_views0:
            if ( view_item._viewname == viewname and
                 not view_item.get_active() ):
                return view_item.set_active( True )

    def _cb_change_view1_menu( self, item ):
        # This is the view menu callback for the secondary view.
        if not item.get_active():
            return False
        if self.current_views[1] is None:
            if item._viewname not in self.VIEWS:
                return False
        elif self.current_views[1].name == item._viewname:
            return False
        self.switch_view( item._viewname, view_num=1 )
        self._set_tool_bar_view1( item._viewname )
        return False

    def _set_tool_bar_view1( self, viewname ):
        # Set the tool bar state for the secondary view.
        model = self.tool_bar_view1.get_model()
        c_iter = model.get_iter_first()
        while c_iter is not None:
            if model.get_value(c_iter, 1) == viewname:
                index = model.get_path( c_iter )[0]
                self.tool_bar_view1.set_active( index )
                break
            c_iter = model.iter_next( c_iter )
        else:
            self.tool_bar_view1.set_active( 0 )

    def _cb_change_view1_tool( self, widget ):
        # This is the tool bar callback for the secondary view.
        viewname = widget.get_model().get_value(widget.get_active_iter(), 1)
        if self.current_views[1] is None:
            if viewname not in self.VIEWS:
                return False
        elif self.current_views[1].name == viewname:
            return False
        self.switch_view( viewname, view_num=1 )
        self._set_menu_view1( viewname )
        return False

    def _set_menu_view1( self, viewname ):
        # Set the view menu state for the secondary view.
        for view_item in self.view_menu_views1:
            if ( view_item._viewname == viewname and
                 not view_item.get_active() ):
                return view_item.set_active( True )
            if ( view_item._viewname not in self.VIEWS and
                 viewname not in self.VIEWS and
                 not view_item.get_active() ):
                return view_item.set_active( True )
        return False

    def _cb_change_view_align( self, widget ):
        # This is the view menu callback to toggle side-by-side layout.
        horizontal = widget.get_active()
        if self.view_layout_horizontal == horizontal:
            return False
        self.change_view_layout( widget.get_active() )
        if widget == self.layout_toolbutton:
            self.view1_align_item.set_active( horizontal )
        else:
            self.layout_toolbutton.set_active( horizontal )

    def switch_view( self, new_viewname, view_num=0 ):
        """Remove a view instance and replace with a different one."""
        if new_viewname not in self.VIEWS:
            self.remove_view( view_num )
            return False
        old_position = -1
        if self.current_views[view_num] is not None:
            if self.current_views[view_num].name == new_viewname:
                return False
            if view_num == 1:
                old_position = self.views_parent.get_children()[0].get_position()
            self.remove_view( view_num )
        self.create_view( new_viewname, view_num, pane_position=old_position )
        return False

    def create_view( self, viewname=None, view_num=0, pane_position=-1 ):
        """Create a view instance.
        
        Toolbars and menus must be updated, as well as pane positioning.
        
        """
        if viewname is None:
            viewname = self.DEFAULT_VIEW
        container = self.view_containers[view_num]
        self.current_views[view_num] = self.VIEWS[viewname]( 
                                                   self.cfg,
                                                   self.info_bar,
                                                   self.get_right_click_menu,
                                                   self.log_colors )
        view = self.current_views[view_num]
        view.name = viewname
        if view_num == 1:
            # Secondary view creation
            viewbox0 = self.view_containers[0]
            zero_parent = viewbox0.get_parent()
            zero_parent.remove( viewbox0 )
            if self.view_layout_horizontal:
                pane = gtk.HPaned()
                extent = zero_parent.get_allocation().width
            else:
                pane = gtk.VPaned()
                extent = zero_parent.get_allocation().height
            pane.pack1( viewbox0, resize=True, shrink=True )
            pane.pack2( container, resize=True, shrink=True )
            # Handle pane positioning
            if pane_position == -1:
                pane_position =  extent / 2
            pane.set_position( pane_position )
            zero_parent.pack_start(pane, expand=True, fill=True)  
        view_widgets = view.get_control_widgets()
        if view_widgets.size_request() == (0, 0):
            view_widgets.set_size_request(1, 1)      
        container.pack_start( view_widgets,
                              expand=True, fill=True )
        # Handle menu
        menu = self.views_option_menus[view_num]
        for item in menu.get_children():
            menu.remove( item )
        new_menuitems = view.get_menuitems()
        for item in new_menuitems:
            menu.append( item )
        self.views_option_menuitems[view_num].set_sensitive( True )
        # Handle toolbar
        for view_toolitems in self.current_view_toolitems[view_num]:
            for item in view_toolitems:
                self.tool_bars[view_num].remove( item )
        new_toolitems = view.get_toolitems()
        if new_toolitems:
            index = self.tool_bars[view_num].get_children().index(
                              self.view_toolitems[view_num])
        for toolitem in reversed(new_toolitems):
            self.tool_bars[view_num].insert( toolitem, index + 1)
        self.current_view_toolitems[view_num] = new_toolitems
        self.window.show_all()

    def remove_view( self, view_num ):
        """Remove a view instance."""
        self.current_views[view_num].stop()
        self.current_views[view_num] = None
        menu = self.views_option_menus[view_num]
        for item in menu.get_children():
            menu.remove( item )
        self.views_option_menuitems[view_num].set_sensitive( False )
        while len(self.current_view_toolitems[view_num]):
            self.tool_bars[view_num].remove(
                      self.current_view_toolitems[view_num].pop() )
        if view_num == 1:
            parent = self.view_containers[0].get_parent()
            parent.remove( self.view_containers[0] )
            parent.remove( self.view_containers[1] )
            top_parent = parent.get_parent()
            top_parent.remove( parent )
            top_parent.pack_start( self.view_containers[0],
                                   expand=True, fill=True )
        for child in self.view_containers[view_num].get_children():
            child.destroy()

    def quit_gcapture( self ):
        for gwindow in self.gcapture_windows:
            if not gwindow.quit_already:
                gwindow.quit( None, None )

    def quit( self ):
        self.quit_gcapture()
        for q in self.quitters:
            q.quit()
        for view in self.current_views:
            if view is not None:
                view.stop()
        gtk.main_quit()

    def delete_event(self, widget, event, data=None):
        self.quit()

    def click_exit( self, foo ):
        self.quit()

    def pause_suite( self, bt ):
        try:
            god = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
            result = god.hold()
        except SuiteIdentificationError, x:
            warning_dialog( x.__str__(), self.window ).warn()
        else:
            if not result.success:
                warning_dialog( result.reason, self.window ).warn()
            #else:
            #    info_dialog( result.reason, self.window ).inform()

    def resume_suite( self, bt ):
        try:
            god = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            warning_dialog( x.__str__(), self.window ).warn()
            return
        result = god.resume()
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def stopsuite_default( self, *args ):
        """Try to stop the suite (after currently running tasks...)."""
        try:
            god = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
            result = god.shutdown()
        except SuiteIdentificationError, x:
            warning_dialog( x.__str__(), self.window ).warn()
        else:
            if not result.success:
                warning_dialog( result.reason, self.window ).warn()
            #else:
            #    info_dialog( result.reason, self.window ).inform()

    def stopsuite( self, bt, window,
            stop_rb, stopat_rb, stopct_rb, stoptt_rb, stopnow_rb,
            stoptag_entry, stopclock_entry, stoptask_entry ):
        stop = False
        stopat = False
        stopnow = False
        stopclock = False
        stoptask = False

        if stop_rb.get_active():
            stop = True

        elif stopat_rb.get_active():
            stopat = True
            stoptag = stoptag_entry.get_text()
            if stoptag == '':
                warning_dialog( "ERROR: No stop TAG entered", self.window ).warn()
                return
            try:
                ct(stoptag)
            except CycleTimeError,x:
                try:
                    int(stoptag)
                except ValueError:
                    warning_dialog( 'ERROR, Invalid stop tag: ' + stoptag, self.window ).warn()
                    return

        elif stopnow_rb.get_active():
            stopnow = True

        elif stopct_rb.get_active():
            stopclock = True
            stopclock_time = stopclock_entry.get_text()
            if stopclock_time == '':
                warning_dialog( "ERROR: No stop time entered", self.window ).warn()
                return
            try:
                # YYYY/MM/DD-HH:mm
                date, time = stopclock_time.split('-')
                yyyy, mm, dd = date.split('/')
                HH,MM = time.split(':')
                stop_dtime = datetime( int(yyyy), int(mm), int(dd), int(HH), int(MM) )
            except:
                warning_dialog( "ERROR: Bad datetime (YYYY/MM/DD-HH:mm): " + stopclock_time,
                                self.window ).warn()
                return

        elif stoptt_rb.get_active():
            stoptask = True
            stoptask_id = stoptask_entry.get_text()
            if stoptask_id == '':
                warning_dialog( "ERROR: No stop task ID entered", self.window ).warn()
                return
            try:
                tid = TaskID( stoptask_id )
            except TaskIDError,x:
                warning_dialog( "ERROR: Bad task ID (TASK%YYYYMMDDHH): " + stoptask_id,
                                self.window ).warn()
                return
            else:
                stoptask_id = tid.getstr()
        else:
            # SHOULD NOT BE REACHED
            warning_dialog( "ERROR: Bug in GUI?", self.window ).warn()
            return

        window.destroy()

        try:
            god = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
            if stop:
                result = god.shutdown()
            elif stopat:
                result = god.set_stop( stoptag, 'stop after TAG' )
            elif stopnow:
                result = god.shutdown_now()
            elif stopclock:
                result = god.set_stop( stopclock_time, 'stop after clock time' )
            elif stoptask:
                result = god.set_stop( stoptask_id, 'stop after task' )
        except SuiteIdentificationError, x:
            warning_dialog( x.__str__(), self.window ).warn()
        else:
            if not result.success:
                warning_dialog( result.reason, self.window ).warn()
            #else:
            #    info_dialog( result.reason, self.window ).inform()

    def loadctimes( self, bt, startentry, stopentry ):
        command = "cylc get-config --mark-output --host=" + \
                self.cfg.host + " --owner=" + self.cfg.owner + " " + \
                self.cfg.suite 
        item1 = " scheduling 'initial cycle time'"
        item2 = " scheduling 'final cycle time'"
        res1 = run_get_stdout( command + item1, filter=True ) # (T/F,[lines])
        res2 = run_get_stdout( command + item2, filter=True )

        if res1[0] and res2[0]:
            out1 = res1[1][0]
            out2 = res2[1][0]
            if out1 == "None" and out2 == "None":  # (default value from suite.rc spec)
                info_dialog( """Initial and final cycle times have not
been defined for this suite""").inform()
            elif out1 == "None":
                info_dialog( """An initial cycle time has not
been defined for this suite""").inform()
                stopentry.set_text( out2 )
            elif out2 == "None":
                info_dialog( """A final cycle time has not
been defined for this suite""").inform()
                startentry.set_text( out1 )
            else:
                startentry.set_text( out1 )
                stopentry.set_text( out2 )
        else: 
            pass # error dialogs done by run_get_stdout()

    def startsuite( self, bt, window, 
            coldstart_rb, warmstart_rb, rawstart_rb, restart_rb,
            entry_ctime, stoptime_entry, no_reset_cb, statedump_entry,
            optgroups, mode_live_rb, mode_sim_rb, mode_dum_rb, hold_cb,
            holdtime_entry ):

        command = 'cylc control run --gcylc'
        options = ''
        method = ''
        if coldstart_rb.get_active():
            method = 'coldstart'
        elif warmstart_rb.get_active():
            method = 'warmstart'
            options += ' -w'
        elif rawstart_rb.get_active():
            method = 'rawstart'
            options += ' -r'
        elif restart_rb.get_active():
            method = 'restart'
            command = 'cylc control restart --gcylc'
            if no_reset_cb.get_active():
                options += ' --no-reset'

        if mode_live_rb.get_active():
            pass
        elif mode_sim_rb.get_active():
            command += ' --mode=simulation'
        elif mode_dum_rb.get_active():
            command += ' --mode=dummy'

        if self.cfg.pyro_timeout:
            command += ' --timeout=' + str(self.cfg.pyro_timeout)

        ctime = ''
        if method != 'restart':
            # start time
            ctime = entry_ctime.get_text()
            if ctime != '':
                try:
                    ct(ctime)
                except CycleTimeError,x:
                    warning_dialog( str(x), self.window ).warn()
                    return

        ste = stoptime_entry.get_text()
        if ste:
            try:
                ct(ste)
            except CycleTimeError,x:
                warning_dialog( str(x), self.window ).warn()
                return
            options += ' --until=' + ste
 
        hetxt = holdtime_entry.get_text()
        if hold_cb.get_active():
            options += ' --hold'
        elif hetxt != '':
            options += ' --hold-after=' + hetxt

        for group in optgroups:
            options += group.get_options()
        window.destroy()

        options += ' --owner=' + self.cfg.owner + ' --host=' + self.cfg.host

        command += ' ' + options + ' ' + self.cfg.suite + ' ' + ctime

        print command 

        if method == 'restart':
            if statedump_entry.get_text():
                command += ' ' + statedump_entry.get_text()

        # DEBUGGING:
        #info_dialog( "I'm about to run this command: \n" + command,
        #             self.window ).inform()
        #return

        try:
            subprocess.Popen( [command], shell=True )
        except OSError, e:
            warning_dialog( 'Error: failed to start ' + self.cfg.suite,
                    self.window ).warn()
            success = False

    def unblock_suite( self, bt ):
        try:
            god = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
            god.unblock()
        except SuiteIdentificationError, x:
            warning_dialog( 'ERROR: ' + str(x), self.window ).warn()

    def block_suite( self, bt ):
        try:
            god = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
            god.block()
        except SuiteIdentificationError, x:
            warning_dialog( 'ERROR: ' + str(x), self.window ).warn()

    def about( self, bt ):
        about = gtk.AboutDialog()
        if gtk.gtk_version[0] ==2:
            if gtk.gtk_version[1] >= 12:
                # set_program_name() was added in PyGTK 2.12
                about.set_program_name( "cylc" )
        about.set_version( cylc_version )
        about.set_copyright( "Copyright (C) 2008-2012 Hilary Oliver, NIWA" )

        about.set_comments( 
"""
The Cylc Suite Engine.
""" )
        #about.set_website( "http://www.niwa.co.nz" )
        about.set_logo( get_logo() )
        about.set_transient_for( self.window )
        about.run()
        about.destroy()

    def view_task_descr( self, w, task_id ):
        command = "cylc show --host=" + self.cfg.host + " --owner=" + \
                self.cfg.owner + " " + self.cfg.suite + " " + task_id
        foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir, 600, 400 )
        self.gcapture_windows.append(foo)
        foo.run()

    def view_task_info( self, w, task_id, jsonly ):
        try:
            [ glbl, states, fam_states ] = self.get_pyro( 'state_summary').get_state_summary()
        except SuiteIdentificationError, x:
            warning_dialog( str(x), self.window ).warn()
            return
        view = True
        reasons = []
        try:
            logfiles = states[ task_id ][ 'logfiles' ]
        except KeyError:
            warning_dialog( task_id + ' is no longer live', self.window ).warn()
            return False

        if len(logfiles) == 0:
            view = False
            reasons.append( task_id + ' has no associated log files' )

        if states[ task_id ][ 'state' ] == 'waiting' or states[ task_id ][ 'state' ] == 'queued':
            view = False
            reasons.append( task_id + ' has not started running yet' )

        if not view:
            warning_dialog( '\n'.join( reasons ), self.window ).warn()
        else:
            self.popup_logview( task_id, logfiles, jsonly )

        return False

    def get_right_click_menu( self, task_id, hide_task=False ):
        """Return the default menu for a task."""
        menu = gtk.Menu()
        if not hide_task:
            menu_root = gtk.MenuItem( task_id )
            menu_root.set_submenu( menu )

            title_item = gtk.MenuItem( 'Task: ' + task_id.replace( "_", "__" ) )
            title_item.set_sensitive(False)
            menu.append( title_item )
            menu.append( gtk.SeparatorMenuItem() )

        menu_items = self._get_right_click_menu_items( task_id )
        for item in menu_items:
            menu.append( item )

        menu.show_all()
        return menu


    def _get_right_click_menu_items( self, task_id ):
        # Return the default menu items for a task
        name, ctime = task_id.split('%')

        items = []

        js0_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_DIALOG_INFO )
        js0_item.set_label( 'View Task Info' )
        items.append( js0_item )
        js0_item.connect( 'activate', self.view_task_descr, task_id )

        js_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_DND )
        js_item.set_label( 'View Job Script' )
        items.append( js_item )
        js_item.connect( 'activate', self.view_task_info, task_id, True )

        info_item = gtk.MenuItem( 'View Task Output' )
        items.append( info_item )
        info_item.connect( 'activate', self.view_task_info, task_id, False )

        info_item = gtk.MenuItem( 'View Task State' )
        items.append( info_item )
        info_item.connect( 'activate', self.popup_requisites, task_id )

        items.append( gtk.SeparatorMenuItem() )

        trigger_now_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_MEDIA_PLAY )
        trigger_now_item.set_label( 'Trigger' )
        items.append( trigger_now_item )
        trigger_now_item.connect( 'activate', self.trigger_task_now, task_id )

        reset_menu = gtk.Menu()
        reset_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_CONVERT )
        reset_item.set_label( "Reset task state" )
        reset_item.set_submenu( reset_menu )
        items.append( reset_item )
        
        reset_ready_item = gtk.MenuItem('Reset to "ready"' )
        reset_menu.append( reset_ready_item )
        reset_ready_item.connect( 'activate', self.reset_task_state, task_id, 'ready' )

        reset_waiting_item = gtk.MenuItem( 'Reset to "waiting"' )
        reset_menu.append( reset_waiting_item )
        reset_waiting_item.connect( 'activate', self.reset_task_state, task_id, 'waiting' )

        reset_succeeded_item = gtk.MenuItem( 'Reset to "succeeded"' )
        reset_menu.append( reset_succeeded_item )
        reset_succeeded_item.connect( 'activate', self.reset_task_state, task_id, 'succeeded' )

        reset_failed_item = gtk.MenuItem( 'Reset to "failed"' )
        reset_menu.append( reset_failed_item )
        reset_failed_item.connect( 'activate', self.reset_task_state, task_id, 'failed' )

        spawn_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_ADD )
        spawn_item.set_label( 'Force spawn' )
        items.append( spawn_item )
        spawn_item.connect( 'activate', self.reset_task_state, task_id, 'spawn' )

        items.append( gtk.SeparatorMenuItem() )

        stoptask_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_MEDIA_PAUSE )
        stoptask_item.set_label( 'Hold' )
        items.append( stoptask_item )
        stoptask_item.connect( 'activate', self.hold_task, task_id, True )

        unstoptask_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_MEDIA_PLAY )
        unstoptask_item.set_label( 'Release' )
        items.append( unstoptask_item )
        unstoptask_item.connect( 'activate', self.hold_task, task_id, False )

        items.append( gtk.SeparatorMenuItem() )
    
        kill_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_CLEAR )
        kill_item.set_label( 'Remove after spawning' )
        items.append( kill_item )
        kill_item.connect( 'activate', self.kill_task, task_id )

        kill_nospawn_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_CLEAR )
        kill_nospawn_item.set_label( 'Remove without spawning' )
        items.append( kill_nospawn_item )
        kill_nospawn_item.connect( 'activate', self.kill_task_nospawn, task_id )

        purge_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_DELETE )
        purge_item.set_label( 'Remove Tree (Recursive Purge)' )
        items.append( purge_item )
        purge_item.connect( 'activate', self.popup_purge, task_id )

        items.append( gtk.SeparatorMenuItem() )
    
        addprereq_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_ADD )
        addprereq_item.set_label( 'Add A Prerequisite' )
        items.append( addprereq_item )
        addprereq_item.connect( 'activate', self.add_prerequisite_popup, task_id )

        return items

    def change_runahead_popup( self, b ):
        window = gtk.Window()
        window.modify_bg( gtk.STATE_NORMAL, 
                gtk.gdk.color_parse( self.log_colors.get_color()))
        window.set_border_width(5)
        window.set_title( "Change Suite Runahead Limit" )
        window.set_transient_for( self.window )
        window.set_type_hint( gtk.gdk.WINDOW_TYPE_HINT_DIALOG )
        #window.set_size_request(800, 300)

        sw = gtk.ScrolledWindow()
        sw.set_policy( gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC )

        vbox = gtk.VBox()

        label = gtk.Label( 'SUITE: ' + self.cfg.suite )
        vbox.pack_start( label, True )
 
        entry = gtk.Entry()
        #entry.connect( "activate", self.change_runahead_entry, window, task_id )

        hbox = gtk.HBox()
        label = gtk.Label( 'HOURS' )
        hbox.pack_start( label, True )
        hbox.pack_start (entry, True)
        vbox.pack_start( hbox )

        cancel_button = gtk.Button( "_Cancel" )
        cancel_button.connect("clicked", lambda x: window.destroy() )

        start_button = gtk.Button( "_Change" )
        start_button.connect("clicked", self.change_runahead, entry, window )

        help_button = gtk.Button( "_Help" )
        help_button.connect("clicked", self.command_help, "control", "set-runahead" )

        hbox = gtk.HBox()
        hbox.pack_start( cancel_button, True )
        hbox.pack_start( start_button, True)
        hbox.pack_start( help_button, True)
        vbox.pack_start( hbox )

        window.add( vbox )
        window.show_all()

    def change_runahead( self, w, entry, window ):
        ent = entry.get_text()
        if ent == '':
            limit = None
        else:
            try:
                int( ent )
            except ValueError:
                warning_dialog( 'Hours value must be integer!', self.window ).warn()
                return
            else:
                limit = ent
        window.destroy()
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            warning_dialog( x.__str__(), self.window ).warn()
            return
        result = proxy.set_runahead( limit )
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def add_prerequisite_popup( self, b, task_id ):
        window = gtk.Window()
        window.modify_bg( gtk.STATE_NORMAL, 
                gtk.gdk.color_parse( self.log_colors.get_color()))
        window.set_border_width(5)
        window.set_title( "Add A Prequisite" )
        window.set_transient_for( self.window )
        window.set_type_hint( gtk.gdk.WINDOW_TYPE_HINT_DIALOG )
        #window.set_size_request(800, 300)

        sw = gtk.ScrolledWindow()
        sw.set_policy( gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC )

        vbox = gtk.VBox()

        label = gtk.Label( 'SUITE: ' + self.cfg.suite )
        vbox.pack_start( label, True )

        label = gtk.Label( 'TASK: ' + task_id )
        vbox.pack_start( label, True )
         
        label = gtk.Label( 'DEP (NAME%TAG or message)' )

        entry = gtk.Entry()

        hbox = gtk.HBox()
        hbox.pack_start( label, True )
        hbox.pack_start (entry, True)
        vbox.pack_start( hbox )

        cancel_button = gtk.Button( "_Cancel" )
        cancel_button.connect("clicked", lambda x: window.destroy() )

        start_button = gtk.Button( "_Add" )
        start_button.connect("clicked", self.add_prerequisite, entry, window, task_id )

        help_button = gtk.Button( "_Help" )
        help_button.connect("clicked", self.command_help, "control", "depend" )

        hbox = gtk.HBox()
        hbox.pack_start( start_button, True)
        hbox.pack_start( help_button, True )
        hbox.pack_start( cancel_button, True )
        vbox.pack_start( hbox )

        window.add( vbox )
        window.show_all()

    def add_prerequisite( self, w, entry, window, task_id ):
        dep = entry.get_text()
        m = re.match( '^(\w+)%(\w+)$', dep )
        if m:
            #name, ctime = m.groups()
            msg = dep + ' succeeded'
        else:
            msg = dep

        try:
            (name, cycle ) = task_id.split('%')
        except ValueError:
            warning_dialog( "ERROR, Task or Group ID must be NAME%YYYYMMDDHH",
                            self.window ).warn()
            return
        try:
            ct(cycle)
        except CycleTimeError,x:
            warning_dialog( str(x), self.window ).warn()
            return

        window.destroy()
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            warning_dialog( x.__str__(), self.window ).warn()
            return
        result = proxy.add_prerequisite( task_id, msg )
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def update_tb( self, tb, line, tags = None ):
        if tags:
            tb.insert_with_tags( tb.get_end_iter(), line, *tags )
        else:
            tb.insert( tb.get_end_iter(), line )

    def popup_requisites( self, w, task_id ):
        try:
            result = self.get_pyro( 'remote' ).get_task_requisites( [ task_id ] )
        except SuiteIdentificationError,x:
            warning_dialog(str(x), self.window).warn()
            return

        if result:
            # (else no tasks were found at all -suite shutting down)
            if task_id not in result:
                warning_dialog( 
                    "Task proxy " + task_id + " not found in " + self.cfg.suite + \
                 ".\nTasks are removed once they are no longer needed.",
                 self.window ).warn()
                return

        window = gtk.Window()
        window.set_title( task_id + " State" )
        #window.modify_bg( gtk.STATE_NORMAL, 
        #       gtk.gdk.color_parse( self.log_colors.get_color()))
        window.set_size_request(600, 400)
        window.set_transient_for( self.window )
        window.set_type_hint( gtk.gdk.WINDOW_TYPE_HINT_DIALOG )

        sw = gtk.ScrolledWindow()
        sw.set_policy( gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC )

        vbox = gtk.VBox()
        quit_button = gtk.Button( "_Close" )
        quit_button.connect("clicked", lambda x: window.destroy() )
        vbox.pack_start( sw )
        vbox.pack_start( quit_button, False )

        textview = gtk.TextView()
        textview.set_border_width(5)
        textview.modify_bg( gtk.STATE_NORMAL, gtk.gdk.color_parse( "#fff" ))
        textview.set_editable( False )
        sw.add( textview )
        window.add( vbox )
        tb = textview.get_buffer()

        blue = tb.create_tag( None, foreground = "blue" )
        red = tb.create_tag( None, foreground = "red" )
        bold = tb.create_tag( None, weight = pango.WEIGHT_BOLD )
        
        self.update_tb( tb, 'TASK ', [bold] )
        self.update_tb( tb, task_id, [bold, blue])
        self.update_tb( tb, ' in SUITE ', [bold] )
        self.update_tb( tb, self.cfg.suite + '\n\n', [bold, blue])

        [ pre, out, extra_info ] = result[ task_id ]

        self.update_tb( tb, 'Prerequisites', [bold])
        #self.update_tb( tb, ' blue => satisfied,', [blue] )
        self.update_tb( tb, ' (' )
        self.update_tb( tb, 'red', [red] )
        self.update_tb( tb, '=> NOT satisfied)\n') 

        if len( pre ) == 0:
            self.update_tb( tb, ' - (None)\n' )
        for item in pre:
            [ msg, state ] = item
            if state:
                tags = None
            else:
                tags = [red]
            self.update_tb( tb, ' - ' + msg + '\n', tags )

        self.update_tb( tb, '\nOutputs', [bold] )
        self.update_tb( tb, ' (' )
        self.update_tb( tb, 'red', [red] )
        self.update_tb( tb, '=> NOT completed)\n') 

        if len( out ) == 0:
            self.update_tb( tb, ' - (None)\n')
        for item in out:
            [ msg, state ] = item
            if state:
                tags = []
            else:
                tags = [red]
            self.update_tb( tb, ' - ' + msg + '\n', tags )

        if len( extra_info.keys() ) > 0:
            self.update_tb( tb, '\nOther\n', [bold] )
            for item in extra_info:
                self.update_tb( tb, ' - ' + item + ': ' + str( extra_info[ item ] ) + '\n' )

        self.update_tb( tb, '\nNOTE: ', [bold] )
        self.update_tb( tb, ''' for tasks that have triggered already, prerequisites are 
shown here in the state they were in at the time of triggering.''' )

        #window.connect("delete_event", lv.quit_w_e)
        window.show_all()

    def on_popup_quit( self, b, lv, w ):
        lv.quit()
        self.quitters.remove( lv )
        w.destroy()

    def hold_task( self, b, task_id, stop=True ):
        if stop:
            msg = "hold " + task_id + "?"
        else:
            msg = "release " + task_id + "?"

        prompt = gtk.MessageDialog( self.window, gtk.DIALOG_MODAL,
                                    gtk.MESSAGE_QUESTION,
                                    gtk.BUTTONS_OK_CANCEL, msg )

        prompt.add_button( gtk.STOCK_HELP, gtk.RESPONSE_HELP )
        response = prompt.run()

        while response == gtk.RESPONSE_HELP:
            if stop:
                self.command_help( "control", "hold" )
            else:
                self.command_help( "control", "release" )
            response = prompt.run()

        prompt.destroy()
        if response != gtk.RESPONSE_OK:
            return
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            # the suite was probably shut down by another process
            warning_dialog( x.__str__(), self.window ).warn()
            return
        if stop:
            result = proxy.hold_task( task_id )
        else:
            result = proxy.release_task( task_id )

        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def trigger_task_now( self, b, task_id ):
        msg = "trigger " + task_id + " now?"
        prompt = gtk.MessageDialog( self.window, gtk.DIALOG_MODAL,
                                    gtk.MESSAGE_QUESTION, gtk.BUTTONS_OK_CANCEL, msg )

        prompt.add_button( gtk.STOCK_HELP, gtk.RESPONSE_HELP )
        response = prompt.run()

        while response == gtk.RESPONSE_HELP:
            self.command_help( "control", "trigger" )
            response = prompt.run()

        prompt.destroy()
        if response != gtk.RESPONSE_OK:
            return
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            # the suite was probably shut down by another process
            warning_dialog( x.__str__(), self.window ).warn()
            return
        result = proxy.trigger_task( task_id )
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def reset_task_state( self, b, task_id, state ):
        msg = "reset " + task_id + " to " + state +"?"
        prompt = gtk.MessageDialog( self.window, gtk.DIALOG_MODAL,
                                    gtk.MESSAGE_QUESTION,
                                    gtk.BUTTONS_OK_CANCEL, msg )

        prompt.add_button( gtk.STOCK_HELP, gtk.RESPONSE_HELP )
        response = prompt.run()

        while response == gtk.RESPONSE_HELP:
            self.command_help( "control", "reset" )
            response = prompt.run()

        prompt.destroy()
        if response != gtk.RESPONSE_OK:
            return
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            # the suite was probably shut down by another process
            warning_dialog( x.__str__(), self.window ).warn()
            return
        result = proxy.reset_task_state( task_id, state )
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def kill_task( self, b, task_id ):
        msg = "remove " + task_id + " (after spawning)?"

        prompt = gtk.MessageDialog( self.window, gtk.DIALOG_MODAL, gtk.MESSAGE_QUESTION,
                                    gtk.BUTTONS_OK_CANCEL, msg )

        prompt.add_button( gtk.STOCK_HELP, gtk.RESPONSE_HELP )
        response = prompt.run()

        while response == gtk.RESPONSE_HELP:
            self.command_help( "control", "remove" )
            response = prompt.run()

        prompt.destroy()
        if response != gtk.RESPONSE_OK:
            return
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            warning_dialog(str(x), self.window).warn()
            return
        result = proxy.spawn_and_die( task_id )
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()
 
    def kill_task_nospawn( self, b, task_id ):
        msg = "remove " + task_id + " (without spawning)?"
        prompt = gtk.MessageDialog( self.window, gtk.DIALOG_MODAL,
                                    gtk.MESSAGE_QUESTION, gtk.BUTTONS_OK_CANCEL, msg )

        prompt.add_button( gtk.STOCK_HELP, gtk.RESPONSE_HELP )
        response = prompt.run()

        while response == gtk.RESPONSE_HELP:
            self.command_help( "control", "remove" )
            response = prompt.run()

        prompt.destroy()
        if response != gtk.RESPONSE_OK:
            return
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            warning_dialog(str(x), self.window).warn()
            return
        result = proxy.die( task_id )
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def purge_cycle_entry( self, e, w, task_id ):
        stop = e.get_text()
        w.destroy()
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            warning_dialog(str(x), self.window).warn()
            return
        result = proxy.purge( task_id, stop )
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def purge_cycle_button( self, b, e, w, task_id ):
        stop = e.get_text()
        w.destroy()
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            warning_dialog(str(x), self.window).warn()
            return
        result = proxy.purge( task_id, stop )
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def stopsuite_popup( self, b ):
        window = gtk.Window()
        window.modify_bg( gtk.STATE_NORMAL, 
                gtk.gdk.color_parse( self.log_colors.get_color()))
        window.set_border_width(5)
        window.set_title( "Stop Suite")
        window.set_transient_for( self.window )
        window.set_type_hint( gtk.gdk.WINDOW_TYPE_HINT_DIALOG )

        vbox = gtk.VBox()

        flabel = gtk.Label( "SUITE: " + self.cfg.suite )
        vbox.pack_start (flabel, True)

        flabel = gtk.Label( "Stop the suite when?" )
        vbox.pack_start (flabel, True)


        stop_rb = gtk.RadioButton( None, "After running tasks have finished" )
        vbox.pack_start (stop_rb, True)
        stopnow_rb = gtk.RadioButton( stop_rb, "NOW (beware of orphaned tasks!)" )
        vbox.pack_start (stopnow_rb, True)
        stopat_rb = gtk.RadioButton( stop_rb, "After all tasks have passed a given TAG" )
        vbox.pack_start (stopat_rb, True)

        st_box = gtk.HBox()
        label = gtk.Label( "STOP (CYCLE or INT')" )
        st_box.pack_start( label, True )
        stoptime_entry = gtk.Entry()
        stoptime_entry.set_max_length(14)
        stoptime_entry.set_sensitive(False)
        label.set_sensitive(False)
        st_box.pack_start (stoptime_entry, True)
        vbox.pack_start( st_box )

        stopct_rb = gtk.RadioButton( stop_rb, "After a given wall clock time" )
        vbox.pack_start (stopct_rb, True)

        sc_box = gtk.HBox()
        label = gtk.Label( 'STOP (YYYY/MM/DD-HH:mm)' )
        sc_box.pack_start( label, True )
        stopclock_entry = gtk.Entry()
        stopclock_entry.set_max_length(16)
        stopclock_entry.set_sensitive(False)
        label.set_sensitive(False)
        sc_box.pack_start (stopclock_entry, True)
        vbox.pack_start( sc_box )

        stoptt_rb = gtk.RadioButton( stop_rb, "After a given task finishes" )
        vbox.pack_start (stoptt_rb, True)
  
        stop_rb.set_active(True)

        tt_box = gtk.HBox()
        label = gtk.Label( 'STOP (task NAME%TAG)' )
        tt_box.pack_start( label, True )
        stoptask_entry = gtk.Entry()
        stoptask_entry.set_sensitive(False)
        label.set_sensitive(False)
        tt_box.pack_start (stoptask_entry, True)
        vbox.pack_start( tt_box )

        stop_rb.connect( "toggled", self.stop_method, "stop", st_box, sc_box, tt_box )
        stopat_rb.connect( "toggled", self.stop_method, "stopat", st_box, sc_box, tt_box )
        stopnow_rb.connect( "toggled", self.stop_method, "stopnow", st_box, sc_box, tt_box )
        stopct_rb.connect( "toggled", self.stop_method, "stopclock", st_box, sc_box, tt_box )
        stoptt_rb.connect( "toggled", self.stop_method, "stoptask", st_box, sc_box, tt_box )

        cancel_button = gtk.Button( "_Cancel" )
        cancel_button.connect("clicked", lambda x: window.destroy() )

        stop_button = gtk.Button( "_Stop" )
        stop_button.connect("clicked", self.stopsuite, window,
                stop_rb, stopat_rb, stopct_rb, stoptt_rb, stopnow_rb,
                stoptime_entry, stopclock_entry, stoptask_entry )

        help_button = gtk.Button( "_Help" )
        help_button.connect("clicked", self.command_help, "control", "stop" )

        hbox = gtk.HBox()
        hbox.pack_start( stop_button, False )
        hbox.pack_end( cancel_button, False )
        hbox.pack_end( help_button, False )
        vbox.pack_start( hbox )

        window.add( vbox )
        window.show_all()

    def stop_method( self, b, meth, st_box, sc_box, tt_box  ):
        for ch in st_box.get_children() + sc_box.get_children() + tt_box.get_children():
            ch.set_sensitive( False )
        if meth == 'stopat':
            for ch in st_box.get_children():
                ch.set_sensitive( True )
        elif meth == 'stopclock':
            for ch in sc_box.get_children():
                ch.set_sensitive( True )
        elif meth == 'stoptask':
            for ch in tt_box.get_children():
                ch.set_sensitive( True )

    def hold_cb_toggled( self, b, box ):
        if b.get_active():
            box.set_sensitive(False)
        else:
            box.set_sensitive(True)

    def startup_method( self, b, meth, ic_box, is_box, no_reset_cb ):
        if meth in ['cold', 'warm', 'raw']:
            for ch in ic_box.get_children():
                ch.set_sensitive( True )
            for ch in is_box.get_children():
                ch.set_sensitive( False )
            no_reset_cb.set_sensitive(False)
        else:
            # restart
            for ch in ic_box.get_children():
                ch.set_sensitive( False )
            for ch in is_box.get_children():
                ch.set_sensitive( True )
            no_reset_cb.set_sensitive(True)

    def startsuite_popup( self, b ):
        window = gtk.Window()
        window.modify_bg( gtk.STATE_NORMAL, 
                gtk.gdk.color_parse( self.log_colors.get_color()))
        window.set_border_width(5)
        window.set_title( "Start Suite '" + self.cfg.suite + "'")
        window.set_transient_for( self.window )
        window.set_type_hint( gtk.gdk.WINDOW_TYPE_HINT_DIALOG )

        vbox = gtk.VBox()

        box = gtk.HBox()
        label = gtk.Label( 'Run mode' )
        box.pack_start(label,True)
        mode_live_rb = gtk.RadioButton( None, "live" )
        box.pack_start (mode_live_rb, True)
        mode_sim_rb = gtk.RadioButton( mode_live_rb, "simulation" )
        box.pack_start (mode_sim_rb, True)
        mode_dum_rb = gtk.RadioButton( mode_live_rb, "dummy" )
        box.pack_start (mode_dum_rb, True)
        mode_live_rb.set_active(True)
        vbox.pack_start( box )
 
        box = gtk.HBox()
        coldstart_rb = gtk.RadioButton( None, "Cold Start" )
        box.pack_start (coldstart_rb, True)
        warmstart_rb = gtk.RadioButton( coldstart_rb, "Warm Start" )
        box.pack_start (warmstart_rb, True)
        rawstart_rb = gtk.RadioButton( coldstart_rb, "Raw Start" )
        box.pack_start (rawstart_rb, True)
        restart_rb = gtk.RadioButton( coldstart_rb, "Restart" )
        box.pack_start (restart_rb, True)
        coldstart_rb.set_active(True)
        vbox.pack_start( box )

        ic_box = gtk.HBox()
        label = gtk.Label( 'START (cycle)' )
        ic_box.pack_start( label, True )
        ctime_entry = gtk.Entry()
        ctime_entry.set_max_length(14)
        ic_box.pack_start (ctime_entry, True)
        vbox.pack_start( ic_box )

        fc_box = gtk.HBox()
        label = gtk.Label( 'STOP (cycle, optional)' )
        fc_box.pack_start( label, True )
        stoptime_entry = gtk.Entry()
        stoptime_entry.set_max_length(14)
        fc_box.pack_start (stoptime_entry, True)
        vbox.pack_start( fc_box )

        load_button = gtk.Button( "_Load START and STOP from suite definition" )
        load_button.connect("clicked", self.loadctimes, ctime_entry, stoptime_entry )
        vbox.pack_start(load_button)

        is_box = gtk.HBox()
        label = gtk.Label( 'FILE (state dump, optional)' )
        is_box.pack_start( label, True )
        statedump_entry = gtk.Entry()
        statedump_entry.set_text( 'state' )
        statedump_entry.set_sensitive( False )
        label.set_sensitive( False )
        is_box.pack_start (statedump_entry, True)
        vbox.pack_start(is_box)

        no_reset_cb = gtk.CheckButton( "Don't reset failed tasks to the 'ready' state" )
        no_reset_cb.set_active(False)
        no_reset_cb.set_sensitive(False)
        vbox.pack_start (no_reset_cb, True)

        coldstart_rb.connect( "toggled", self.startup_method, "cold", ic_box, is_box, no_reset_cb )
        warmstart_rb.connect( "toggled", self.startup_method, "warm", ic_box, is_box, no_reset_cb )
        rawstart_rb.connect ( "toggled", self.startup_method, "raw",  ic_box, is_box, no_reset_cb )
        restart_rb.connect(   "toggled", self.startup_method, "re",   ic_box, is_box, no_reset_cb )
        
        hold_cb = gtk.CheckButton( "Hold on start-up" )
  
        hold_box = gtk.HBox()
        label = gtk.Label( 'Hold after (cycle)' )
        hold_box.pack_start( label, True )
        holdtime_entry = gtk.Entry()
        holdtime_entry.set_max_length(14)
        hold_box.pack_start (holdtime_entry, True)

        vbox.pack_start( hold_cb )
        vbox.pack_start( hold_box )

        hold_cb.connect( "toggled", self.hold_cb_toggled, hold_box )

        hbox = gtk.HBox()
        debug_group = controlled_option_group( "Debug", "--debug" )
        debug_group.pack( hbox )

        refgen_group = controlled_option_group( "Ref-log", "--reference-log" )
        refgen_group.pack( hbox )

        reftest_group = controlled_option_group( "Ref-test", "--reference-test" )
        reftest_group.pack( hbox )

        vbox.pack_start(hbox)

        optgroups = [ debug_group, refgen_group, reftest_group ]

        cancel_button = gtk.Button( "_Cancel" )
        cancel_button.connect("clicked", lambda x: window.destroy() )

        start_button = gtk.Button( "_Start" )
        start_button.connect("clicked", self.startsuite, window,
                coldstart_rb, warmstart_rb, rawstart_rb, restart_rb,
                ctime_entry, stoptime_entry, no_reset_cb,
                statedump_entry, optgroups, mode_live_rb, mode_sim_rb,
                mode_dum_rb, hold_cb, holdtime_entry )

        help_run_button = gtk.Button( "_Help Run" )
        help_run_button.connect("clicked", self.command_help, "control", "run" )

        help_restart_button = gtk.Button( "_Help Restart" )
        help_restart_button.connect("clicked", self.command_help, "control", "restart" )

        hbox = gtk.HBox()
        hbox.pack_start( start_button, False )
        hbox.pack_end( cancel_button, False )
        hbox.pack_end( help_run_button, False )
        hbox.pack_end( help_restart_button, False )
        vbox.pack_start( hbox )

        window.add( vbox )
        window.show_all()

    def popup_purge( self, b, task_id ):
        window = gtk.Window()
        window.modify_bg( gtk.STATE_NORMAL, 
                gtk.gdk.color_parse( self.log_colors.get_color()))
        window.set_border_width(5)
        window.set_title( "Purge " + task_id )
        window.set_transient_for( self.window )
        window.set_type_hint( gtk.gdk.WINDOW_TYPE_HINT_DIALOG )
        #window.set_size_request(800, 300)

        sw = gtk.ScrolledWindow()
        sw.set_policy( gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC )

        vbox = gtk.VBox()
        label = gtk.Label( 'stop cycle (inclusive)' )

        entry = gtk.Entry()
        entry.set_max_length(14)
        entry.connect( "activate", self.purge_cycle_entry, window, task_id )

        hbox = gtk.HBox()
        hbox.pack_start( label, True )
        hbox.pack_start (entry, True)
        vbox.pack_start( hbox )

        start_button = gtk.Button( "_Purge" )
        start_button.connect("clicked", self.purge_cycle_button, entry, window, task_id )

        help_button = gtk.Button( "_Help" )
        help_button.connect("clicked", self.command_help, "control", "purge" )

        cancel_button = gtk.Button( "_Cancel" )
        cancel_button.connect("clicked", lambda x: window.destroy() )

        hbox = gtk.HBox()
        hbox.pack_start( start_button, True)
        hbox.pack_start( help_button, True)
        hbox.pack_start( cancel_button, True )
        vbox.pack_start( hbox )

        window.add( vbox )
        window.show_all()

    def ctime_entry_popup( self, b, callback, title ):
        window = gtk.Window()
        window.modify_bg( gtk.STATE_NORMAL, 
                gtk.gdk.color_parse( self.log_colors.get_color()))
        window.set_border_width(5)
        window.set_title( title )
        window.set_transient_for( self.window )
        window.set_type_hint( gtk.gdk.WINDOW_TYPE_HINT_DIALOG )
        #window.set_size_request(800, 300)

        sw = gtk.ScrolledWindow()
        sw.set_policy( gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC )

        vbox = gtk.VBox()

        hbox = gtk.HBox()
        label = gtk.Label( 'Cycle Time' )
        hbox.pack_start( label, True )
        entry_ctime = gtk.Entry()
        entry_ctime.set_max_length(14)
        hbox.pack_start (entry_ctime, True)
        vbox.pack_start(hbox)

        go_button = gtk.Button( "Go" )
        go_button.connect("clicked", callback, window, entry_ctime )
        vbox.pack_start(go_button)
 
        window.add( vbox )
        window.show_all()

    def insert_task_popup( self, b ):
        window = gtk.Window()
        window.modify_bg( gtk.STATE_NORMAL, 
                gtk.gdk.color_parse( self.log_colors.get_color()))
        window.set_border_width(5)
        window.set_title( "Insert Task" )
        window.set_transient_for( self.window )
        window.set_type_hint( gtk.gdk.WINDOW_TYPE_HINT_DIALOG )
        #window.set_size_request(800, 300)

        sw = gtk.ScrolledWindow()
        sw.set_policy( gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC )

        vbox = gtk.VBox()

        label = gtk.Label( 'SUITE: ' + self.cfg.suite )
        vbox.pack_start( label, True )
 
        hbox = gtk.HBox()
        label = gtk.Label( 'TASK (NAME%TAG)' )
        hbox.pack_start( label, True )
        entry_taskorgroup = gtk.Entry()
        hbox.pack_start (entry_taskorgroup, True)
        vbox.pack_start(hbox)

        hbox = gtk.HBox()
        label = gtk.Label( 'STOP (optional final tag, temporary tasks)' )
        hbox.pack_start( label, True )
        entry_stoptag = gtk.Entry()
        entry_stoptag.set_max_length(14)
        hbox.pack_start (entry_stoptag, True)
        vbox.pack_start(hbox)
 
        help_button = gtk.Button( "_Help" )
        help_button.connect("clicked", self.command_help, "control", "insert" )

        hbox = gtk.HBox()
        insert_button = gtk.Button( "_Insert" )
        insert_button.connect("clicked", self.insert_task, window, entry_taskorgroup, entry_stoptag )
        cancel_button = gtk.Button( "_Cancel" )
        cancel_button.connect("clicked", lambda x: window.destroy() )
        hbox.pack_start(insert_button, False)
        hbox.pack_end(cancel_button, False)
        hbox.pack_end(help_button, False)
        vbox.pack_start( hbox )

        window.add( vbox )
        window.show_all()

    def insert_task( self, w, window, entry_taskorgroup, entry_stoptag ):
        torg = entry_taskorgroup.get_text()
        if torg == '':
            warning_dialog( "Enter task or group ID", self.window ).warn()
            return
        else:
            try:
                tid = TaskID( torg )
            except TaskIDError,x:
                warning_dialog( str(x), self.window ).warn()
                return
            else:
                torg= tid.getstr()

        stoptag = entry_stoptag.get_text()
        if stoptag != '':
            try:
                ct(stoptag)
            except CycleTimeError,x:
                warning_dialog( str(x), self.window ).warn()
                return
        window.destroy()
        if stoptag == '':
            stop = None
        else:
            stop = stoptag
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            warning_dialog( x.__str__(), self.window ).warn()
            return
        result = proxy.insert( torg, stop )
        if not result.success:
            warning_dialog( result.reason, self.window ).warn()
        #else:
        #    info_dialog( result.reason, self.window ).inform()

    def reload_suite( self, w ):
        msg = """Reload the suite definition (EXPERIMENTAL!)
This allows you change task runtime config items
and add or remove task definitions
without restarting the suite."""
        prompt = gtk.MessageDialog( self.window, gtk.DIALOG_MODAL,
                                    gtk.MESSAGE_QUESTION,
                                    gtk.BUTTONS_OK_CANCEL, msg )

        prompt.add_button( gtk.STOCK_HELP, gtk.RESPONSE_HELP )
        response = prompt.run()

        while response == gtk.RESPONSE_HELP:
            self.command_help( "control", "reload" )
            response = prompt.run()

        prompt.destroy()
        if response != gtk.RESPONSE_OK:
            return

        command = "cylc reload -f --host=" + self.cfg.host + " --owner=" + self.cfg.owner + " " + self.cfg.suite
        foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir, 600, 400 )
        self.gcapture_windows.append(foo)
        foo.run()

    def nudge_suite( self, w ):
        try:
            proxy = cylc_pyro_client.client( self.cfg.suite,
                    self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                    self.cfg.pyro_timeout, self.cfg.port ).get_proxy( 'remote' )
        except SuiteIdentificationError, x:
            warning_dialog( str(x), self.window ).warn()
            return False
        result = proxy.nudge()
        if not result:
            warning_dialog( 'Failed to nudge the suite', self.window ).warn()

    def popup_logview( self, task_id, logfiles, jsonly ):
        # TO DO: jsonly is dirty hack to separate the task Job script from
        # task log files; we should do this properly by storing them
        # separately in the task proxy, or at least separating them in
        # the suite state summary.
        window = gtk.Window()
        window.modify_bg( gtk.STATE_NORMAL, 
                gtk.gdk.color_parse( self.log_colors.get_color()))
        window.set_border_width(5)
        window.set_transient_for( self.window )
        window.set_type_hint( gtk.gdk.WINDOW_TYPE_HINT_DIALOG )
        logs = []
        jsfound = False
        for f in logfiles:
            if f.endswith('.err') or f.endswith('.out'):
                logs.append(f)
            else:
                jsfound = True
                js = f

        window.set_size_request(800, 300)
        if jsonly:
            window.set_title( task_id + ": Task Job Submission Script" )
            if jsfound:
                lv = textload( task_id, js )
            else:
                # This should not happen anymore!
                pass

        else:
            # put '.out' before '.err'
            logs.sort( reverse=True )
            window.set_title( task_id + ": Task Logs" )
            lv = combo_logviewer( task_id, logs )
        #print "ADDING to quitters: ", lv
        self.quitters.append( lv )

        window.add( lv.get_widget() )

        #state_button = gtk.Button( "Interrogate" )
        #state_button.connect("clicked", self.popup_requisites, task_id )
 
        quit_button = gtk.Button( "_Close" )
        quit_button.connect("clicked", self.on_popup_quit, lv, window )
        
        lv.hbox.pack_start( quit_button, False )
        #lv.hbox.pack_start( state_button )

        window.connect("delete_event", lv.quit_w_e)
        window.show_all()

    def _set_tooltip( self, widget, tip_text ):
        tooltip = gtk.Tooltips()
        tooltip.enable()
        tooltip.set_tip( widget, tip_text )

    def create_main_menu( self ):
        self.menu_bar = gtk.MenuBar()

        file_menu = gtk.Menu()

        file_menu_root = gtk.MenuItem( '_File' )
        file_menu_root.set_submenu( file_menu )

        exit_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_QUIT )
        exit_item.set_label( 'E_xit (Disconnect From Suite)' )
        exit_item.connect( 'activate', self.click_exit )
        file_menu.append( exit_item )

        self.view_menu = gtk.Menu()
        view_menu_root = gtk.MenuItem( '_View' )
        view_menu_root.set_submenu( self.view_menu )

        self.view1_align_item = gtk.CheckMenuItem(
                                    label="Toggle views _side-by-side" )
        self._set_tooltip( self.view1_align_item,
                           "Toggle horizontal layout of views." )
        self.view1_align_item.connect( 'toggled', self._cb_change_view_align )
        self.view_menu.append( self.view1_align_item )
        
        self.view_menu.append( gtk.SeparatorMenuItem() )

        graph_view0_item = gtk.RadioMenuItem( label="1 - _Graph View" )
        self.view_menu.append( graph_view0_item )
        self._set_tooltip( graph_view0_item, self.VIEW_DESC["graph"] + " - primary panel" )
        graph_view0_item._viewname = "graph"
        graph_view0_item.set_active( self.DEFAULT_VIEW == "graph" )

        dot_view0_item = gtk.RadioMenuItem( group=graph_view0_item, label="1 - _Dot View" )
        self.view_menu.append( dot_view0_item )
        self._set_tooltip( dot_view0_item, self.VIEW_DESC["dot"] + " - primary panel" )
        dot_view0_item._viewname = "dot"
        dot_view0_item.set_active( self.DEFAULT_VIEW == "dot" )

        text_view0_item = gtk.RadioMenuItem( group=graph_view0_item, label="1 - _Text View" )
        self.view_menu.append( text_view0_item )
        self._set_tooltip( text_view0_item, self.VIEW_DESC["text"] + " - primary panel" )
        text_view0_item._viewname = "text"
        text_view0_item.set_active( self.DEFAULT_VIEW == "text" )

        graph_view0_item.connect( 'toggled', self._cb_change_view0_menu )
        dot_view0_item.connect( 'toggled', self._cb_change_view0_menu )
        text_view0_item.connect( 'toggled', self._cb_change_view0_menu )
        self.view_menu_views0 = [ graph_view0_item, dot_view0_item, text_view0_item ]
        
        self.views_option_menuitems = [ gtk.MenuItem(  "1 - _Options" ) ]
        self.views_option_menus = [gtk.Menu()]
        self.views_option_menuitems[0].set_submenu( self.views_option_menus[0] )
        self._set_tooltip( self.views_option_menuitems[0], "Options for primary panel" )
        self.view_menu.append( self.views_option_menuitems[0] )
        
        self.view_menu.append( gtk.SeparatorMenuItem() )
        
        no_view1_item = gtk.RadioMenuItem( label="2 - Off" )
        no_view1_item.set_active( True )
        self.view_menu.append( no_view1_item )
        self._set_tooltip( no_view1_item, "Switch off secondary view panel" )
        no_view1_item._viewname = "None"
        no_view1_item.connect( 'toggled', self._cb_change_view1_menu )

        graph_view1_item = gtk.RadioMenuItem( group=no_view1_item, label="2 - Grap_h View" )
        self.view_menu.append( graph_view1_item )
        self._set_tooltip( graph_view1_item, self.VIEW_DESC["graph"] + " - secondary panel" )
        graph_view1_item._viewname = "graph"
        graph_view1_item.connect( 'toggled', self._cb_change_view1_menu )

        dot_view1_item = gtk.RadioMenuItem( group=no_view1_item, label="2 - Dot _View" )
        self.view_menu.append( dot_view1_item )
        self._set_tooltip( dot_view1_item, self.VIEW_DESC["dot"] + " - secondary panel" )
        dot_view1_item._viewname = "dot"
        dot_view1_item.connect( 'toggled', self._cb_change_view1_menu )

        text_view1_item = gtk.RadioMenuItem( group=no_view1_item, label="2 - Te_xt View" )
        self.view_menu.append( text_view1_item )
        self._set_tooltip( text_view1_item, self.VIEW_DESC["text"] + " - secondary panel" )
        text_view1_item._viewname = "text"
        text_view1_item.connect( 'toggled', self._cb_change_view1_menu )

        self.view_menu_views1 = [ no_view1_item,
                                  graph_view1_item,
                                  dot_view1_item,
                                  text_view1_item ]

        self.views_option_menuitems.append( gtk.MenuItem(  "2 - O_ptions" ) )
        self.views_option_menus.append( gtk.Menu() )
        self._set_tooltip( self.views_option_menuitems[1], "Options for secondary panel" )
        self.views_option_menuitems[1].set_submenu( self.views_option_menus[1] )
        self.view_menu.append( self.views_option_menuitems[1] )

        start_menu = gtk.Menu()
        start_menu_root = gtk.MenuItem( '_Control' )
        start_menu_root.set_submenu( start_menu )

        self.run_menuitem = gtk.ImageMenuItem( 
                                   stock_id=gtk.STOCK_MEDIA_PLAY )
        self.run_menuitem.set_label( '_Run Suite ... ' )
        start_menu.append( self.run_menuitem )
        self.run_menuitem.connect( 'activate', self.startsuite_popup )

        self.pause_menuitem = gtk.ImageMenuItem(
                                   stock_id=gtk.STOCK_MEDIA_PAUSE )
        self.pause_menuitem.set_label( '_Hold Suite (pause)' )
        start_menu.append( self.pause_menuitem )
        self.pause_menuitem.connect( 'activate', self.pause_suite )

        self.unpause_menuitem = gtk.ImageMenuItem(
                                    stock_id=gtk.STOCK_MEDIA_PLAY )
        self.unpause_menuitem.set_label( 'R_elease Suite (unpause)' )
        start_menu.append( self.unpause_menuitem )
        self.unpause_menuitem.connect( 'activate', self.resume_suite )

        self.stop_menuitem = gtk.ImageMenuItem( 
                                  stock_id=gtk.STOCK_MEDIA_STOP )
        self.stop_menuitem.set_label( '_Stop Suite ... ' )
        start_menu.append( self.stop_menuitem )
        self.stop_menuitem.connect( 'activate', self.stopsuite_popup )

        start_menu.append( gtk.SeparatorMenuItem() )

        nudge_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_REFRESH )
        nudge_item.set_label( "_Nudge (updates times)" )
        start_menu.append( nudge_item )
        nudge_item.connect( 'activate', self.nudge_suite  )

        reload_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_CDROM )
        reload_item.set_label( "Re_load Suite Definition ..." )
        start_menu.append( reload_item )
        reload_item.connect( 'activate', self.reload_suite  )

        insert_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_PASTE )
        insert_item.set_label( '_Insert Task(s) ...' )
        start_menu.append( insert_item )
        insert_item.connect( 'activate', self.insert_task_popup )

        start_menu.append( gtk.SeparatorMenuItem() )

        block_item = gtk.ImageMenuItem( 
                         stock_id=gtk.STOCK_DIALOG_AUTHENTICATION )
        block_item.set_label( '_Block Access' )
        start_menu.append( block_item )
        block_item.connect( 'activate', self.block_suite )

        unblock_item = gtk.ImageMenuItem(
                           stock_id=gtk.STOCK_DIALOG_AUTHENTICATION )
        unblock_item.set_label( 'Unbl_ock Access' )
        start_menu.append( unblock_item )
        unblock_item.connect( 'activate', self.unblock_suite )

        start_menu.append( gtk.SeparatorMenuItem() )

        runahead_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_JUMP_TO )
        runahead_item.set_label( '_Change Runahead Limit ...' )
        start_menu.append( runahead_item )
        runahead_item.connect( 'activate', self.change_runahead_popup )

        tools_menu = gtk.Menu()
        tools_menu_root = gtk.MenuItem( '_Tools' )
        tools_menu_root.set_submenu( tools_menu )

        val_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_APPLY )
        val_item.set_label( 'Suite _Validate' ) 
        tools_menu.append( val_item )
        val_item.connect( 'activate', self.run_suite_validate )

        info_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_DIALOG_INFO )
        info_item.set_label( 'Suite _Info' )
        tools_menu.append( info_item )
        info_item.connect( 'activate', self.run_suite_info )

        graph_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_SELECT_COLOR )
        graph_item.set_label( 'Suite _Graph' )
        tools_menu.append( graph_item )
        graphmenu = gtk.Menu()
        graph_item.set_submenu(graphmenu)

        gtree_item = gtk.MenuItem( '_Dependencies' )
        graphmenu.append( gtree_item )
        gtree_item.connect( 'activate', self.run_suite_graph, False )

        gns_item = gtk.MenuItem( '_Namespaces' )
        graphmenu.append( gns_item )
        gns_item.connect( 'activate', self.run_suite_graph, True )

        list_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_INDEX )
        list_item.set_label( 'Suite _List' )
        tools_menu.append( list_item )
        list_menu = gtk.Menu()
        list_item.set_submenu( list_menu )
 
        flat_item = gtk.MenuItem( '_Tasks' )
        list_menu.append( flat_item )
        flat_item.connect( 'activate', self.run_suite_list )

        tree_item = gtk.MenuItem( '_Namespaces' )
        list_menu.append( tree_item )
        tree_item.connect( 'activate', self.run_suite_list, '-t' )

        log_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_DND )
        log_item.set_label( 'Suite _Log' )
        tools_menu.append( log_item )
        log_item.connect( 'activate', self.run_suite_log )

        view_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_EDIT )
        view_item.set_label( 'Suite _View' )
        tools_menu.append( view_item )
        subviewmenu = gtk.Menu()
        view_item.set_submenu(subviewmenu)

        rw_item = gtk.MenuItem( '_Raw' )
        subviewmenu.append( rw_item )
        rw_item.connect( 'activate', self.run_suite_view, 'raw' )
 
        viewi_item = gtk.MenuItem( '_Inlined' )
        subviewmenu.append( viewi_item )
        viewi_item.connect( 'activate', self.run_suite_view, 'inlined' )
 
        viewp_item = gtk.MenuItem( '_Processed' )
        subviewmenu.append( viewp_item )
        viewp_item.connect( 'activate', self.run_suite_view, 'processed' )

        edit_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_EDIT )
        edit_item.set_label( 'Suite _Edit' )
        tools_menu.append( edit_item )
        edit_menu = gtk.Menu()
        edit_item.set_submenu(edit_menu)

        raw_item = gtk.MenuItem( '_Raw' )
        edit_menu.append( raw_item )
        raw_item.connect( 'activate', self.run_suite_edit, False )

        inl_item = gtk.MenuItem( '_Inlined' )
        edit_menu.append( inl_item )
        inl_item.connect( 'activate', self.run_suite_edit, True )

        help_menu = gtk.Menu()
        help_menu_root = gtk.MenuItem( '_Help' )
        help_menu_root.set_submenu( help_menu )

        self.userguide_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_HELP )
        self.userguide_item.set_label( '_GUI Quick Guide' )
        self.userguide_item.connect( 'activate', helpwindow.userguide )
        help_menu.append( self.userguide_item )

        help_menu.append( gtk.SeparatorMenuItem() )

        chelp_menu = gtk.ImageMenuItem( stock_id=gtk.STOCK_EXECUTE )
        chelp_menu.set_label( 'Command Help' )
        help_menu.append( chelp_menu )
        self.construct_command_menu( chelp_menu )

        help_menu.append( gtk.SeparatorMenuItem() )

        cug_pdf_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_EDIT )
        cug_pdf_item.set_label( '_PDF User Guide' )
        help_menu.append( cug_pdf_item )
        cug_pdf_item.connect( 'activate', self.browse, '--pdf' )
  
        cug_html_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_DND_MULTIPLE )
        cug_html_item.set_label( '_Multi Page HTML User Guide' )
        help_menu.append( cug_html_item )
        cug_html_item.connect( 'activate', self.browse, '--html' )

        cug_shtml_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_DND )
        cug_shtml_item.set_label( '_Single Page HTML User Guide' )
        help_menu.append( cug_shtml_item )
        cug_shtml_item.connect( 'activate', self.browse, '--html-single' )

        cug_www_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_HOME )
        cug_www_item.set_label( '_Internet Home Page' )
        help_menu.append( cug_www_item )
        cug_www_item.connect( 'activate', self.browse, '--www' )
 
        help_menu.append( gtk.SeparatorMenuItem() )
 
        cug_clog_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_EDIT )
        cug_clog_item.set_label( 'Change _Log' )
        help_menu.append( cug_clog_item )
        cug_clog_item.connect( 'activate', self.browse, '-g --log' )
 
        about_item = gtk.ImageMenuItem( stock_id=gtk.STOCK_ABOUT )
        about_item.set_label( '_About' )
        help_menu.append( about_item )
        about_item.connect( 'activate', self.about )

        self.menu_bar.append( file_menu_root )
        self.menu_bar.append( view_menu_root )
        self.menu_bar.append( start_menu_root )
        self.menu_bar.append( tools_menu_root )
        self.menu_bar.append( help_menu_root )

    def construct_command_menu( self, menu ):
        ## # JUST CONTROL COMMANDS:
        ## com_menu = gtk.Menu()
        ## menu.set_submenu( com_menu )
        ## cout = subprocess.Popen( ["cylc", "category=control" ], stdout=subprocess.PIPE ).communicate()[0]
        ## commands = cout.rstrip().split()
        ## for command in commands:
        ##     if command == "gcylc":
        ##         continue
        ##     bar_item = gtk.MenuItem( command )
        ##     com_menu.append( bar_item )
        ##     bar_item.connect( 'activate', self.command_help, "control", command )
        # ALL COMMANDS
        # ALL COMMANDS
        cat_menu = gtk.Menu()
        menu.set_submenu( cat_menu )

        cylc_help_item = gtk.MenuItem( 'cylc' )
        cat_menu.append( cylc_help_item )
        cylc_help_item.connect( 'activate', self.command_help )

        cout = subprocess.Popen( ["cylc", "categories"], stdout=subprocess.PIPE ).communicate()[0]
        categories = cout.rstrip().split()
        for category in categories: 
            foo_item = gtk.MenuItem( category )
            cat_menu.append( foo_item )
            com_menu = gtk.Menu()
            foo_item.set_submenu( com_menu )
            cout = subprocess.Popen( ["cylc", "category="+category ], stdout=subprocess.PIPE ).communicate()[0]
            commands = cout.rstrip().split()
            for command in commands:
                bar_item = gtk.MenuItem( command )
                com_menu.append( bar_item )
                bar_item.connect( 'activate', self.command_help, category, command )

    def create_tool_bar( self ):
        """Create the tool bar for the control GUI."""
        self.tool_bars = [ gtk.Toolbar(), gtk.Toolbar() ]
        views = self.VIEWS_ORDERED
        self.tool_bar_view0 = gtk.ComboBox()
        self.tool_bar_view1 = gtk.ComboBox()
        pixlist0 = gtk.ListStore( gtk.gdk.Pixbuf, str )
        pixlist1 = gtk.ListStore( gtk.gdk.Pixbuf, str, bool, bool )
        view_items = []
        for v in views:
             pixbuf = gtk.gdk.pixbuf_new_from_file( self.cfg.imagedir + self.VIEW_ICON_PATHS[v] )
             pixlist0.append( ( pixbuf, v ) )
             pixlist1.append( ( pixbuf, v, True, False ) )
        pixlist1.insert( 0, ( pixbuf, "None", False, True ) )
        # Primary view chooser
        self.tool_bar_view0.set_model( pixlist0 )
        cell_pix0 = gtk.CellRendererPixbuf()
        self.tool_bar_view0.pack_start( cell_pix0 )
        self.tool_bar_view0.add_attribute( cell_pix0, "pixbuf", 0 )
        self.tool_bar_view0.set_active(0)
        self.tool_bar_view0.connect( "changed", self._cb_change_view0_tool )
        self._set_tooltip( self.tool_bar_view0, "Change primary view" )
        self.view_toolitems = [ gtk.ToolItem() ]
        self.view_toolitems[0].add( self.tool_bar_view0 )
        # Secondary view chooser
        self.tool_bar_view1.set_model( pixlist1 )
        cell_pix1 = gtk.CellRendererPixbuf()
        cell_text1 = gtk.CellRendererText()
        self.tool_bar_view1.pack_start( cell_pix1 )
        self.tool_bar_view1.pack_start( cell_text1 )
        self.tool_bar_view1.add_attribute( cell_pix1, "pixbuf", 0 )
        self.tool_bar_view1.add_attribute( cell_text1, "text", 1 )
        self.tool_bar_view1.add_attribute( cell_pix1, "visible", 2 )
        self.tool_bar_view1.add_attribute( cell_text1, "visible", 3 )
        self.tool_bar_view1.set_active(0)
        self.tool_bar_view1.connect( "changed", self._cb_change_view1_tool )
        self._set_tooltip( self.tool_bar_view1, "Change secondary view" )
        self.view_toolitems.append( gtk.ToolItem() )
        self.view_toolitems[1].add( self.tool_bar_view1 )
        # Horizontal layout toggler
        self.layout_toolbutton = gtk.ToggleToolButton()
        image1 = gtk.image_new_from_stock( gtk.STOCK_GOTO_FIRST, gtk.ICON_SIZE_MENU )
        image2 = gtk.image_new_from_stock( gtk.STOCK_GOTO_LAST, gtk.ICON_SIZE_MENU )
        im_box = gtk.HBox()
        im_box.pack_start( image1, expand=False, fill=False )
        im_box.pack_start( image2, expand=False, fill=False )
        self.layout_toolbutton.set_icon_widget( im_box )
        self.layout_toolbutton.connect( "toggled", self._cb_change_view_align )
        self.layout_toolbutton.set_active( self.view_layout_horizontal )
        self._set_tooltip( self.layout_toolbutton,
                           "Toggle side-by-side layout of views." )
        toggle_layout_toolitem = gtk.ToolItem()
        toggle_layout_toolitem.add( self.layout_toolbutton )
        # Insert the view choosers
        view0_label_item = gtk.ToolItem()
        view0_label_item.add( gtk.Label("View 1: ") )
        self._set_tooltip( view0_label_item, "Configure primary view" )
        view1_label_item = gtk.ToolItem()
        view1_label_item.add( gtk.Label("View 2: ") )
        self._set_tooltip( view1_label_item, "Configure secondary view" )
        self.tool_bars[1].insert( self.view_toolitems[1], 0 )
        self.tool_bars[1].insert( view1_label_item, 0 )
        self.tool_bars[0].insert( self.view_toolitems[0], 0 )
        self.tool_bars[0].insert( view0_label_item, 0 )
        self.tool_bars[0].insert( gtk.SeparatorToolItem(), 0 )
        self.tool_bars[0].insert( toggle_layout_toolitem, 0 )
        self.tool_bars[0].insert( gtk.SeparatorToolItem(), 0 )
        stop_icon = gtk.image_new_from_stock( gtk.STOCK_MEDIA_STOP,
                                              gtk.ICON_SIZE_SMALL_TOOLBAR )
        self.stop_toolbutton = gtk.ToolButton( icon_widget=stop_icon )
        tooltip = gtk.Tooltips()
        tooltip.enable()
        tooltip.set_tip( self.stop_toolbutton, 
"""Stop Suite after all running tasks finish.
For more Stop options use the Control menu.""" )
        self.stop_toolbutton.connect( "clicked", self.stopsuite_default )
        self.tool_bars[0].insert(self.stop_toolbutton, 0)

        run_icon = gtk.image_new_from_stock( gtk.STOCK_MEDIA_PLAY,
                                             gtk.ICON_SIZE_SMALL_TOOLBAR )
        self.run_pause_toolbutton = gtk.ToolButton( icon_widget=run_icon )
        click_func = self.startsuite_popup
        tooltip = gtk.Tooltips()
        tooltip.enable()
        tooltip.set_tip( self.run_pause_toolbutton, "Run Suite..." )
        self.run_pause_toolbutton.connect( "clicked", lambda w: w.click_func( w ) )
        self.tool_bars[0].insert(self.run_pause_toolbutton, 0)
        self.tool_bar_box = gtk.HPaned()
        self.tool_bar_box.pack1( self.tool_bars[0], resize=True, shrink=True )
        self.tool_bar_box.pack2( self.tool_bars[1], resize=True, shrink=True )

    def _alter_status_toolbar_menu( self, new_status ):
        # Handle changes in status for some toolbar/menuitems.
        if new_status == self._prev_status:
            return False
        self._prev_status = new_status
        if "connected" in new_status:
            self.stop_toolbutton.set_sensitive( False )
            return False
        run_ok = bool( "stopped" in new_status )
        pause_ok = bool( "running" in new_status )
        unpause_ok = bool( "held" in new_status or "stopping" in new_status )
        stop_ok = bool( "stopped" not in new_status )
        self.run_menuitem.set_sensitive( run_ok )
        self.pause_menuitem.set_sensitive( pause_ok )
        self.unpause_menuitem.set_sensitive( unpause_ok )
        self.stop_menuitem.set_sensitive( stop_ok )
        self.stop_toolbutton.set_sensitive( stop_ok and 
                                            "stopping" not in new_status )
        if pause_ok:
            icon = gtk.STOCK_MEDIA_PAUSE
            tip_text = "Hold Suite (pause)"
            click_func = self.pause_suite
        elif run_ok:
            icon = gtk.STOCK_MEDIA_PLAY
            tip_text = "Run Suite ..."
            click_func = self.startsuite_popup
        elif unpause_ok:
            icon = gtk.STOCK_MEDIA_PLAY
            tip_text = "Release Suite (unpause)"
            click_func = self.resume_suite
        else:
            # how do we end up here?
            self.run_pause_toolbutton.set_sensitive( False )
            return False
        icon_widget = gtk.image_new_from_stock( icon,
                                                gtk.ICON_SIZE_SMALL_TOOLBAR )
        icon_widget.show()
        self.run_pause_toolbutton.set_icon_widget( icon_widget )
        tip_tuple = gtk.tooltips_data_get( self.run_pause_toolbutton )
        if tip_tuple is None:
            tips = gtk.Tooltips()
            tips.enable()
        tips.set_tip( self.run_pause_toolbutton, tip_text )
        self.run_pause_toolbutton.click_func = click_func

    def create_info_bar( self ):
        self.info_bar = InfoBar( self.cfg.suite, self.cfg.imagedir,
                                 self._alter_status_toolbar_menu )

    #def check_connection( self ):
    #    # called on a timeout in the gtk main loop, tell the log viewer
    #    # to reload if the connection has been lost and re-established,
    #    # which probably means the cylc suite was shutdown and
    #    # restarted.
    #    try:
    #        cylc_pyro_client.ping( self.cfg.host, self.cfg.port )
    #    except Pyro.errors.ProtocolError:
    #        print "NO CONNECTION"
    #        self.connection_lost = True
    #    else:
    #        print "CONNECTED"
    #        if self.connection_lost:
    #            #print "------>INITIAL RECON"
    #            self.connection_lost = False
    #    # always return True so that we keep getting called
    #    return True

    def get_pyro( self, object ):
        return cylc_pyro_client.client( self.cfg.suite,
                self.cfg.pphrase, self.cfg.owner, self.cfg.host,
                self.cfg.pyro_timeout, self.cfg.port ).get_proxy( object )

    def run_suite_validate( self, w ):
        command = ( "cylc validate -v " + self.get_remote_run_opts() + 
                " --notify-completion " + self.cfg.suite )
        foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir )
        self.gcapture_windows.append(foo)
        foo.run()
        return False

    def run_suite_edit( self, w, inlined=False):
        extra = ''
        if inlined:
            extra = '-i '
        command = ( "cylc edit --notify-completion -g" + self.get_remote_run_opts() + 
                    " " + extra + ' ' + self.cfg.suite )
        foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir )
        self.gcapture_windows.append(foo)
        foo.run()
        return False

    def run_suite_graph( self, w, show_ns=False ):
        if show_ns:
            command = ( "cylc graph --notify-completion --namespaces " +
                        self.get_remote_run_opts() +
                        " " + self.cfg.suite )
            foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir )
            self.gcapture_windows.append(foo)
            foo.run()
        else:
            times = []
            for option in ["'initial cycle time'", "'final cycle time'"]:
                command = ( "cylc get-config" + self.get_remote_run_opts() +
                            " " + self.cfg.suite + 
                            " visualization " + option )
                res, pieces = run_get_stdout( command )
                if not res or not pieces:
                    return False
                times.append(pieces[0].strip())
            graph_suite_popup( self.cfg.suite, self.command_help, times[0], times[1],
                               self.get_remote_run_opts(), self.gcapture_windows,
                               self.cfg.cylc_tmpdir, parent_window=self.window )

    def run_suite_info( self, w ):
        command = ( "cylc show --notify-completion" + self.get_remote_run_opts() + 
                    " " + self.cfg.suite )
        foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir, 600, 400 )
        self.gcapture_windows.append(foo)
        foo.run()

    def run_suite_list( self, w, opt='' ):
        command = ( "cylc list " + self.get_remote_run_opts() + " " + opt +
                    " --notify-completion " + self.cfg.suite )
        foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir, 600, 600 )
        self.gcapture_windows.append(foo)
        foo.run()

    def run_suite_log( self, w ):
        com = False
        if is_remote_host( self.cfg.host ) or is_remote_user( self.cfg.owner ):
            com = True
            warning_dialog( \
"""The full-function GUI log viewer is only available
for local suites; I will call "cylc cat-log" instead.""" ).warn()
            command = ( "cylc cat-log --notify-completion" + self.get_remote_run_opts() +
                        " " + self.cfg.suite )
            foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir )
            self.gcapture_windows.append(foo)
            foo.run()
            return

        # just for local suites (so --host and --owner are not needed here)
        command = "cylc get-config --mark-output " + self.cfg.suite + " cylc logging directory"
        res, lst = run_get_stdout( command, filter=True )
        logging_dir = lst[0]
        command = "cylc get-config --mark-output --tasks " + self.cfg.suite
        res, tasks = run_get_stdout( command, filter=True )
        if res:
            task_list = tasks
        else:
            task_list = []
        foo = cylc_logviewer( 'log', logging_dir, task_list)
        self.quitters.append(foo)

    def run_suite_view( self, w, method ):
        extra = ''
        if method == 'inlined':
            extra = ' -i'
        elif method == 'processed':
            extra = ' -j'

        command = ( "cylc view --notify-completion -g " + self.get_remote_run_opts() + 
                    " " + extra + " " + self.cfg.suite )
        foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir, 400 )
        self.gcapture_windows.append(foo)
        foo.run()
        return False

    def get_remote_run_opts( self ):
        return " --host=" + self.cfg.host + " --owner=" + self.cfg.owner

    def browse( self, b, option='' ):
        command = 'cylc documentation ' + option
        foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir, 400 )
        self.gcapture_windows.append(foo)
        foo.run()

    def command_help( self, w, cat='', com='' ):
        command = "cylc " + cat + " " + com + " help"
        foo = gcapture_tmpfile( command, self.cfg.cylc_tmpdir, 700, 600 )
        self.gcapture_windows.append(foo)
        foo.run()
