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

# This module uses the @classmethod decorator, introduced in Python 2.4.
# . @classmethod
# . def foo( bar ):
# .   pass
# Equivalent Python<2.4 form:
# . def foo( bar ):
# .   pass
# . foo = classmethod( foo )

# TASK PROXY BASE CLASS:

import sys, re
from copy import deepcopy
import datetime
from cylc import task_state
from cylc.RunEventHandler import RunHandler
import logging
import Pyro.core

def displaytd( td ):
    # Display a python timedelta sensibly.
    # Default for str(td) of -5 sec is '-1 day, 23:59:55' !
    d, s, m = td.days, td.seconds, td.microseconds
    secs = d * 24 * 3600 + s + m / 10**6
    if secs < 0:
        res = '-' + str( datetime.timedelta( 0, - secs, 0 ))
    else:
        res = str(td)
    return res

# NOTE ON TASK STATE INFORMATION---------------------------------------

# task attributes required for a system cold start are:
#  state ('waiting', 'submitted', 'running', and 'succeeded' or 'failed')

# The 'state' variable is initialised by the base class, and written to
# the state dump file by the base class dump_state() method.

# For a restart from previous state some tasks may require additional
# state information to be stored in the state dump file.

# To handle this difference in initial state information (between normal
# start and restart) task initialisation must use a default value of
# 'None' for the additional variables, and for a restart the task
# manager must instantiate each task with a flattened list of all the
# state values found in the state dump file.

# NOTE ON EXECUTION OF EVENT HANDLERS:
# These have to be executed in the background because (a) they could
# take a long time to execute, or (b) they could try to operate on the
# suite in some way (e.g. to remove a failed task automatically) - this
# would create a deadlock if cylc waited on them to complete before
# carrying on. Consequently cylc cannot to detect failure of a handler
# (not easily at least ...)

class task( Pyro.core.ObjBase ):

    clock = None
    intercycle = False
    suite = None
    state_changed = True

    # set by the back door at startup:
    cylc_env = {}

    @classmethod
    def describe( cls ):
        return cls.description

    @classmethod
    def set_class_var( cls, item, value ):
        # set the value of a class variable
        # that will be written to the state dump file
        try:
            cls.class_vars[ item ] = value
        except AttributeError:
            cls.class_vars = {}
            cls.class_vars[ item ] = value

    @classmethod
    def get_class_var( cls, item ):
        # get the value of a class variable that is
        # written to the state dump file
        try:
            return cls.class_vars[ item ]
        except:
            raise AttributeError

    @classmethod
    def dump_class_vars( cls, FILE ):
        # dump special class variables to the state dump file
        try:
            result = ''
            for key in cls.class_vars:
                result += key + '=' + str( cls.class_vars[ key ] ) + ', '
            result = result.rstrip( ', ' )
            FILE.write( 'class ' + cls.__name__ + ' : ' + result + '\n')
        except AttributeError:
            # class has no class_vars defined
            pass

    @classmethod
    def update_mean_total_elapsed_time( cls, started, succeeded ):
        # the class variables here are defined in derived task classes
        cls.elapsed_times.append( succeeded - started )
        elt_sec = [x.days * 86400 + x.seconds for x in cls.elapsed_times ]
        mtet_sec = sum( elt_sec ) / len( elt_sec )
        cls.mean_total_elapsed_time = datetime.timedelta( seconds=mtet_sec )

    def __init__( self, state ):
        # Call this AFTER derived class initialisation

        # Derived class init MUST define:
        #  * self.id: unique identity (e.g. NAME%CYCLE for cycling tasks)
        #  * prerequisites and outputs
        #  * self.env_vars

        class_vars = {}
        self.state = task_state.task_state( state )
        self.launcher = None
        self.trigger_now = False

        # Count instances of each top level object derived from task.
        # Top level derived classes must define:
        #   <class>.instance_count = 0
        # NOTE: top level derived classes are now defined dynamically
        # (so this is initialised in src/taskdef.py).
        self.__class__.instance_count += 1
        self.__class__.upward_instance_count += 1

        Pyro.core.ObjBase.__init__(self)

        self.latest_message = ""
        self.latest_message_priority = "NORMAL"

        self.submission_timer_start = None
        self.execution_timer_start = None

        self.submitted_time = None
        self.started_time = None
        self.succeeded_time = None
        self.etc = None
        self.to_go = None
        self.try_number = 1
        self.retry_delay_timer_start = None

    def plog( self, message ):
        # print and log a low priority message
        print self.id + ':', message
        self.log( 'NORMAL', message )

    def log( self, priority, message ):
        logger = logging.getLogger( "main" )
        message = '[' + self.id + '] -' + message
        if priority == "WARNING":
            logger.warning( message )
        elif priority == "NORMAL":
            logger.info( message )
        elif priority == "DEBUG":
            logger.debug( message )
        elif priority == "CRITICAL":
            logger.critical( message )
        else:
            logger.warning( 'UNKNOWN PRIORITY: ' + priority )
            logger.warning( '-> ' + message )

    def prepare_for_death( self ):
        # Decrement the instance count of objects derived from task
        # base. Was once used for constraining the number of instances
        # of each task type. Python's __del__() function cannot be used
        # for this as it is only called when a deleted object is about
        # to be garbage collected (not guaranteed to be right away). 
        self.__class__.instance_count -= 1

    def ready_to_run( self ):
        ready = False
        if self.state.is_queued() or \
            self.state.is_waiting() and self.prerequisites.all_satisfied():
                if self.retry_delay_timer_start:
                     diff = task.clock.get_datetime() - self.retry_delay_timer_start
                     foo = datetime.timedelta( 0,0,0,0,self.retry_delay,0,0 )
                     if diff >= foo:
                        ready = True
                else:
                        ready = True
        return ready

    def get_resolved_dependencies( self ):
        dep = []
        satby = self.prerequisites.get_satisfied_by()
        for label in satby.keys():
            dep.append( satby[ label ] )
        return dep

    def set_submitted( self ):
        self.state.set_status( 'submitted' )
        self.log( 'NORMAL', "job submitted" )
        self.submitted_time = task.clock.get_datetime()
        self.submission_timer_start = self.submitted_time
        handler = self.__class__.event_handlers['submitted']
        if handler:
            RunHandler( 'submitted', handler, self.__class__.suite, self.id, '(task submitted)' )

    def set_running( self ):
        self.state.set_status( 'running' )
        self.started_time = task.clock.get_datetime()
        self.started_time_real = datetime.datetime.now()
        self.execution_timer_start = self.started_time
        handler = self.__class__.event_handlers['started']
        if handler:
            RunHandler( 'started', handler, self.__class__.suite, self.id, '(task started)' )

    def set_succeeded( self ):
        self.outputs.set_all_completed()
        self.state.set_status( 'succeeded' )
        self.succeeded_time = task.clock.get_datetime()
        # don't update mean total elapsed time if set_succeeded() was called

    def set_succeeded_handler( self ):
        # (set_succeeded() is used by remote switch)
        print '\n' + self.id + " SUCCEEDED"
        self.state.set_status( 'succeeded' )
        handler = self.__class__.event_handlers['succeeded']
        if handler:
            RunHandler( 'succeeded', handler, self.__class__.suite, self.id, '(task succeeded)' )

    def set_failed( self, reason='(task failed)' ):
        self.state.set_status( 'failed' )
        self.log( 'CRITICAL', reason )
        handler = self.__class__.event_handlers['failed']
        if handler:
            RunHandler( 'failed', handler, self.__class__.suite, self.id, reason )

    def set_submit_failed( self, reason='(job submission failed)' ):
        self.state.set_status( 'failed' )
        self.log( 'CRITICAL', reason )
        handler = self.__class__.event_handlers['submission failed']
        if handler:
            RunHandler( 'submission_failed', handler, self.__class__.suite, self.id, reason )

    def unfail( self ):
        # if a task is manually reset remove any previous failed message
        # or on later success it will be seen as an incomplete output.
        failed_msg = self.id + " failed"
        if self.outputs.exists(failed_msg):
            self.outputs.remove(failed_msg)

    def reset_state_ready( self ):
        self.state.set_status( 'waiting' )
        self.prerequisites.set_all_satisfied()
        self.unfail()
        self.outputs.set_all_incomplete()

    def reset_state_waiting( self ):
        # waiting and all prerequisites UNsatisified.
        self.state.set_status( 'waiting' )
        self.prerequisites.set_all_unsatisfied()
        self.unfail()
        self.outputs.set_all_incomplete()

    def reset_state_succeeded( self ):
        # all prerequisites satisified and all outputs complete
        self.state.set_status( 'succeeded' )
        self.prerequisites.set_all_satisfied()
        self.unfail()
        self.outputs.set_all_completed()

    def reset_state_failed( self ):
        # all prerequisites satisified and no outputs complete
        self.state.set_status( 'failed' )
        self.prerequisites.set_all_satisfied()
        self.outputs.set_all_incomplete()
        # set a new failed output just as if a failure message came in
        self.outputs.add( self.id + ' failed', completed=True )

    def reset_state_held( self ):
        itask.state.set_status( 'held' )

    def submit( self, bcvars={}, dry_run=False ):
        self.log( 'DEBUG', 'submitting task job script' )
        # construct the job launcher here so that a new one is used if
        # the task is re-triggered by the suite operator - so it will
        # get new stdout/stderr logfiles and not overwrite the old ones.

        # dynamic instantiation - don't know job sub method till run time.
        module_name = self.job_submit_method
        class_name  = self.job_submit_method
        # NOTE: not using__import__() keyword arguments:
        #mod = __import__( module_name, fromlist=[class_name] )
        # as these were only introduced in Python 2.5.
        try:
            # try to import built-in job submission classes first
            mod = __import__( 'cylc.job_submission.' + module_name, globals(), locals(), [class_name] )
        except ImportError:
            try:
                # else try for user-defined job submission classes, in sys.path
                mod = __import__( module_name, globals(), locals(), [class_name] )
            except ImportError, x:
                print >> sys.stderr, x
                raise SystemExit( 'ERROR importing job submission method: ' + class_name )

        launcher_class = getattr( mod, class_name )

        # To Do: most of the following could be class variables?
        # To Do: should cylc_env just be a task instance variable?
        # (it has to be deepcopy'd below as as may be modified by task
        # instances when writing the jobfile).

        jobconfig = {
                'directives' : self.directives,
                'directive prefix' : None,
                'directive final' : None,
                'directive connector' : ' ',
                'initial scripting' : self.initial_scripting,
                'cylc environment' : deepcopy( task.cylc_env ),
                'environment scripting' : self.enviro_scripting,
                'runtime environment' : self.env_vars,
                'broadcast environment' : bcvars,
                'pre-command scripting' : self.precommand,
                'command scripting' : self.command,
                'post-command scripting' : self.postcommand,
                'namespace hierarchy' : self.namespace_hierarchy,
                'use ssh messaging' : self.ssh_messaging,
                'use manual completion' : self.manual_messaging,
                'try number' : self.try_number,
                'is cold-start' : self.is_coldstart,
                'remote cylc path' : self.__class__.remote_cylc_directory,
                'remote suite path' : self.__class__.remote_suite_directory,
                'share path' : self.__class__.job_submit_share_directory,
                'work path' : self.__class__.job_submit_work_directory,
                'job script shell' :  self.__class__.job_submission_shell,
                }
        xconfig = {
                'owner' : self.__class__.owner,
                'host' : self.__class__.remote_host,
                'log path' : self.__class__.job_submit_log_directory,
                'extra log files' : self.logfiles,
                'remote shell template' : self.__class__.remote_shell_template,
                'remote log path' : self.__class__.remote_log_directory,
                'job submission command template' : self.__class__.job_submit_command_template,
                }

        self.launcher = launcher_class( self.id, jobconfig, xconfig )

        try:
            p = self.launcher.submit( dry_run )
        except Exception, x:
            # a bug was activated in cylc job submission code
            print >> sys.stderr, 'ERROR: cylc job submission bug?'
            raise
        else:
            self.set_submitted()
            self.submission_timer_start = task.clock.get_datetime()
            return p

    def check_submission_timeout( self ):
        handler = self.__class__.event_handlers['submission timeout']
        timeout = self.__class__.timeouts['submission']
        if not handler or not timeout:
            return
        if not self.state.is_submitted() and not self.state.is_running():
            # nothing to time out yet
            return
        current_time = task.clock.get_datetime()
        if self.submission_timer_start != None and not self.state.is_running():
            cutoff = self.submission_timer_start + datetime.timedelta( minutes=timeout )
            if current_time > cutoff:
                msg = 'submitted ' + str( timeout ) + ' minutes ago, but has not started'
                self.log( 'WARNING', msg )
                RunHandler( 'submission_timeout', handler, self.__class__.suite, self.id, msg )
                self.submission_timer_start = None

    def check_execution_timeout( self ):
        handler = self.__class__.event_handlers['execution timeout']
        timeout = self.__class__.timeouts['execution']
        if not handler or not timeout:
            return
        if not self.state.is_submitted() and not self.state.is_running():
            # nothing to time out yet
            return
        current_time = task.clock.get_datetime()
        if self.execution_timer_start != None and self.state.is_running():
            cutoff = self.execution_timer_start + datetime.timedelta( minutes=timeout )
            if current_time > cutoff:
                if self.reset_timer:
                    msg = 'last message ' + str( timeout ) + ' minutes ago, but has not succeeded'
                else:
                    msg = 'started ' + str( timeout ) + ' minutes ago, but has not succeeded'
                self.log( 'WARNING', msg )
                RunHandler( 'execution_timeout', handler, self.__class__.suite, self.id, msg )
                self.execution_timer_start = None

    def sim_time_check( self ):
        if not self.state.is_running():
            return
        timeout = self.started_time_real + \
                datetime.timedelta( seconds=self.sim_mode_run_length )
        if datetime.datetime.now() > timeout:
            if self.fail_in_sim_mode:
                self.incoming( 'CRITICAL', self.id + ' failed' )
            else:
                self.incoming( 'NORMAL', self.id + ' succeeded' )
            task.state_changed = True

    def set_all_internal_outputs_completed( self ):
        if self.reject_if_failed( 'set_all_internal_outputs_completed' ):
            return
        self.log( 'DEBUG', 'setting all internal outputs completed' )
        for message in self.outputs.completed:
            if message != self.id + ' started' and \
                    message != self.id + ' succeeded' and \
                    message != self.id + ' completed':
                self.incoming( 'NORMAL', message )

    def is_complete( self ):  # not needed?
        if self.outputs.all_completed():
            return True
        else:
            return False

    def reject_if_failed( self, message ):
        if self.state.is_failed():
            if self.__class__.resurrectable:
                self.log( 'WARNING', 'message receive while failed: I am returning from the dead!' )
                return False
            else:
                self.log( 'WARNING', 'rejecting a message received while in the failed state:' )
                self.log( 'WARNING', '  ' + message )
            return True
        else:
            return False

    def incoming( self, priority, message ):
        handler = self.__class__.event_handlers['warning']
        if priority == 'WARNING' and handler:
            RunHandler( 'warning', handler, self.__class__.suite, self.id, message )

        if self.reject_if_failed( message ):
            return

        if self.reset_timer:
            self.execution_timer_start = task.clock.get_datetime()

        # receive all incoming pyro messages for this task
        self.latest_message = message
        self.latest_message_priority = priority

        # set state_changed to stimulate the task processing loop
        task.state_changed = True

        if message == self.id + ' started':
            self.set_running()

        if not self.state.is_running():
            # my external task should not be running!
            self.log( 'WARNING', "UNEXPECTED MESSAGE (task should not be running)" )
            self.log( 'WARNING', '-> ' + message )

        if message == self.id + ' failed':
            # process task failure messages
            self.succeeded_time = task.clock.get_datetime()
            try:
                # Is there a retry lined up for this task?
                self.retry_delay = float(self.retry_delays.popleft())
            except IndexError:
                # Nope, we are now failed as.
                # Add the failed method as a task output so that other
                # tasks can trigger off the failure event (failure
                # outputs are not added in advance as under normal
                # circumstances they will not be completed outputs).
                self.outputs.add( message )
                self.outputs.set_completed( message )
                # this also calls the task failure handler:
                self.set_failed( message )
                task.state_changed = True
            else:
                # Yep, we can retry.
                self.plog( 'Setting retry delay: ' + str(self.retry_delay) +  ' minutes' )
                self.retry_delay_timer_start = task.clock.get_datetime()
                self.try_number += 1
                self.state.set_status( 'retry_delayed' )
                self.prerequisites.set_all_satisfied()
                self.outputs.set_all_incomplete()
                task.state_changed = True
                handler = self.__class__.event_handlers['retry']
                if handler:
                    RunHandler( 'retry', handler, self.__class__.suite, self.id, '(task retrying)' )

        elif self.outputs.exists( message ):
            # registered output messages

            if not self.outputs.is_completed( message ):
                # message indicates completion of a registered output.
                self.log( priority,  message )
                self.outputs.set_completed( message )

                if message == self.id + ' succeeded':
                    # TASK HAS SUCCEEDED
                    self.succeeded_time = task.clock.get_datetime()
                    self.__class__.update_mean_total_elapsed_time( self.started_time, self.succeeded_time )
                    if not self.outputs.all_completed():
                        self.set_failed( 'succeeded before all outputs were completed' )
                    else:
                        self.set_succeeded_handler()
            else:
                # this output has already been satisfied
                self.log( 'WARNING', "UNEXPECTED OUTPUT (already completed):" )
                self.log( 'WARNING', "-> " + message )

        else:
            # log other (non-failed) unregistered messages with a '*' prefix
            message = '*' + message
            self.log( priority, message )

    def update( self, reqs ):
        for req in reqs.get_list():
            if req in self.prerequisites.get_list():
                # req is one of my prerequisites
                if reqs.is_satisfied(req):
                    self.prerequisites.set_satisfied( req )

    def dump_state( self, FILE ):
        # Write state information to the state dump file
        # This must be compatible with __init__() on reload
        FILE.write( self.id + ' : ' + self.state.dump() + '\n' )

    def spawn( self, state ):
        self.state.set_spawned()
        return self.__class__( self.next_tag(), state )

    def has_spawned( self ):
        # the one off task type modifier overrides this.
        return self.state.has_spawned()

    def ready_to_spawn( self ):
        # return True or False
        self.log( 'CRITICAL', 'ready_to_spawn(): OVERRIDE ME')
        sys.exit(1)

    def done( self ):
        # return True if task has succeeded and spawned
        if self.state.is_succeeded() and self.state.has_spawned():
            return True
        else:
            return False

    def check_requisites( self ):
        # overridden by repeating asynchronous tasks
        pass

    def get_state_summary( self ):
        # derived classes can call this method and then
        # add more information to the summary if necessary.

        n_total = self.outputs.count()
        n_satisfied = self.outputs.count_completed()

        summary = {}
        summary[ 'name' ] = self.name
        summary[ 'label' ] = self.tag
        summary[ 'state' ] = self.state.get_status()
        summary[ 'n_total_outputs' ] = n_total
        summary[ 'n_completed_outputs' ] = n_satisfied
        summary[ 'spawned' ] = self.state.has_spawned()
        summary[ 'latest_message' ] = self.latest_message
        summary[ 'latest_message_priority' ] = self.latest_message_priority

        if self.submitted_time:
            #summary[ 'submitted_time' ] = self.submitted_time.strftime("%Y/%m/%d %H:%M:%S" )
            summary[ 'submitted_time' ] = self.submitted_time.strftime("%H:%M:%S" )
        else:
            summary[ 'submitted_time' ] = '*'

        if self.started_time:
            #summary[ 'started_time' ] =  self.started_time.strftime("%Y/%m/%d %H:%M:%S" )
            summary[ 'started_time' ] =  self.started_time.strftime("%H:%M:%S" )
        else:
            summary[ 'started_time' ] =  '*'

        if self.succeeded_time:
            #summary[ 'succeeded_time' ] =  self.succeeded_time.strftime("%Y/%m/%d %H:%M:%S" )
            summary[ 'succeeded_time' ] =  self.succeeded_time.strftime("%H:%M:%S" )
        else:
            summary[ 'succeeded_time' ] =  '*'

        # str(timedelta) => "1 day, 23:59:55.903937" (for example)
        # to strip off fraction of seconds:
        # timedelta = re.sub( '\.\d*$', '', timedelta )

        # TO DO: the following section could probably be streamlined a bit
        if self.__class__.mean_total_elapsed_time:
            met = self.__class__.mean_total_elapsed_time
            summary[ 'mean total elapsed time' ] =  re.sub( '\.\d*$', '', str(met) )
            if self.started_time:
                if not self.succeeded_time:
                    # started but not succeeded yet, compute ETC
                    current_time = task.clock.get_datetime()
                    run_time = current_time - self.started_time
                    self.to_go = met - run_time
                    self.etc = current_time + self.to_go
                    summary[ 'Tetc' ] = self.etc.strftime( "%H:%M:%S" ) + '(' + re.sub( '\.\d*$', '', displaytd(self.to_go) ) + ')'
                elif self.etc:
                    # the first time a task finishes self.etc is not defined
                    # task succeeded; leave final prediction
                    summary[ 'Tetc' ] = self.etc.strftime( "%H:%M:%S" ) + '(' + re.sub( '\.\d*$', '', displaytd(self.to_go) ) + ')'
                else:
                    summary[ 'Tetc' ] = '*'
            else:
                # not started yet
                summary[ 'Tetc' ] = '*'
        else:
            # first instance: no mean time computed yet
            summary[ 'mean total elapsed time' ] =  '*'
            summary[ 'Tetc' ] = '*'

        summary[ 'logfiles' ] = self.logfiles.get_paths()

        return summary

    def not_fully_satisfied( self ):
        if not self.prerequisites.all_satisfied():
            return True
        if not self.suicide_prerequisites.all_satisfied():
            return True
        return False

    def satisfy_me( self, outputs ):
        self.prerequisites.satisfy_me( outputs )
        if self.suicide_prerequisites.count() > 0:
            self.suicide_prerequisites.satisfy_me( outputs )

    def adjust_tag( self, tag ):
        # Override to modify initial tag if necessary.
        return tag

    def next_tag( self ):
        # Asynchronous tasks: increment the tag by one.
        # Cycling tasks override this to compute their next valid cycle time.
        return str( int( self.tag ) + 1 )

    def is_cycling( self ):
        return False

    def is_daemon( self ):
        return False

    def is_clock_triggered( self ):
        return False

