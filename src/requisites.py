#!/usr/bin/env python

#C: THIS FILE IS PART OF THE CYLC FORECAST SUITE METASCHEDULER.
#C: Copyright (C) 2008-2011 Hilary Oliver, NIWA
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


# REQUISITES, a base class for prerequisites and outputs
# (postrequisites?). A collection of messages, each of which is
# "satisfied" or not.

class requisites(object):
    # A collection of messages, each "satisfied" or not.

    def __init__( self, owner_id ):
        self.owner_id = owner_id
        self.satisfied = {}     # self.satisfied[ "message" ] = True/False

    def count( self ):
        # how many messages are stored
        return len( self.satisfied.keys() )

    def count_satisfied( self ):
        # how many messages are stored
        n = 0
        for message in self.satisfied.keys():
            if self.satisfied[ message ]:
                n += 1
        return n

    def dump( self ):
        # return a list of strings representing each message and its state
        res = []
        for key in self.satisfied.keys():
            res.append( [ key, self.satisfied[ key ] ]  )
        return res

    def all_satisfied( self ):
        if False in self.satisfied.values(): 
            return False
        else:
            return True

    def is_satisfied( self, message ):
        if self.satisfied[ message ]:
            return True
        else:
            return False

    def set_satisfied( self, message ):
        self.satisfied[ message ] = True

    def exists( self, message ):
        if message in self.satisfied.keys():
            return True
        else:
            return False

    def set_all_unsatisfied( self ):
        for message in self.satisfied.keys():
            self.satisfied[ message ] = False

    def set_all_satisfied( self ):
        for message in self.satisfied.keys():
            self.satisfied[ message ] = True

    def get_satisfied_list( self ):
        satisfied = []
        for message in self.satisfied.keys():
            if self.satisfied[ message ]:
                satisfied.append( message )
        return satisfied

    def get_not_satisfied_list( self ):
        not_satisfied = []
        for message in self.satisfied.keys():
            if not self.satisfied[ message ]:
                not_satisfied.append( message )
        return not_satisfied

    def get_list( self ):
        return self.satisfied.keys()